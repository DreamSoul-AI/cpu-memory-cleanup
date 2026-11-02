use crate::contracts::{AppResult, Target};
use serde::{Deserialize, Serialize};
use std::time::{Duration, Instant};

#[derive(Clone, Debug, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Protection {
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub image_path: Option<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub pid: Option<u32>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub created_at: Option<String>,
}
impl Protection {
    pub fn validate(&self) -> AppResult<()> {
        match (&self.image_path, self.pid, &self.created_at) {
            (Some(path), None, None) if !path.trim().is_empty() => Ok(()),
            (None, Some(pid), Some(created)) if pid > 0 && !created.is_empty() => Ok(()),
            _ => Err("保护项需要完整路径或PID/创建身份".into()),
        }
    }
    pub fn matches(&self, target: &Target) -> bool {
        if let Some(path) = &self.image_path {
            target
                .image_path
                .as_ref()
                .is_none_or(|current| normalize(current) == normalize(path))
        } else {
            self.pid == Some(target.pid) && self.created_at.as_ref() == Some(&target.created_at)
        }
    }
}
fn normalize(path: &str) -> String {
    path.replace('\\', "/").to_lowercase()
}

#[derive(Clone, Debug, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Rule {
    pub mode: String,
    pub interval_s: u64,
    pub available_pct: f64,
    pub sustain_s: u64,
    pub cooldown_s: u64,
}
impl Rule {
    pub fn validate(&self) -> AppResult<()> {
        if !matches!(self.mode.as_str(), "interval" | "pressure")
            || !(1..=86400).contains(&self.interval_s)
            || !(1..=86400).contains(&self.sustain_s)
            || !(1..=86400).contains(&self.cooldown_s)
            || !self.available_pct.is_finite()
            || !(0.1..=100.).contains(&self.available_pct)
        {
            Err("自动规则参数超出范围".into())
        } else {
            Ok(())
        }
    }
}
#[derive(Clone, Debug, Serialize, Deserialize)]
#[serde(default, deny_unknown_fields)]
pub struct Settings {
    pub schema_version: u32,
    pub protections: Vec<Protection>,
    pub descending: bool,
    pub resident: bool,
    pub geometry: String,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub rule: Option<Rule>,
}
impl Default for Settings {
    fn default() -> Self {
        Self {
            schema_version: 1,
            protections: Vec::new(),
            descending: true,
            resident: false,
            geometry: String::new(),
            rule: None,
        }
    }
}
impl Settings {
    pub fn validate(&self) -> AppResult<()> {
        if self.schema_version != 1 || self.protections.len() > 4096 || self.geometry.len() > 256 {
            return Err("设置版本或规模无效".into());
        }
        for protection in &self.protections {
            protection.validate()?;
        }
        if let Some(rule) = &self.rule {
            rule.validate()?;
        }
        Ok(())
    }
}
pub struct Policy {
    pub rule: Option<Rule>,
    pub targets: Vec<Target>,
    pub protections: Vec<Protection>,
    pub armed: bool,
    pub last_job: Option<String>,
    pub error: Option<String>,
    next: Instant,
    low_since: Option<Instant>,
}
impl Default for Policy {
    fn default() -> Self {
        Self {
            rule: None,
            targets: Vec::new(),
            protections: Vec::new(),
            armed: false,
            last_job: None,
            error: None,
            next: Instant::now(),
            low_since: None,
        }
    }
}
impl Policy {
    pub fn arm(
        &mut self,
        rule: Rule,
        targets: Vec<Target>,
        protections: Vec<Protection>,
        now: Instant,
    ) -> AppResult<()> {
        rule.validate()?;
        if targets.is_empty() {
            return Err("启用规则必须选择身份快照".into());
        }
        self.next = now
            + Duration::from_secs(if rule.mode == "interval" {
                rule.interval_s
            } else {
                0
            });
        self.rule = Some(rule);
        self.targets = targets;
        self.protections = protections;
        self.armed = true;
        self.error = None;
        self.low_since = None;
        Ok(())
    }
    pub fn due(&mut self, now: Instant, available_pct: Option<f64>) -> bool {
        if !self.armed {
            return false;
        }
        let Some(rule) = &self.rule else { return false };
        if rule.mode == "interval" {
            return now >= self.next;
        }
        if available_pct.is_some_and(|value| value < rule.available_pct) {
            let since = *self.low_since.get_or_insert(now);
            now >= self.next && now.duration_since(since) >= Duration::from_secs(rule.sustain_s)
        } else {
            self.low_since = None;
            false
        }
    }
    pub fn dispatched(&mut self, now: Instant) {
        if let Some(rule) = &self.rule {
            self.next = now
                + Duration::from_secs(rule.cooldown_s.max(if rule.mode == "interval" {
                    rule.interval_s
                } else {
                    0
                }));
        }
        self.low_since = None;
    }
}
