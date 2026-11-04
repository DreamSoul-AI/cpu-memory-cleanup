//! Linux read-only capability. Active reclaim is intentionally unavailable.
use super::System;
use crate::contracts::{AppResult, Memory, Sweep, Target};
use std::{fs, sync::atomic::AtomicBool};

pub struct Linux;
fn kb(text: &str, name: &str) -> AppResult<u64> {
    let line = text
        .lines()
        .find(|line| line.starts_with(&format!("{name}:")))
        .ok_or_else(|| format!("Missing /proc field {name}"))?;
    let mut parts = line.split_whitespace().skip(1);
    let number = parts
        .next()
        .and_then(|s| s.parse::<u64>().ok())
        .ok_or("Invalid /proc value")?;
    if parts.next() != Some("kB") {
        return Err("Unknown /proc unit".into());
    }
    number
        .checked_mul(1024)
        .ok_or_else(|| "Overflow in /proc value".into())
}
fn memory(text: &str) -> AppResult<Memory> {
    let result = Memory {
        total_bytes: kb(text, "MemTotal")?,
        available_bytes: kb(text, "MemAvailable")?,
        commit_used_bytes: kb(text, "Committed_AS")?,
        commit_limit_bytes: kb(text, "CommitLimit")?,
    };
    if result.total_bytes == 0 || result.available_bytes > result.total_bytes {
        return Err("Invalid physical capacity".into());
    }
    Ok(result)
}
fn start_ticks(stat: &str) -> AppResult<&str> {
    stat.rsplit_once(") ")
        .and_then(|(_, rest)| rest.split_whitespace().nth(19))
        .filter(|s| s.parse::<u64>().is_ok())
        .ok_or_else(|| "Invalid process stat identity".into())
}
impl System for Linux {
    fn supported(&self) -> bool {
        true
    }
    fn cleanup_supported(&self) -> bool {
        false
    }
    fn snapshot(&self) -> AppResult<Memory> {
        memory(&fs::read_to_string("/proc/meminfo").map_err(|e| e.to_string())?)
    }
    fn targets(&self) -> AppResult<Vec<Target>> {
        let boot =
            fs::read_to_string("/proc/sys/kernel/random/boot_id").map_err(|e| e.to_string())?;
        let mut targets = Vec::new();
        for entry in fs::read_dir("/proc").map_err(|e| e.to_string())? {
            let entry = entry.map_err(|e| e.to_string())?;
            let Ok(pid) = entry.file_name().to_string_lossy().parse::<u32>() else {
                continue;
            };
            let path = entry.path();
            let Ok(stat) = fs::read_to_string(path.join("stat")) else {
                continue;
            };
            let Ok(identity) = start_ticks(&stat) else {
                continue;
            };
            let Ok(status) = fs::read_to_string(path.join("status")) else {
                continue;
            };
            let Ok(second) = fs::read_to_string(path.join("stat")) else {
                continue;
            };
            if start_ticks(&second).ok() != Some(identity) {
                continue;
            }
            targets.push(Target {
                pid,
                created_at: format!("{}:{identity}", boot.trim()),
                name: status
                    .lines()
                    .find_map(|s| s.strip_prefix("Name:"))
                    .unwrap_or("")
                    .trim()
                    .into(),
                image_path: fs::read_link(path.join("exe"))
                    .ok()
                    .map(|p| p.to_string_lossy().into_owned()),
                working_set_bytes: kb(&status, "VmRSS").ok(),
            });
        }
        targets.sort_by_key(|t| t.pid);
        Ok(targets)
    }
    fn cleanup(&self, _: Option<&[Target]>, _: &AtomicBool) -> AppResult<Sweep> {
        Err("Linux当前仅支持真实读取，未派发主动整理".into())
    }
}
#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn validates_physical_and_commit_fields_without_filling_unknowns() {
        let text =
            "MemTotal: 100 kB\nMemAvailable: 40 kB\nCommitted_AS: 90 kB\nCommitLimit: 80 kB\n";
        let m = memory(text).unwrap();
        assert_eq!(m.available_bytes, 40960);
        assert!(m.commit_used_bytes > m.commit_limit_bytes);
        assert!(memory(&text.replace("MemAvailable", "unknown")).is_err());
        assert!(memory(&text.replace("40 kB", "110 kB")).is_err());
    }
    #[test]
    fn identity_handles_spaces_and_parentheses_in_process_name() {
        let stat = format!("12 (app ) name) S {} 1234 0", vec!["0"; 18].join(" "));
        assert_eq!(start_ticks(&stat).unwrap(), "1234");
        assert!(start_ticks("bad").is_err());
        assert!(!Linux.cleanup_supported());
    }
}
