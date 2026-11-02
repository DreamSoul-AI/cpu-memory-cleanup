use cpu_memory_cleanup::system::measurement::{Stages, native_summary, summarize_report};
use serde_json::json;

#[test]
fn native_distribution_preserves_long_tail_and_missing_coverage() {
    let samples: Vec<_> = (1..=100).map(|pid| (pid, f64::from(pid))).collect();
    let summary = native_summary(&samples, 100);
    assert_eq!(summary["sum_ms"], 5050.);
    assert_eq!(summary["p50_ms"], 51.);
    assert_eq!(summary["p95_ms"], 95.);
    assert_eq!(summary["p99_ms"], 99.);
    assert_eq!(summary["top20"][0]["pid"], 100);
    assert_eq!(summary["top20_ms"], 1810.);
    assert_eq!(summary["complete"], true);
    assert_eq!(native_summary(&samples, 150)["complete"], false);
    assert!(native_summary(&[], 0)["p95_ms"].is_null());
}

#[test]
fn legacy_summary_uses_only_saved_calls_and_leaves_stages_unknown() {
    let mut report = cpu_memory_cleanup::contracts::Report::new("review").unwrap();
    report.action = json!({"process_sweep":{"attempted_count":3,"items":[{"pid":42,"native_call_ms":12.},{"pid":43,"native_call_ms":4.}]}});
    let original = report.action.clone();
    summarize_report(&mut report);
    assert_eq!(report.metadata["native_summary"]["sum_ms"], 16.);
    assert_eq!(report.metadata["native_summary"]["complete"], false);
    assert!(report.metadata.get("timing").is_none());
    assert_eq!(report.action, original);
}

#[test]
fn stage_measurement_preserves_results_and_separates_disjoint_work() {
    let mut stages = Stages::default();
    let result: Result<u8, &str> = stages.measure("first", || Err("expected"));
    assert_eq!(result, Err("expected"));
    assert_eq!(stages.measure("second", || 42), 42);
    let timing = stages.report("test");
    let sum: f64 = timing["stages_ms"]
        .as_object()
        .unwrap()
        .values()
        .map(|v| v.as_f64().unwrap())
        .sum();
    assert!(timing["elapsed_ms"].as_f64().unwrap() >= sum);
    assert_eq!(timing["scope"], "test");
}
