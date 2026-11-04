use serde::{Deserialize, Serialize};
use serde_json::{Value, json};

pub type AppResult<T> = Result<T, String>;

#[cfg(feature = "experimental-quota")]
#[derive(Clone, Copy, Debug, PartialEq, Eq, PartialOrd, Ord, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum ComparisonMethod {
    Verified,
    Quota,
}
#[cfg(feature = "experimental-quota")]
pub fn validate_comparison_pids(pids: &[u32]) -> AppResult<()> {
    if pids.is_empty()
        || pids.len() > 4096
        || pids.contains(&0)
        || pids.iter().collect::<std::collections::BTreeSet<_>>().len() != pids.len()
    {
        return Err("对照需要1..4096个不重复的非零PID".into());
    }
    Ok(())
}

pub fn now() -> String {
    time::OffsetDateTime::now_utc()
        .format(&time::format_description::well_known::Rfc3339)
        .expect("UTC date")
}

pub fn identifier() -> AppResult<String> {
    let mut bytes = [0u8; 16];
    crate::system::random_bytes(&mut bytes)?;
    Ok(bytes.iter().map(|b| format!("{b:02x}")).collect())
}

#[derive(Clone, Copy, Debug, Default, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum AvailableKind {
    #[default]
    OsAvailable,
    FreeAndInactiveEstimate,
}
#[derive(Clone, Debug, Default, Serialize, Deserialize)]
pub struct Memory {
    pub total_bytes: u64,
    pub available_bytes: u64,
    pub commit_used_bytes: Option<u64>,
    pub commit_limit_bytes: Option<u64>,
    #[serde(default)]
    pub available_kind: AvailableKind,
    #[serde(default)]
    pub compressed_bytes: Option<u64>,
}
impl Memory {
    pub fn metrics(&self) -> Value {
        let status = match (self.commit_used_bytes, self.commit_limit_bytes) {
            (Some(_), Some(_)) => "measured",
            (None, None) => "unavailable",
            _ => "partial",
        };
        json!({"physical":{"total_bytes":self.total_bytes,"available_bytes":self.available_bytes,
            "available_kind":self.available_kind,"compressed_bytes":self.compressed_bytes},
            "commit":{"used_bytes":self.commit_used_bytes,"limit_bytes":self.commit_limit_bytes,"status":status}})
    }
}

#[derive(Clone, Debug, Serialize, Deserialize, PartialEq)]
#[serde(deny_unknown_fields)]
pub struct Target {
    pub pid: u32,
    pub created_at: String,
    #[serde(default)]
    pub name: String,
    #[serde(default)]
    pub image_path: Option<String>,
    #[serde(default)]
    pub working_set_bytes: Option<u64>,
}
impl Target {
    pub fn key(&self) -> String {
        format!("{}:{}", self.pid, self.created_at)
    }
}

#[derive(Clone, Debug, Default, Serialize, Deserialize)]
pub struct Sweep {
    pub enumerated_count: usize,
    pub visited_count: usize,
    pub attempted_count: usize,
    pub successful_count: usize,
    pub failed_count: usize,
    pub unknown_count: usize,
    pub skipped_count: usize,
    pub resources_closed: bool,
    pub cancelled: bool,
    pub native_call_ms: f64,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub timing: Option<Value>,
    pub items: Vec<Value>,
    pub omitted_items_count: usize,
}
impl Sweep {
    pub fn push(&mut self, value: Value) {
        if self.items.len() < 1024 {
            self.items.push(value);
        } else {
            self.omitted_items_count += 1;
        }
    }
}

#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct Report {
    pub run_id: String,
    pub mode: String,
    pub completion: String,
    pub phase: String,
    pub samples: Vec<Value>,
    pub analysis: Value,
    pub action: Value,
    pub errors: Vec<Value>,
    pub metadata: Value,
    pub output_dir: Option<String>,
    #[serde(flatten)]
    pub extra: serde_json::Map<String, Value>,
}
impl Report {
    pub fn new(mode: &str) -> AppResult<Self> {
        Ok(Self {
            run_id: identifier()?,
            mode: mode.into(),
            completion: "failed".into(),
            phase: "finished".into(),
            samples: Vec::new(),
            analysis: json!({}),
            action: Value::Null,
            errors: Vec::new(),
            metadata: json!({"implementation":"rust","program_version":crate::VERSION,"build_id":crate::BUILD_ID}),
            output_dir: None,
            extra: Default::default(),
        })
    }
    pub fn sample(&mut self, memory: Memory) {
        self.samples.push(
            json!({"run_id":self.run_id,"sequence":self.samples.len(),"timestamp":now(),
            "metrics":memory.metrics(),"processes":[],"errors":[]}),
        );
    }
    pub fn error(&mut self, phase: &str, message: impl Into<String>) {
        self.errors
            .push(json!({"phase":phase,"type":"operation_failed","message":message.into()}));
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn optional_metrics_keep_null_and_known_commit_keeps_numeric_shape() {
        let mut value = Memory {
            total_bytes: 100,
            available_bytes: 50,
            ..Memory::default()
        };
        assert!(value.metrics()["commit"]["used_bytes"].is_null());
        assert_eq!(value.metrics()["commit"]["status"], "unavailable");
        value.commit_used_bytes = Some(0);
        value.commit_limit_bytes = Some(200);
        assert_eq!(value.metrics()["commit"]["used_bytes"], 0);
        assert_eq!(value.metrics()["commit"]["status"], "measured");
    }
}
