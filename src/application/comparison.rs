//! Opt-in developer evidence collection. No automatic rounds or winner selection.
use super::service::Application;
pub use crate::contracts::ComparisonMethod as Method;
pub use crate::contracts::validate_comparison_pids as validate_pids;
use crate::contracts::{AppResult, Report, Target};
use serde::{Deserialize, Serialize};
use serde_json::{Value, json};
use std::{
    collections::BTreeSet,
    fs::{self, OpenOptions},
    hint::black_box,
    io::Write,
    path::{Path, PathBuf},
    sync::atomic::AtomicBool,
    time::Instant,
};

impl Method {
    pub fn name(self) -> &'static str {
        match self {
            Self::Verified => "verified",
            Self::Quota => "quota",
        }
    }
    fn strategy(self) -> &'static str {
        match self {
            Self::Verified => "verified_serial",
            Self::Quota => "quota_only_global_serial",
        }
    }
}
#[derive(Clone, Copy, Debug, PartialEq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum Scope {
    Owned,
    GlobalFixed,
}
#[derive(Clone, Debug, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Protocol {
    pub schema_version: u32,
    pub session_id: String,
    pub scope: Scope,
    pub pids: Vec<u32>,
    pub load_bytes: usize,
    pub owner_pid: u32,
    pub program_version: String,
}
#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct LoadReading {
    pub bytes: usize,
    pub content_valid: bool,
    pub read_ms: f64,
}
pub struct ResidentLoad(Vec<u8>);
impl ResidentLoad {
    pub fn new(bytes: usize) -> AppResult<Self> {
        if !(4096..=256 * 1024 * 1024).contains(&bytes) {
            return Err("自有负载预算为4KiB至256MiB".into());
        }
        let mut memory = Vec::new();
        memory.try_reserve_exact(bytes).map_err(|e| e.to_string())?;
        memory.resize(bytes, 0x5a);
        Ok(Self(memory))
    }
    pub fn restore(&mut self) -> LoadReading {
        self.0.fill(0x5a);
        self.read()
    }
    pub fn read(&self) -> LoadReading {
        let started = Instant::now();
        // Read every byte even after a mismatch. This is a measured recovery cost.
        let invalid = self
            .0
            .iter()
            .fold(0u8, |acc, byte| acc | (black_box(*byte) ^ 0x5a));
        LoadReading {
            bytes: self.0.len(),
            content_valid: invalid == 0,
            read_ms: started.elapsed().as_secs_f64() * 1000.,
        }
    }
}
#[derive(Clone, Debug, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Evidence {
    pub protocol: Protocol,
    pub method: Method,
    pub order: usize,
    pub before: Vec<Target>,
    pub after: Vec<Target>,
    pub load_before: LoadReading,
    pub load_after: LoadReading,
    pub action_return_to_readback_ms: f64,
    pub report: Option<Report>,
    pub errors: Vec<String>,
}

