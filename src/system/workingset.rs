//! One serial sweep, with platform calls isolated behind owned process handles.
use super::measurement::{Stages, native_summary};
use crate::contracts::{AppResult, Sweep, Target};
use serde_json::json;
use std::sync::atomic::{AtomicBool, Ordering};

#[derive(Clone, Copy, Debug, PartialEq)]
pub(crate) enum Access {
    #[cfg(windows)]
    Query,
    VerifiedCleanup,
    #[cfg(any(feature = "experimental-quota", test))]
    QuotaOnlyCleanup,
}

#[derive(Clone, Copy, Debug, PartialEq)]
pub(crate) enum Strategy {
    Verified,
    #[cfg(any(feature = "experimental-quota", test))]
    QuotaOnlyGlobal,
}
impl Strategy {
    fn access(self) -> Access {
        match self {
            Self::Verified => Access::VerifiedCleanup,
            #[cfg(any(feature = "experimental-quota", test))]
            Self::QuotaOnlyGlobal => Access::QuotaOnlyCleanup,
        }
    }
    fn name(self) -> &'static str {
        match self {
            Self::Verified => "verified_serial",
            #[cfg(any(feature = "experimental-quota", test))]
            Self::QuotaOnlyGlobal => "quota_only_global_serial",
        }
    }
}

pub(crate) struct NativeCall {
    pub succeeded: bool,
    pub status: u32,
    pub elapsed_ms: f64,
}
pub(crate) trait Process {
    fn identity(&self) -> AppResult<String>;
    fn path(&self) -> Option<String>;
    fn working_set(&self) -> Option<u64>;
    fn trim(&self) -> NativeCall;
    fn close(self) -> AppResult<()>;
}
pub(crate) trait Processes {
    type Handle: Process;
    fn enumerate(&self) -> AppResult<Vec<u32>>;
    fn open(&self, pid: u32, access: Access) -> Result<Self::Handle, u32>;
}

pub(crate) fn sweep(
    api: &impl Processes,
    selected: Option<&[Target]>,
    strategy: Strategy,
    cancel: &AtomicBool,
) -> AppResult<Sweep> {
    // A candidate can never weaken the identity contract of explicit targets.
    let strategy = if selected.is_some() {
        Strategy::Verified
    } else {
        strategy
    };
    let mut timing = Stages::default();
    let mut native_samples = Vec::new();
    let ids = timing.measure("enumeration", || match selected {
        Some(targets) => Ok(targets.iter().map(|t| t.pid).collect::<Vec<_>>()),
        None => api.enumerate(),
    })?;
    let mut sweep = Sweep {
        enumerated_count: ids.len(),
        resources_closed: true,
        ..Default::default()
    };
    for pid in ids {
        if cancel.load(Ordering::Acquire) {
            sweep.cancelled = true;
            break;
        }
        sweep.visited_count += 1;
        let handle = match timing.measure("open", || api.open(pid, strategy.access())) {
            Ok(handle) => handle,
            Err(status) => {
                sweep.skipped_count += 1;
                sweep.push(json!({"pid":pid,"outcome":"skipped","status":status}));
                continue;
            }
        };
        let identity = if strategy == Strategy::Verified {
            Some(timing.measure("identity", || handle.identity()))
        } else {
            None
        };
        let target = selected.and_then(|targets| targets.iter().find(|t| t.pid == pid));
        let mismatch = timing.measure("path_verification", || match &identity {
            Some(Ok(created)) => target.is_some_and(|t| {
                t.created_at != *created
                    || t.image_path.as_ref().is_some_and(|p| {
                        handle
                            .path()
                            .is_none_or(|current| normalize(&current) != normalize(p))
                    })
            }),
            Some(Err(_)) => true,
            None => false,
        });
        if mismatch {
            sweep.skipped_count += 1;
            sweep.push(json!({"pid":pid,"outcome":"skipped","reason":"目标身份或路径未确认"}));
        } else if cancel.load(Ordering::Acquire) {
            sweep.cancelled = true;
        } else {
            sweep.attempted_count += 1;
            let before = selected
                .and_then(|_| timing.measure("working_set_verification", || handle.working_set()));
            let call = handle.trim();
            timing.add("native", call.elapsed_ms);
            sweep.native_call_ms += call.elapsed_ms;
            native_samples.push((pid, call.elapsed_ms));
            if call.succeeded {
                sweep.successful_count += 1;
            } else {
                sweep.failed_count += 1;
            }
            let after = selected
                .and_then(|_| timing.measure("working_set_verification", || handle.working_set()));
            sweep.push(json!({"pid":pid,"created_at":identity.and_then(Result::ok),
                "outcome":if call.succeeded {"success"} else {"failure"},"status":call.status,
                "native_call_ms":call.elapsed_ms,"working_set_before_bytes":before,"working_set_after_bytes":after}));
        }
        if let Err(error) = timing.measure("close", || handle.close()) {
            sweep.resources_closed = false;
            sweep.unknown_count += 1;
            sweep.push(json!({"pid":pid,"close_error":error,"outcome":"unknown"}));
            break;
        }
        if sweep.cancelled {
            break;
        }
    }
    let mut measured = timing.report("serial_system_sweep");
    measured["native_summary"] = native_summary(&native_samples, sweep.attempted_count);
    measured["strategy"] = json!(strategy.name());
    measured["identity_scope"] = json!(if strategy == Strategy::Verified {
        "creation_time_verified"
    } else {
        "opened_handle_only"
    });
    sweep.timing = Some(measured);
    Ok(sweep)
}

pub(crate) fn normalize(path: &str) -> String {
    path.replace('\\', "/").to_lowercase()
}

#[cfg(test)]
#[path = "../../tests/system/workingset.rs"]
mod tests;
