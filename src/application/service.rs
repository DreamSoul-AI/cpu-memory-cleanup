use crate::{
    application::policy::Protection,
    contracts::{AppResult, Report, Target},
    system::{System, records},
};
use serde_json::{Value, json};
use std::{
    path::Path,
    sync::{
        Arc,
        atomic::{AtomicBool, Ordering},
    },
};

pub struct Application {
    pub system: Arc<dyn System>,
    busy: AtomicBool,
}
struct Guard<'a>(&'a AtomicBool);
impl Drop for Guard<'_> {
    fn drop(&mut self) {
        self.0.store(false, Ordering::Release);
    }
}
impl Default for Application {
    fn default() -> Self {
        Self::new(crate::system::native())
    }
}
impl Application {
    pub fn new(system: Arc<dyn System>) -> Self {
        Self {
            system,
            busy: AtomicBool::new(false),
        }
    }
    pub fn memory_status(&self) -> AppResult<Report> {
        let mut report = Report::new("inspect")?;
        match self.system.snapshot() {
            Ok(memory) => {
                report.sample(memory);
                report.completion = "success".into();
            }
            Err(error) => report.error("reading", error),
        }
        Ok(report)
    }
    pub fn memory_targets(&self) -> AppResult<Report> {
        let mut report = Report::new("inspect")?;
        match self.system.targets() {
            Ok(targets) => {
                report.samples.push(json!({"run_id":report.run_id,"sequence":0,"timestamp":crate::contracts::now(),"metrics":{},"processes":targets,"errors":[]}));
                report.completion = "success".into();
            }
            Err(error) => report.error("reading", error),
        }
        Ok(report)
    }
    pub fn memory_cleanup(
        &self,
        base: &Path,
        confirmed: bool,
        cancel: &AtomicBool,
    ) -> AppResult<Report> {
        self.cleanup(base, None, &[], confirmed, cancel)
    }
    pub fn memory_batch_cleanup(
        &self,
        base: &Path,
        targets: &[Target],
        protections: &[Protection],
        confirmed: bool,
        cancel: &AtomicBool,
    ) -> AppResult<Report> {
        if targets.is_empty() || targets.len() > 4096 {
            return Err("需要1..4096个明确目标".into());
        }
        let mut keys = std::collections::HashSet::new();
        for target in targets {
            if target.pid == 0 || target.created_at.is_empty() || !keys.insert(target.key()) {
                return Err("目标身份缺失或重复".into());
            }
        }
        self.cleanup(base, Some(targets), protections, confirmed, cancel)
    }
    pub fn memory_process_cleanup(
        &self,
        base: &Path,
        target: &Target,
        confirmed: bool,
        cancel: &AtomicBool,
    ) -> AppResult<Report> {
        self.memory_batch_cleanup(base, std::slice::from_ref(target), &[], confirmed, cancel)
    }
    fn cleanup(
        &self,
        base: &Path,
        targets: Option<&[Target]>,
        protections: &[Protection],
        confirmed: bool,
        cancel: &AtomicBool,
    ) -> AppResult<Report> {
        if !confirmed {
            return Err("工作集整理需要本次明确确认".into());
        }
        for rule in protections {
            rule.validate()?;
        }
        if self
            .busy
            .compare_exchange(false, true, Ordering::AcqRel, Ordering::Acquire)
            .is_err()
        {
            return Err("已有整理任务执行中".into());
        }
        let _guard = Guard(&self.busy);
        let mut timing = crate::system::measurement::Stages::default();
        let mut report = Report::new(if targets.is_some() {
            "batch"
        } else {
            "maintain"
        })?;
        report.metadata["header"] = json!({"run_id":report.run_id,"record_version":3,"program_version":crate::VERSION,"build_id":crate::BUILD_ID,"implementation":"rust","mechanism":"working-set","scope":if targets.is_some(){"process"}else{"system"},"started_at":crate::contracts::now()});
        match timing.measure("before_snapshot", || self.system.snapshot()) {
            Ok(memory) => report.sample(memory),
            Err(error) => {
                report.error("before_snapshot", error);
                report.metadata["timing"] = timing.report("failed_before_action");
                return Ok(report);
            }
        }
        report.phase = "calling".into();
        report.completion = "unknown".into();
        report.action = json!({"attempted":true,"call_outcome":"unknown","native_method":"SetProcessWorkingSetSize","intent":"one explicitly confirmed sweep"});
        if let Err(error) = timing.measure("record_intent", || records::save(base, &mut report)) {
            report.phase = "finished".into();
            report.completion = "failed".into();
            report.action = json!({"attempted":false,"call_outcome":"not_started"});
            report.error("record_before_action", error);
            report.metadata["timing"] = timing.report("failed_before_action");
            return Ok(report);
        }
        let selected = targets.map(|list| {
            list.iter()
                .filter(|target| !protections.iter().any(|rule| rule.matches(target)))
                .cloned()
                .collect::<Vec<_>>()
        });
        let result = timing.measure("system_cleanup", || {
            self.system.cleanup(selected.as_deref(), cancel)
        });
        match result {
            Ok(sweep) => {
                let outcome = if !sweep.resources_closed || sweep.unknown_count > 0 {
                    "unknown"
                } else if sweep.failed_count > 0 {
                    "failure"
                } else if sweep.attempted_count == 0 {
                    "not_started"
                } else {
                    "success"
                };
                report.completion = if outcome == "unknown" {
                    "unknown"
                } else if sweep.cancelled {
                    "cancelled"
                } else if sweep.failed_count > 0
                    || sweep.skipped_count > 0
                    || sweep.attempted_count == 0
                {
                    "partial"
                } else {
                    "success"
                }
                .into();
                if sweep.failed_count > 0 {
                    report.error(
                        "calling",
                        format!("{}个进程未完成，逐项状态见动作事实", sweep.failed_count),
                    );
                }
                if !sweep.resources_closed {
                    report.error("close", "资源关闭未确认，已停止后续派发");
                }
                if let Some(targets) = targets {
                    report.metadata["items"]=json!(targets.iter().map(|target|{
                        let protected=protections.iter().any(|rule|rule.matches(target));
                        let item=sweep.items.iter().find(|item|item["pid"]==target.pid);
                        json!({"target":target,"status":if protected {"skipped"}else{item.and_then(|v|v["outcome"].as_str()).unwrap_or("skipped")},"reason":if protected {"命中保护规则"}else{"逐项状态见原报告"},"analysis":{"working_set_before_bytes":item.and_then(|v|v.get("working_set_before_bytes")),"working_set_after_bytes":item.and_then(|v|v.get("working_set_after_bytes"))}})
                    }).collect::<Vec<_>>());
                }
                let status = sweep
                    .items
                    .iter()
                    .find(|item| item["outcome"] == "failure")
                    .map(|item| item["status"].clone())
                    .unwrap_or(json!(0));
                report.action = json!({"attempted":sweep.attempted_count>0,"call_outcome":outcome,"status":status,"privilege_restored":true,"native_method":"SetProcessWorkingSetSize","process_sweep":sweep});
            }
            Err(error) => {
                report.error("calling", error);
                report.completion = "failed".into();
                report.action = json!({"attempted":false,"call_outcome":"not_started","native_method":"SetProcessWorkingSetSize"});
            }
        }
        report.phase = "after_snapshot".into();
        if let Err(error) = timing.measure("record_action", || records::save(base, &mut report)) {
            report.error("record_action", error);
            if report.completion == "success" {
                report.completion = "partial".into();
            }
        }
        match timing.measure("after_snapshot", || self.system.snapshot()) {
            Ok(memory) => report.sample(memory),
            Err(error) => {
                report.error("after_snapshot", error);
                if report.completion == "success" {
                    report.completion = "partial".into();
                }
            }
        }
        if report.samples.len() == 2 {
            let before = report.samples[0]["metrics"]["physical"]["available_bytes"]
                .as_u64()
                .unwrap_or(0);
            let after = report.samples[1]["metrics"]["physical"]["available_bytes"]
                .as_u64()
                .unwrap_or(0);
            report.analysis = json!({"available_change_bytes":after as i128-before as i128});
        }
        timing.measure("summary", || {
            crate::system::measurement::summarize_report(&mut report)
        });
        report.metadata["timing"] = timing.report("business_before_final_record");
        report.phase = "finished".into();
        if let Err(error) = timing.measure("record_final", || records::save(base, &mut report)) {
            report.error("record", error);
            if report.completion == "success" {
                report.completion = "partial".into();
            }
        }
        let before_final = report.metadata["timing"].clone();
        report.metadata["timing"] =
            timing.report("business_through_final_record_excludes_timing_attachment");
        report.metadata["timing"]["before_final_record_ms"] = before_final["elapsed_ms"].clone();
        if let Err(error) = records::save_timing(&report) {
            report.metadata["timing_record_error"] = json!(error);
        }
        Ok(report)
    }
    pub fn memory_result(&self, directory: &Path) -> AppResult<Report> {
        records::review(directory)
    }
}