fn validate(protocol: &Protocol) -> AppResult<()> {
    validate_pids(&protocol.pids)?;
    if protocol.schema_version != 1
        || protocol.session_id.is_empty()
        || protocol.program_version.is_empty()
        || !(4096..=256 * 1024 * 1024).contains(&protocol.load_bytes)
        || !protocol.pids.contains(&protocol.owner_pid)
        || (protocol.scope == Scope::Owned && protocol.pids != [protocol.owner_pid])
    {
        return Err("对照协议版本、范围或负载无效".into());
    }
    Ok(())
}
fn create_json(path: &Path, value: &impl Serialize) -> AppResult<()> {
    let data = serde_json::to_vec_pretty(value).map_err(|e| e.to_string())?;
    let mut file = OpenOptions::new()
        .write(true)
        .create_new(true)
        .open(path)
        .map_err(|e| e.to_string())?;
    file.write_all(&data)
        .and_then(|_| file.sync_all())
        .map_err(|e| e.to_string())
}
pub struct Session {
    protocol: Protocol,
    directory: PathBuf,
    consumed: BTreeSet<Method>,
}
impl Session {
    /// New directory only. No system access or workload allocation here.
    pub fn create(
        directory: &Path,
        scope: Scope,
        pids: Vec<u32>,
        load_bytes: usize,
    ) -> AppResult<Self> {
        let protocol = Protocol {
            schema_version: 1,
            session_id: crate::contracts::identifier()?,
            scope,
            pids,
            load_bytes,
            owner_pid: std::process::id(),
            program_version: crate::VERSION.into(),
        };
        validate(&protocol)?;
        fs::create_dir(directory).map_err(|e| e.to_string())?;
        create_json(&directory.join("session.json"), &protocol)?;
        Ok(Self {
            protocol,
            directory: directory.into(),
            consumed: BTreeSet::new(),
        })
    }
    /// Explicit confirmation is required per method. A consumed method is never retried,
    /// including when preparation, native maintenance or evidence persistence fails.
    pub fn run_once(
        &mut self,
        method: Method,
        confirmed: bool,
        app: &Application,
        load: &mut ResidentLoad,
        observe: impl Fn() -> AppResult<Vec<Target>>,
    ) -> AppResult<Evidence> {
        if !confirmed {
            return Err("每种方法需要本次单独确认".into());
        }
        if load.0.len() != self.protocol.load_bytes {
            return Err("负载预算与会话不符".into());
        }
        let order = self.consumed.len();
        if !self.consumed.insert(method) {
            return Err("本会话该方法已消耗，不重试".into());
        }
        create_json(
            &self
                .directory
                .join(format!("{}.started.json", method.name())),
            &json!({"session_id":self.protocol.session_id,"method":method,"order":order,
                "timestamp":crate::contracts::now(),"state":"consumed_before_preparation"}),
        )?;
        let load_before = load.restore();
        let before = observe()?;
        create_json(
            &self
                .directory
                .join(format!("{}.before.json", method.name())),
            &json!({"session_id":self.protocol.session_id,"method":method,"load":load_before,"targets":before}),
        )?;
        let mut errors = Vec::new();
        let report =
            match app.memory_cleanup(&self.directory.join("runs"), true, &AtomicBool::new(false)) {
                Ok(report) => Some(report),
                Err(error) => {
                    errors.push(error);
                    None
                }
            };
        // Observation and report bookkeeping can themselves refault pages. Record
        // that delay instead of presenting readback as immediate syscall latency.
        let returned = Instant::now();
        let after = match observe() {
            Ok(value) => value,
            Err(error) => {
                errors.push(error);
                Vec::new()
            }
        };
        let action_return_to_readback_ms = returned.elapsed().as_secs_f64() * 1000.;
        let load_after = load.read();
        let evidence = Evidence {
            protocol: self.protocol.clone(),
            method,
            order,
            before,
            after,
            load_before,
            load_after,
            action_return_to_readback_ms,
            report,
            errors,
        };
        create_json(
            &self.directory.join(format!("{}.json", method.name())),
            &evidence,
        )?;
        Ok(evidence)
    }
}

