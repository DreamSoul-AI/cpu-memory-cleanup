// integration/content, p1, c1: comparison protocol through isolated Application
// and temporary files. No host process reads, workload experiment or native cleanup.
use super::*;
use crate::{
    contracts::{Memory, Sweep},
    system::System,
};
use std::{
    cell::Cell,
    sync::{
        Arc,
        atomic::{AtomicUsize, Ordering},
    },
};

struct Isolated {
    calls: AtomicUsize,
    reads: AtomicUsize,
    method: Method,
}
impl System for Isolated {
    fn supported(&self) -> bool {
        true
    }
    fn snapshot(&self) -> AppResult<Memory> {
        self.reads.fetch_add(1, Ordering::SeqCst);
        Ok(Memory {
            total_bytes: 1000,
            available_bytes: 500,
            commit_used_bytes: 100,
            commit_limit_bytes: 2000,
        })
    }
    fn targets(&self) -> AppResult<Vec<Target>> {
        panic!("comparison must use its fixed observer")
    }
    fn cleanup(&self, selected: Option<&[Target]>, _: &AtomicBool) -> AppResult<Sweep> {
        assert!(selected.is_none());
        self.calls.fetch_add(1, Ordering::SeqCst);
        Ok(Sweep {
            enumerated_count: 1,
            visited_count: 1,
            attempted_count: 1,
            successful_count: 1,
            resources_closed: true,
            items: vec![json!({"pid":std::process::id(),"outcome":"success"})],
            timing: Some(json!({"strategy":self.method.strategy(),"elapsed_ms":1.})),
            ..Default::default()
        })
    }
}
fn target() -> Target {
    Target {
        pid: std::process::id(),
        created_at: "same-instance".into(),
        name: String::new(),
        image_path: None,
        working_set_bytes: Some(4096),
    }
}
fn system(method: Method) -> Arc<Isolated> {
    Arc::new(Isolated {
        calls: AtomicUsize::new(0),
        reads: AtomicUsize::new(0),
        method,
    })
}
fn setup() -> (Session, ResidentLoad, PathBuf) {
    let directory = std::env::temp_dir().join(format!(
        "cleanup-compare-{}",
        crate::contracts::identifier().unwrap()
    ));
    let session =
        Session::create(&directory, Scope::Owned, vec![std::process::id()], 4096).unwrap();
    (session, ResidentLoad::new(4096).unwrap(), directory)
}
fn pair() -> (Evidence, Evidence) {
    let (mut session, mut load, directory) = setup();
    let a = session
        .run_once(
            Method::Verified,
            true,
            &Application::new(system(Method::Verified)),
            &mut load,
            || Ok(vec![target()]),
        )
        .unwrap();
    let b = session
        .run_once(
            Method::Quota,
            true,
            &Application::new(system(Method::Quota)),
            &mut load,
            || Ok(vec![target()]),
        )
        .unwrap();
    fs::remove_dir_all(directory).unwrap();
    (a, b)
}
#[test]
fn confirmation_is_per_method_and_has_no_preparation_or_native_calls_when_missing() {
    let (mut session, mut load, directory) = setup();
    let native = system(Method::Verified);
    let app = Application::new(native.clone());
    assert!(
        session
            .run_once(Method::Verified, false, &app, &mut load, || panic!(
                "no observer"
            ))
            .is_err()
    );
    assert_eq!(native.calls.load(Ordering::SeqCst), 0);
    assert_eq!(native.reads.load(Ordering::SeqCst), 0);
    assert!(!directory.join("verified.started.json").exists());
    fs::remove_dir_all(directory).unwrap();
}
#[test]
fn each_method_dispatches_once_and_evidence_roundtrip_is_readonly() {
    let (mut session, mut load, directory) = setup();
    let native = system(Method::Verified);
    let app = Application::new(native.clone());
    let observations = Cell::new(0);
    let evidence = session
        .run_once(Method::Verified, true, &app, &mut load, || {
            observations.set(observations.get() + 1);
            Ok(vec![target()])
        })
        .unwrap();
    assert_eq!(observations.get(), 2);
    assert_eq!(native.calls.load(Ordering::SeqCst), 1);
    assert_eq!(native.reads.load(Ordering::SeqCst), 2);
    assert!(evidence.load_after.content_valid);
    let saved = read_evidence(&directory.join("verified.json")).unwrap();
    assert_eq!(
        saved.report.unwrap().run_id,
        evidence.report.unwrap().run_id
    );
    assert!(
        session
            .run_once(Method::Verified, true, &app, &mut load, || panic!(
                "no retry"
            ))
            .is_err()
    );
    assert_eq!(native.calls.load(Ordering::SeqCst), 1);
    assert!(Session::create(&directory, Scope::Owned, vec![std::process::id()], 4096).is_err());
    fs::remove_dir_all(directory).unwrap();
}
#[test]
fn preparation_failure_consumes_method_without_native_retry() {
    let (mut session, mut load, directory) = setup();
    let native = system(Method::Quota);
    let app = Application::new(native.clone());
    assert!(
        session
            .run_once(Method::Quota, true, &app, &mut load, || Err(
                "observe failed".into()
            ))
            .is_err()
    );
    assert!(
        session
            .run_once(Method::Quota, true, &app, &mut load, || Ok(vec![target()]))
            .is_err()
    );
    assert_eq!(native.calls.load(Ordering::SeqCst), 0);
    assert!(directory.join("quota.started.json").exists());
    fs::remove_dir_all(directory).unwrap();
}
#[test]
fn matching_pair_is_an_observation_never_a_winner() {
    let (a, b) = pair();
    let report = compare(&a, &b).unwrap();
    assert_eq!(report["issues"], json!([]));
    assert!(report["winner"].is_null());
    assert_eq!(report["observations"][0]["coverage"]["attempted"], 1);
    assert_eq!(report["starting_difference_percent"], 0.);
    assert!(compare(&a, &a).is_err());
}
#[test]
fn different_sessions_and_changed_identity_are_visible() {
    let (a, mut b) = pair();
    b.protocol.session_id = "other-session".into();
    b.after[0].created_at = "reused-pid".into();
    let issues = compare(&a, &b).unwrap()["issues"].to_string();
    assert!(issues.contains("会话"));
    assert!(issues.contains("身份观测"));
}
#[test]
fn starting_difference_unknown_coverage_and_wrong_assembly_cannot_look_comparable() {
    let (a, mut b) = pair();
    b.before[0].working_set_bytes = Some(8192);
    let report = b.report.as_mut().unwrap();
    report.action["process_sweep"]["unknown_count"] = json!(1);
    report.action["process_sweep"]["timing"]["strategy"] = json!("verified_serial");
    let issues = compare(&a, &b).unwrap()["issues"].to_string();
    assert!(issues.contains("5%"));
    assert!(issues.contains("收尾不完整"));
    assert!(issues.contains("装配方法"));
}
#[test]
fn workload_content_check_detects_corruption_and_restore_touches_original_budget() {
    let mut load = ResidentLoad::new(4096).unwrap();
    load.0[42] = 0;
    assert!(!load.read().content_valid);
    assert!(load.restore().content_valid);
    assert_eq!(load.read().bytes, 4096);
    assert!(ResidentLoad::new(0).is_err());
    assert!(ResidentLoad::new(257 * 1024 * 1024).is_err());
}