pub fn presentation(report: &Report) -> Value {
    let sweep = &report.action["process_sweep"];
    let limited = report.completion == "partial"
        && sweep["successful_count"].as_u64().unwrap_or(0) > 0
        && sweep["unknown_count"].as_u64() == Some(0)
        && sweep["resources_closed"] == true
        && report.samples.len() >= 2
        && report
            .errors
            .iter()
            .all(|error| error["phase"] == "calling");
    let success = report.completion == "success" || limited;
    let change = report.analysis["available_change_bytes"].as_f64();
    let title = if success {
        match change {
            Some(value) if value >= 0. => {
                format!("已整理 · 可用内存增加 {:.2} GiB", value / 1073741824.)
            }
            Some(value) => format!("已整理 · 可用内存减少 {:.2} GiB", -value / 1073741824.),
            None => "已整理 · 容量变化未知".into(),
        }
    } else {
        format!("本次{} · 详见动作事实", report.completion)
    };
    json!({"limited":limited,"title":title,"note":"结果来自真实前后快照，再次使用应用时占用可能回升。","scope":if report.mode=="batch" {"选中进程"}else{"系统工作集"},"context":format!("Rust {} · 本次流程{:.2}秒",crate::VERSION,report.metadata["timing"]["elapsed_ms"].as_f64().unwrap_or(0.)/1000.)})
}
pub fn brief(report: &Report) -> Value {
    let mut value = serde_json::to_value(report).expect("serializable report");
    if let Some(sweep) = value["action"]["process_sweep"].as_object_mut() {
        sweep.remove("items");
    }
    if let Some(metadata) = value["metadata"].as_object_mut() {
        metadata.remove("diagnostics");
    }
    value
}
