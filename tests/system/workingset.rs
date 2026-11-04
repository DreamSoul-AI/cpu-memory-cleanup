// integration/content, p1, c1: owned-handle orchestration, all platform calls isolated.
// The trace identifies the affected boundary. No Win32 process is opened or trimmed.
use super::*;
use std::{cell::RefCell, rc::Rc};

struct Isolated<'a> {
    trace: Rc<RefCell<Vec<String>>>,
    cancel: &'a AtomicBool,
    cancel_at: Option<&'static str>,
    open_failure: Option<u32>,
    identity_failure: Option<u32>,
    native_failure: Option<u32>,
    close_failure: Option<u32>,
}
impl<'a> Isolated<'a> {
    fn new(cancel: &'a AtomicBool) -> Self {
        Self {
            trace: Default::default(),
            cancel,
            cancel_at: None,
            open_failure: None,
            identity_failure: None,
            native_failure: None,
            close_failure: None,
        }
    }
    fn event(&self, operation: &str, pid: u32) {
        self.trace.borrow_mut().push(format!("{operation}:{pid}"));
        if self.cancel_at == Some(operation) {
            self.cancel.store(true, Ordering::Release);
        }
    }
}
struct Handle<'a, 'b> {
    api: &'a Isolated<'b>,
    pid: u32,
    closed: bool,
}
impl Drop for Handle<'_, '_> {
    fn drop(&mut self) {
        if !self.closed {
            self.api.event("unclosed_drop", self.pid);
        }
    }
}
impl Process for Handle<'_, '_> {
    fn identity(&self) -> AppResult<String> {
        self.api.event("identity", self.pid);
        if self.api.identity_failure == Some(self.pid) {
            Err("identity unavailable".into())
        } else {
            Ok(format!("created{}", self.pid))
        }
    }
    fn path(&self) -> Option<String> {
        self.api.event("path", self.pid);
        Some("C:\\Apps\\example.exe".into())
    }
    fn working_set(&self) -> Option<u64> {
        self.api.event("working_set", self.pid);
        Some(100)
    }
    fn trim(&self) -> NativeCall {
        self.api.event("trim", self.pid);
        let succeeded = self.api.native_failure != Some(self.pid);
        NativeCall {
            succeeded,
            status: if succeeded { 0 } else { 5 },
            // This is an isolated return value, never a host speed measurement.
            elapsed_ms: 0.,
        }
    }
    fn close(mut self) -> AppResult<()> {
        self.api.event("close", self.pid);
        self.closed = true;
        if self.api.close_failure == Some(self.pid) {
            Err("close unknown".into())
        } else {
            Ok(())
        }
    }
}
// The sweep borrows the API for the lifetime of each owned handle.
impl<'a, 'b> Processes for &'a Isolated<'b> {
    type Handle = Handle<'a, 'b>;
    fn enumerate(&self) -> AppResult<Vec<u32>> {
        self.trace.borrow_mut().push("enumerate".into());
        Ok(vec![1, 2])
    }
    fn open(&self, pid: u32, access: Access) -> Result<Self::Handle, u32> {
        self.event(&format!("open_{access:?}"), pid);
        if self.open_failure == Some(pid) {
            Err(5)
        } else {
            Ok(Handle {
                api: self,
                pid,
                closed: false,
            })
        }
    }
}
fn target(pid: u32) -> Target {
    Target {
        pid,
        created_at: format!("created{pid}"),
        name: String::new(),
        image_path: Some("c:/apps/EXAMPLE.exe".into()),
        working_set_bytes: None,
    }
}

#[test]
fn quota_global_is_serial_and_never_queries_identity_path_or_working_set() {
    let cancel = AtomicBool::new(false);
    let api = Isolated::new(&cancel);
    let result = sweep(&&api, None, Strategy::QuotaOnlyGlobal, &cancel).unwrap();
    assert_eq!(
        *api.trace.borrow(),
        [
            "enumerate",
            "open_QuotaOnlyCleanup:1",
            "trim:1",
            "close:1",
            "open_QuotaOnlyCleanup:2",
            "trim:2",
            "close:2"
        ]
    );
    assert_eq!(result.successful_count, 2);
    assert_eq!(result.attempted_count, 2);
    assert!(result.resources_closed);
    for item in &result.items {
        assert!(item["created_at"].is_null());
        assert!(item["working_set_before_bytes"].is_null());
    }
    let timing = result.timing.unwrap();
    assert_eq!(timing["strategy"], "quota_only_global_serial");
    assert_eq!(timing["identity_scope"], "opened_handle_only");
    assert_eq!(timing["native_summary"]["sample_count"], 2);
    assert!(timing["stages_ms"].get("identity").is_none());
}

#[test]
fn default_global_still_requires_identity_before_every_call() {
    let cancel = AtomicBool::new(false);
    let mut api = Isolated::new(&cancel);
    api.identity_failure = Some(2);
    let result = sweep(&&api, None, Strategy::Verified, &cancel).unwrap();
    assert_eq!(
        *api.trace.borrow(),
        [
            "enumerate",
            "open_VerifiedCleanup:1",
            "identity:1",
            "trim:1",
            "close:1",
            "open_VerifiedCleanup:2",
            "identity:2",
            "close:2"
        ]
    );
    assert_eq!(result.attempted_count, 1);
    assert_eq!(result.skipped_count, 1);
    assert_eq!(result.items[0]["created_at"], "created1");
    assert_eq!(result.timing.unwrap()["strategy"], "verified_serial");
}

