use cpu_memory_cleanup::{
    Application,
    application::service::brief,
    contracts::{AppResult, Memory, Sweep, Target},
    system::System,
};
use std::sync::{
    Arc,
    atomic::{AtomicBool, AtomicUsize, Ordering},
};

struct Isolated {
    reads: AtomicUsize,
    actions: AtomicUsize,
}
impl System for Isolated {
    fn supported(&self) -> bool {
        true
    }
    fn snapshot(&self) -> AppResult<Memory> {
        let n = self.reads.fetch_add(1, Ordering::SeqCst);
        Ok(Memory {
            total_bytes: 1000,
            available_bytes: if n == 0 { 200 } else { 500 },
            commit_used_bytes: Some(1000),
            commit_limit_bytes: Some(2000),
            ..Memory::default()
        })
    }
    fn targets(&self) -> AppResult<Vec<Target>> {
        Ok(Vec::new())
    }
    fn cleanup(&self, _: Option<&[Target]>, cancel: &AtomicBool) -> AppResult<Sweep> {
        self.actions.fetch_add(1, Ordering::SeqCst);
        Ok(Sweep {
            enumerated_count: 1,
            visited_count: 1,
            attempted_count: usize::from(!cancel.load(Ordering::SeqCst)),
            successful_count: usize::from(!cancel.load(Ordering::SeqCst)),
            resources_closed: true,
            cancelled: cancel.load(Ordering::SeqCst),
            ..Default::default()
        })
    }
}
fn setup() -> (Arc<Isolated>, Application) {
    let native = Arc::new(Isolated {
        reads: AtomicUsize::new(0),
        actions: AtomicUsize::new(0),
    });
    (native.clone(), Application::new(native))
}
#[test]
fn construction_and_catalog_do_not_touch_system() {
    let (system, _) = setup();
    assert_eq!(
        cpu_memory_cleanup::interaction::cli::catalog()["tools"]
            .as_array()
            .unwrap()
            .len(),
        6
    );
    assert_eq!(system.reads.load(Ordering::SeqCst), 0);
    assert_eq!(system.actions.load(Ordering::SeqCst), 0);
}
#[test]
fn unconfirmed_request_has_zero_reads_and_zero_actions() {
    let (system, app) = setup();
    assert!(
        app.memory_cleanup(
            std::path::Path::new("unused"),
            false,
            &AtomicBool::new(false)
        )
        .is_err()
    );
    assert_eq!(system.reads.load(Ordering::SeqCst), 0);
    assert_eq!(system.actions.load(Ordering::SeqCst), 0);
}
#[test]
fn cleanup_records_actual_snapshots_once_and_review_has_no_calls() {
    let (system, app) = setup();
    let directory = std::env::temp_dir().join(format!(
        "cpu-memory-rust-{}",
        cpu_memory_cleanup::contracts::identifier().unwrap()
    ));
    let report = app
        .memory_cleanup(&directory, true, &AtomicBool::new(false))
        .unwrap();
    assert_eq!(system.actions.load(Ordering::SeqCst), 1);
    assert_eq!(system.reads.load(Ordering::SeqCst), 2);
    assert_eq!(report.analysis["available_change_bytes"], 300);
    assert_eq!(report.completion, "success");
    let review = app
        .memory_result(std::path::Path::new(report.output_dir.as_ref().unwrap()))
        .unwrap();
    assert_eq!(review.mode, "review");
    assert_ne!(review.run_id, report.run_id);
    assert_eq!(review.samples, report.samples);
    assert_eq!(
        review.metadata["timing"]["scope"],
        report.metadata["timing"]["scope"]
    );
    for (stage, value) in report.metadata["timing"]["stages_ms"].as_object().unwrap() {
        assert!(
            (review.metadata["timing"]["stages_ms"][stage]
                .as_f64()
                .unwrap()
                - value.as_f64().unwrap())
            .abs()
                < 1e-8
        );
    }
    assert!(
        report.metadata["timing"]["stages_ms"]["record_final"]
            .as_f64()
            .is_some()
    );
    assert!(
        std::path::Path::new(report.output_dir.as_ref().unwrap())
            .join("timing.json")
            .exists()
    );
    assert_eq!(system.actions.load(Ordering::SeqCst), 1);
    assert_eq!(system.reads.load(Ordering::SeqCst), 2);
    std::fs::remove_dir_all(directory).unwrap();
}
#[test]
fn report_summary_omits_per_target_diagnostics() {
    let mut report = cpu_memory_cleanup::contracts::Report::new("maintain").unwrap();
    report.action = serde_json::json!({"process_sweep":{"successful_count":2,"items":[{"pid":1}]}});
    report.metadata["diagnostics"] = serde_json::json!({"large":"details"});
    let value = brief(&report);
    assert!(value["action"]["process_sweep"].get("items").is_none());
    assert!(value["metadata"].get("diagnostics").is_none());
    assert_eq!(
        report.action["process_sweep"]["items"]
            .as_array()
            .unwrap()
            .len(),
        1
    );
}
#[test]
fn invalid_targets_are_rejected_before_system_access() {
    let (system, app) = setup();
    let target = Target {
        pid: 42,
        created_at: String::new(),
        name: String::new(),
        image_path: None,
        working_set_bytes: None,
    };
    assert!(
        app.memory_batch_cleanup(
            std::path::Path::new("unused"),
            &[target],
            &[],
            true,
            &AtomicBool::new(false)
        )
        .is_err()
    );
    assert_eq!(system.reads.load(Ordering::SeqCst), 0);
}

