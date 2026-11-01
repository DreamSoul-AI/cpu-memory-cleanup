use crate::contracts::{AppResult, Report};
use serde_json::{Value, json};
use std::{fs, path::Path};

pub fn save(base: &Path, report: &mut Report) -> AppResult<()> {
    let directory = base.join(&report.run_id);
    fs::create_dir_all(&directory).map_err(|e| e.to_string())?;
    report.output_dir = Some(directory.to_string_lossy().into_owned());
    let temporary = directory.join("report.json.tmp");
    fs::write(
        &temporary,
        serde_json::to_vec(report).map_err(|e| e.to_string())?,
    )
    .map_err(|e| e.to_string())?;
    fs::rename(temporary, directory.join("report.json")).map_err(|e| format!("记录提交失败：{e}"))
}
fn read(path: &Path) -> AppResult<Vec<u8>> {
    let metadata = fs::metadata(path).map_err(|e| e.to_string())?;
    if metadata.len() > 16 * 1024 * 1024 {
        return Err("记录超过16MiB预算".into());
    }
    fs::read(path).map_err(|e| e.to_string())
}
pub fn review(directory: &Path) -> AppResult<Report> {
    let path = directory.join("report.json");
    let mut original: Report = if path.exists() {
        serde_json::from_slice(&read(&path)?).map_err(|e| format!("记录无效：{e}"))?
    } else {
        legacy(directory)?
    };
    if original.phase != "finished" {
        return Err("记录尚未封存，已有动作结果保持未知，不重放".into());
    }
    let source_id = original.run_id.clone();
    original.run_id = crate::contracts::identifier()?;
    original.mode = "review".into();
    original.metadata["source_run_id"] = json!(source_id);
    original.metadata["review_implementation"] = json!("rust");
    Ok(original)
}
fn lines(path: &Path) -> AppResult<Vec<Value>> {
    let data = read(path)?;
    let text = String::from_utf8(data).map_err(|e| e.to_string())?;
    text.lines()
        .map(|line| serde_json::from_str(line).map_err(|e| format!("旧记录损坏：{e}")))
        .collect()
}
fn legacy(directory: &Path) -> AppResult<Report> {
    let rows = lines(&directory.join("samples.jsonl"))?;
    let mut report = Report::new("review")?;
    let header = rows
        .iter()
        .find(|r| r["record_type"] == "header")
        .ok_or("旧记录缺少header")?;
    report.run_id = header["run_id"].as_str().ok_or("旧记录缺少run_id")?.into();
    report.samples = rows
        .iter()
        .filter(|r| r["record_type"] == "sample")
        .map(|r| r["sample"].clone())
        .collect();
    if report.samples.iter().any(|s| s["run_id"] != report.run_id) {
        return Err("旧记录样本身份不一致".into());
    }
    let terminal = rows
        .iter()
        .find(|r| r["record_type"] == "end")
        .ok_or("旧记录未封存，不能冒充完整结果")?;
    report.completion = terminal["completion"]
        .as_str()
        .or_else(|| terminal["status"].as_str())
        .unwrap_or("unknown")
        .into();
    report.metadata["header"] = header.clone();
    report.metadata["legacy_terminal"] = terminal.clone();
    report.errors = terminal["errors"].as_array().cloned().unwrap_or_default();
    for row in lines(&directory.join("operation.jsonl"))? {
        if row["run_id"] != report.run_id {
            return Err("旧动作身份不一致".into());
        }
        if row["record_type"] == "action_result" {
            report.action = row["action"].clone();
        }
    }
    let diagnostics = directory.join("diagnostics.json");
    if diagnostics.exists() {
        let value: Value =
            serde_json::from_slice(&read(&diagnostics)?).map_err(|e| e.to_string())?;
        if value["run_id"] != report.run_id {
            return Err("旧诊断附件身份不一致".into());
        }
        report.metadata["diagnostics"] = value;
    }
    if report.samples.len() == 2 {
        if let (Some(before), Some(after)) = (
            report.samples[0]["metrics"]["physical"]["available_bytes"].as_i64(),
            report.samples[1]["metrics"]["physical"]["available_bytes"].as_i64(),
        ) {
            report.analysis = json!({"available_change_bytes":after-before});
        }
    }
    report.output_dir = Some(directory.to_string_lossy().into_owned());
    Ok(report)
}