#[test]
fn candidate_targets_keep_verified_access_identity_path_and_snapshots() {
    let cancel = AtomicBool::new(false);
    let api = Isolated::new(&cancel);
    let result = sweep(
        &&api,
        Some(&[target(1)]),
        Strategy::QuotaOnlyGlobal,
        &cancel,
    )
    .unwrap();
    assert_eq!(
        *api.trace.borrow(),
        [
            "open_VerifiedCleanup:1",
            "identity:1",
            "path:1",
            "working_set:1",
            "trim:1",
            "working_set:1",
            "close:1"
        ]
    );
    assert_eq!(result.successful_count, 1);
    assert_eq!(result.items[0]["working_set_before_bytes"], 100);
    assert_eq!(
        result.timing.unwrap()["identity_scope"],
        "creation_time_verified"
    );
}

#[test]
fn candidate_targets_cannot_bypass_creation_or_path_mismatch() {
    let cancel = AtomicBool::new(false);
    let api = Isolated::new(&cancel);
    let mut first = target(1);
    first.created_at = "reused_pid".into();
    let mut second = target(2);
    second.image_path = Some("c:/other.exe".into());
    let result = sweep(
        &&api,
        Some(&[first, second]),
        Strategy::QuotaOnlyGlobal,
        &cancel,
    )
    .unwrap();
    assert_eq!(result.attempted_count, 0);
    assert_eq!(result.skipped_count, 2);
    assert_eq!(
        *api.trace.borrow(),
        [
            "open_VerifiedCleanup:1",
            "identity:1",
            "close:1",
            "open_VerifiedCleanup:2",
            "identity:2",
            "path:2",
            "close:2"
        ]
    );
}

#[test]
fn quota_failures_keep_native_return_and_open_skip_without_retry() {
    let cancel = AtomicBool::new(false);
    let mut api = Isolated::new(&cancel);
    api.native_failure = Some(1);
    api.open_failure = Some(2);
    let result = sweep(&&api, None, Strategy::QuotaOnlyGlobal, &cancel).unwrap();
    assert_eq!(result.failed_count, 1);
    assert_eq!(result.skipped_count, 1);
    assert_eq!(result.successful_count, 0);
    assert_eq!(result.items[0]["status"], 5);
    assert_eq!(result.items[0]["outcome"], "failure");
    assert_eq!(
        *api.trace.borrow(),
        [
            "enumerate",
            "open_QuotaOnlyCleanup:1",
            "trim:1",
            "close:1",
            "open_QuotaOnlyCleanup:2"
        ]
    );
}

#[test]
fn preexisting_cancel_never_opens_or_trims() {
    let cancel = AtomicBool::new(true);
    let api = Isolated::new(&cancel);
    let result = sweep(&&api, None, Strategy::QuotaOnlyGlobal, &cancel).unwrap();
    assert_eq!(*api.trace.borrow(), ["enumerate"]);
    assert!(result.cancelled);
    assert_eq!(result.visited_count, 0);
}

#[test]
fn cancellation_after_open_closes_without_calling_or_opening_next() {
    let cancel = AtomicBool::new(false);
    let mut api = Isolated::new(&cancel);
    api.cancel_at = Some("open_QuotaOnlyCleanup");
    let result = sweep(&&api, None, Strategy::QuotaOnlyGlobal, &cancel).unwrap();
    assert!(result.cancelled);
    assert_eq!(result.attempted_count, 0);
    assert_eq!(
        *api.trace.borrow(),
        ["enumerate", "open_QuotaOnlyCleanup:1", "close:1"]
    );
}

#[test]
fn cancellation_in_native_call_waits_for_close_and_preserves_return() {
    let cancel = AtomicBool::new(false);
    let mut api = Isolated::new(&cancel);
    api.cancel_at = Some("trim");
    let result = sweep(&&api, None, Strategy::QuotaOnlyGlobal, &cancel).unwrap();
    assert!(result.cancelled);
    assert_eq!(result.successful_count, 1);
    assert!(result.resources_closed);
    assert_eq!(
        *api.trace.borrow(),
        ["enumerate", "open_QuotaOnlyCleanup:1", "trim:1", "close:1"]
    );
}

#[test]
fn unknown_close_stops_before_next_and_keeps_completed_native_fact() {
    let cancel = AtomicBool::new(false);
    let mut api = Isolated::new(&cancel);
    api.close_failure = Some(1);
    let result = sweep(&&api, None, Strategy::QuotaOnlyGlobal, &cancel).unwrap();
    assert_eq!(result.successful_count, 1);
    assert_eq!(result.unknown_count, 1);
    assert!(!result.resources_closed);
    assert_eq!(
        *api.trace.borrow(),
        ["enumerate", "open_QuotaOnlyCleanup:1", "trim:1", "close:1"]
    );
}

#[test]
fn progress_observes_actual_returns_without_adding_native_calls() {
    let cancel = AtomicBool::new(false);
    let mut api = Isolated::new(&cancel);
    api.open_failure = Some(2);
    let mut observed = Vec::new();
    let result = sweep_with_progress(&&api, None, Strategy::Verified, &cancel, &mut |sweep| {
        observed.push(crate::contracts::CleanupProgress::from(sweep))
    })
    .unwrap();
    assert_eq!(observed.first().unwrap().visited_count, 0);
    let last = observed.last().unwrap();
    assert_eq!(last.visited_count, result.visited_count);
    assert_eq!(last.skipped_count, result.skipped_count);
    assert_eq!(last.successful_count, result.successful_count);
    assert_eq!(
        api.trace
            .borrow()
            .iter()
            .filter(|s| s.starts_with("trim:"))
            .count(),
        result.attempted_count
    );
}