#[test]
fn record_failure_blocks_native_dispatch() {
    let (system, app) = setup();
    let file = std::env::temp_dir().join(format!(
        "cpu-memory-block-{}",
        cpu_memory_cleanup::contracts::identifier().unwrap()
    ));
    std::fs::write(&file, b"not a directory").unwrap();
    let report = app
        .memory_cleanup(&file, true, &AtomicBool::new(false))
        .unwrap();
    assert_eq!(report.completion, "failed");
    assert_eq!(report.action["attempted"], false);
    assert_eq!(system.actions.load(Ordering::SeqCst), 0);
    std::fs::remove_file(file).unwrap();
}

#[test]
fn pressure_rule_requires_sustained_pressure_and_cooldown() {
    use cpu_memory_cleanup::application::policy::{Policy, Rule};
    let start = std::time::Instant::now();
    let target = Target {
        pid: 42,
        created_at: "1".into(),
        name: "test".into(),
        image_path: None,
        working_set_bytes: None,
    };
    let mut policy = Policy::default();
    assert!(!policy.armed);
    policy
        .arm(
            Rule {
                mode: "pressure".into(),
                interval_s: 600,
                available_pct: 10.,
                sustain_s: 3,
                cooldown_s: 20,
            },
            vec![target],
            vec![],
            start,
        )
        .unwrap();
    assert!(!policy.due(start, Some(5.)));
    assert!(!policy.due(start + std::time::Duration::from_secs(2), Some(5.)));
    assert!(policy.due(start + std::time::Duration::from_secs(3), Some(5.)));
    policy.dispatched(start + std::time::Duration::from_secs(3));
    assert!(!policy.due(start + std::time::Duration::from_secs(10), Some(5.)));
    policy.armed = false;
    assert!(!policy.due(start + std::time::Duration::from_secs(100), Some(5.)));
}

#[test]
fn path_protection_covers_same_program_and_unknown_path_conservatively() {
    use cpu_memory_cleanup::application::policy::Protection;
    let rule = Protection {
        image_path: Some("C:\\Apps\\Browser.exe".into()),
        pid: None,
        created_at: None,
    };
    let mut target = Target {
        pid: 42,
        created_at: "1".into(),
        name: "browser".into(),
        image_path: Some("c:/apps/browser.exe".into()),
        working_set_bytes: None,
    };
    assert!(rule.matches(&target));
    target.image_path = Some("C:/Apps/Other.exe".into());
    assert!(!rule.matches(&target));
    target.image_path = None;
    assert!(rule.matches(&target));
}

#[test]
fn unfinished_record_is_not_replayed_or_relabelled_complete() {
    let directory = std::env::temp_dir().join(format!(
        "cpu-memory-unfinished-{}",
        cpu_memory_cleanup::contracts::identifier().unwrap()
    ));
    let mut report = cpu_memory_cleanup::contracts::Report::new("maintain").unwrap();
    report.phase = "calling".into();
    report.completion = "unknown".into();
    cpu_memory_cleanup::system::records::save(&directory, &mut report).unwrap();
    assert!(
        cpu_memory_cleanup::system::records::review(std::path::Path::new(
            report.output_dir.as_ref().unwrap()
        ))
        .is_err()
    );
    assert!(directory.starts_with(std::env::temp_dir()));
    std::fs::remove_dir_all(directory).unwrap();
}

#[test]
fn readonly_capability_blocks_maintenance_before_reading_or_recording() {
    struct ReadOnly;
    impl cpu_memory_cleanup::system::System for ReadOnly {
        fn supported(&self) -> bool {
            true
        }
        fn cleanup_supported(&self) -> bool {
            false
        }
        fn snapshot(&self) -> AppResult<Memory> {
            panic!("must not sample a refused action")
        }
        fn targets(&self) -> AppResult<Vec<Target>> {
            panic!("must not enumerate a refused action")
        }
        fn cleanup(&self, _: Option<&[Target]>, _: &AtomicBool) -> AppResult<Sweep> {
            panic!("must not dispatch")
        }
    }
    let app = Application::new(std::sync::Arc::new(ReadOnly));
    let output = std::env::temp_dir().join(format!(
        "memory-refused-{}",
        cpu_memory_cleanup::contracts::identifier().unwrap()
    ));
    let error = app
        .memory_cleanup(&output, true, &AtomicBool::new(false))
        .unwrap_err();
    assert!(error.contains("未派发"));
    assert!(!output.exists());
}
