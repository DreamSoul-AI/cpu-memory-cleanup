//! Local timing and pure statistics. This module never accesses a process or dispatches cleanup.
use serde_json::{Map, Value, json};
use std::time::Instant;

pub struct Stages {
    started: Instant,
    values: Map<String, Value>,
}
impl Default for Stages {
    fn default() -> Self {
        Self {
            started: Instant::now(),
            values: Map::new(),
        }
    }
}
impl Stages {
    pub fn measure<T>(&mut self, name: &str, operation: impl FnOnce() -> T) -> T {
        let started = Instant::now();
        let result = operation();
        let elapsed = started.elapsed().as_secs_f64() * 1000.;
        self.add(name, elapsed);
        result
    }
    pub fn add(&mut self, name: &str, elapsed: f64) {
        let previous = self.values.get(name).and_then(Value::as_f64).unwrap_or(0.);
        self.values.insert(name.into(), json!(previous + elapsed));
    }
    pub fn report(&self, scope: &str) -> Value {
        let elapsed = self.started.elapsed().as_secs_f64() * 1000.;
        let measured: f64 = self.values.values().filter_map(Value::as_f64).sum();
        json!({"schema_version":1,"elapsed_ms":elapsed,"scope":scope,"stages_ms":self.values,
            "bookkeeping_ms":(elapsed-measured).max(0.),"composition":"stages are disjoint; nested native timing is not added again"})
    }
}

pub fn native_summary(samples: &[(u32, f64)], expected: usize) -> Value {
    let mut ranked: Vec<_> = samples
        .iter()
        .copied()
        .filter(|(_, ms)| ms.is_finite() && *ms >= 0.)
        .collect();
    ranked.sort_by(|a, b| b.1.total_cmp(&a.1));
    let total: f64 = ranked.iter().map(|s| s.1).sum();
    let percentile = |q: f64| -> Option<f64> {
        (!ranked.is_empty()).then(|| {
            ranked[ranked.len() - 1 - (((ranked.len() - 1) as f64 * q).round() as usize)].1
        })
    };
    let top_ms: f64 = ranked.iter().take(20).map(|s| s.1).sum();
    json!({"sample_count":ranked.len(),"expected_count":expected,"complete":ranked.len()==expected,
        "sum_ms":total,"p50_ms":percentile(0.5),"p95_ms":percentile(0.95),"p99_ms":percentile(0.99),
        "max_ms":ranked.first().map(|s|s.1),"top20_ms":top_ms,"top20_percent":if total>0.{Some(top_ms/total*100.)}else{None},
        "top20":ranked.iter().take(20).map(|(pid,ms)|json!({"pid":pid,"native_call_ms":ms})).collect::<Vec<_>>(),
        "quantile_method":"nearest index round((n-1)*q)","scope":"measured calls only; PID is bound to this run, not a current application lookup"})
}

pub fn summarize_report(report: &mut crate::contracts::Report) {
    let sweep = &report.action["process_sweep"];
    if sweep.is_null() {
        return;
    }
    let summary = if let Some(value) = sweep["timing"].get("native_summary") {
        value.clone()
    } else {
        let samples: Vec<_> = sweep["items"]
            .as_array()
            .into_iter()
            .flatten()
            .filter_map(|item| {
                Some((
                    u32::try_from(item["pid"].as_u64()?).ok()?,
                    item["native_call_ms"].as_f64()?,
                ))
            })
            .collect();
        let Some(expected) = sweep["attempted_count"].as_u64() else {
            return;
        };
        native_summary(&samples, expected as usize)
    };
    report.metadata["native_summary"] = summary;
}