/// File parsing is bounded and read only. It never constructs a System or Application.
pub fn read_evidence(path: &Path) -> AppResult<Evidence> {
    if fs::metadata(path).map_err(|e| e.to_string())?.len() > 16 * 1024 * 1024 {
        return Err("对照证据超过16MiB".into());
    }
    let value: Evidence = serde_json::from_slice(&fs::read(path).map_err(|e| e.to_string())?)
        .map_err(|e| e.to_string())?;
    validate(&value.protocol)?;
    Ok(value)
}
fn metrics(value: &Evidence) -> Value {
    let report = value.report.as_ref();
    json!({"method":value.method,"order":value.order,"run_id":report.map(|r|&r.run_id),
        "completion":report.map(|r|&r.completion),
        "native_sum_ms":report.map(|r|&r.action["process_sweep"]["native_call_ms"]),
        "system_wall_ms":report.map(|r|&r.action["process_sweep"]["timing"]["elapsed_ms"]),
        "business_wall_ms":report.map(|r|&r.metadata["timing"]["elapsed_ms"]),
        "coverage":report.map(|r|json!({"enumerated":r.action["process_sweep"]["enumerated_count"],
            "attempted":r.action["process_sweep"]["attempted_count"],"successful":r.action["process_sweep"]["successful_count"],
            "failed":r.action["process_sweep"]["failed_count"],"skipped":r.action["process_sweep"]["skipped_count"],
            "unknown":r.action["process_sweep"]["unknown_count"]})),
        "available_change_bytes":report.map(|r|&r.analysis["available_change_bytes"]),
        "load_control_read_ms":value.load_before.read_ms,"load_readback_ms":value.load_after.read_ms,
        "action_return_to_readback_ms":value.action_return_to_readback_ms,
        "load_content_valid":value.load_after.content_valid})
}
pub fn compare(a: &Evidence, b: &Evidence) -> AppResult<Value> {
    validate(&a.protocol)?;
    validate(&b.protocol)?;
    let (left, right) = if a.method == Method::Verified && b.method == Method::Quota {
        (a, b)
    } else if b.method == Method::Verified && a.method == Method::Quota {
        (b, a)
    } else {
        return Err("需要默认和候选各一份证据".into());
    };
    let mut issues = Vec::<String>::new();
    if serde_json::to_value(&left.protocol).unwrap()
        != serde_json::to_value(&right.protocol).unwrap()
    {
        issues.push("会话、范围、固定清单、版本或负载不同".into());
    }
    if left.order == right.order || left.order > 1 || right.order > 1 {
        issues.push("方法执行顺序无效".into());
    }
    if left
        .report
        .as_ref()
        .zip(right.report.as_ref())
        .is_some_and(|(a, b)| a.run_id == b.run_id)
    {
        issues.push("两方法不能引用同一源运行".into());
    }
    for evidence in [left, right] {
        let Some(report) = &evidence.report else {
            issues.push("缺少动作报告".into());
            continue;
        };
        let sweep = &report.action["process_sweep"];
        if report.phase != "finished"
            || report.completion != "success"
            || !evidence.errors.is_empty()
            || !report.errors.is_empty()
            || report.metadata.get("timing_record_error").is_some()
            || sweep["resources_closed"] != true
            || sweep["unknown_count"] != 0
            || sweep["cancelled"] != false
        {
            issues.push(format!("{}结果或收尾不完整", evidence.method.name()));
        }
        if sweep["timing"]["strategy"] != evidence.method.strategy() {
            issues.push("装配方法与报告不符".into());
        }
        if sweep["enumerated_count"].as_u64() != Some(evidence.protocol.pids.len() as u64)
            || sweep["attempted_count"] != sweep["enumerated_count"]
            || sweep["omitted_items_count"] != 0
        {
            issues.push("目标覆盖不完整或明细被截断".into());
        }
        let actual: BTreeSet<_> = sweep["items"]
            .as_array()
            .into_iter()
            .flatten()
            .filter_map(|i| i["pid"].as_u64())
            .collect();
        if actual
            != evidence
                .protocol
                .pids
                .iter()
                .map(|&p| u64::from(p))
                .collect()
        {
            issues.push("实际调用清单与固定清单不同".into());
        }
        if [&evidence.load_before, &evidence.load_after]
            .iter()
            .any(|r| {
                !r.content_valid
                    || r.bytes != evidence.protocol.load_bytes
                    || !r.read_ms.is_finite()
                    || r.read_ms < 0.
            })
        {
            issues.push("自有负载内容或预算未通过核验".into());
        }
        for value in [
            &report.metadata["timing"]["elapsed_ms"],
            &sweep["native_call_ms"],
            &sweep["timing"]["elapsed_ms"],
        ] {
            if !value.as_f64().is_some_and(|n| n.is_finite() && n >= 0.) {
                issues.push("业务、系统或原生计时缺失或无效".into());
            }
        }
    }
    // Pre/post observations are external brackets, not atomic handle identity proof.
    let observations = [&left.before, &left.after, &right.before, &right.after];
    for &pid in &left.protocol.pids {
        let matches: Vec<_> = observations
            .iter()
            .map(|list| list.iter().find(|t| t.pid == pid))
            .collect();
        let identity = matches[0].map(|t| t.created_at.as_str());
        if identity.is_none_or(str::is_empty)
            || matches
                .iter()
                .any(|t| t.map(|t| t.created_at.as_str()) != identity)
        {
            issues.push(format!("PID {pid}身份观测缺失或发生变化"));
        }
    }
    let working_set = |e: &Evidence| -> Option<u64> {
        e.protocol.pids.iter().try_fold(0u64, |sum, pid| {
            sum.checked_add(e.before.iter().find(|t| t.pid == *pid)?.working_set_bytes?)
        })
    };
    let starts = (working_set(left), working_set(right));
    let difference = match starts {
        (Some(a), Some(b)) if a.max(b) > 0 => Some(a.abs_diff(b) as f64 / a.max(b) as f64 * 100.),
        _ => None,
    };
    if difference.is_none_or(|p| p > 5.) {
        issues.push("前置工作集总量未知或差异超过预设5%探索容差".into());
    }
    issues.sort();
    issues.dedup();
    Ok(
        json!({"schema_version":1,"scope":left.protocol.scope,"observations":[metrics(left),metrics(right)],
        "starting_working_set_bytes":[starts.0,starts.1],"starting_difference_percent":difference,
        "issues":issues,"winner":null,"conclusion":"单对探索，不构成性能排名或默认替换依据",
        "limitations":["固定PID与前后身份观测不证明候选句柄原子身份或其他应用负载已恢复",
            "5%为探索判读容差，不是效果标准；总量相近不保证每个目标状态相同",
            "读回耗时单列，系统可用量差额不等于独占释放量",
            "自有负载不能代表全局长尾，顺序未平衡且无重复不能认定更快"]}),
    )
}

#[cfg(test)]
#[path = "../../tests/application/comparison.rs"]
mod tests;
