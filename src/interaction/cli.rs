use crate::{
    Application,
    contracts::{AppResult, Target},
    interaction::web,
};
use serde_json::{Value, json};
use std::{
    collections::HashMap,
    path::PathBuf,
    sync::{Arc, atomic::AtomicBool},
};

pub fn catalog() -> Value {
    json!({"tool_schema_version":1,"implementation":"rust","version":crate::VERSION,"tools":[
        {"name":"memory_status","confirmation_required":false},
        {"name":"memory_targets","confirmation_required":false},
        {"name":"memory_cleanup","confirmation_required":true},
        {"name":"memory_process_cleanup","confirmation_required":true,"required":["pid","created_at"]},
        {"name":"memory_batch_cleanup","confirmation_required":true,"required":["request_file"]},
        {"name":"memory_result","confirmation_required":false,"required":["run_dir"]}]})
}
pub fn main(args: Vec<String>) -> i32 {
    match execute(args) {
        Ok(code) => code,
        Err(error) => {
            eprintln!("{error}");
            2
        }
    }
}
fn execute(args: Vec<String>) -> AppResult<i32> {
    if args.iter().any(|arg| arg == "--help" || arg == "-h") {
        println!(
            "CPU 内存清理 Rust {}\n--web [--port 14800] [--open] [--output-dir DIR]\n--tool-info\n--tool memory_status|memory_targets|memory_cleanup|memory_process_cleanup|memory_batch_cleanup|memory_result\n维护需要--confirm，单目标需要--pid及--created-at，批次需要--request-file，回看需要--run-dir。",
            crate::VERSION
        );
        return Ok(0);
    }
    let mut options = HashMap::new();
    let mut i = 0;
    while i < args.len() {
        let arg = &args[i];
        if !matches!(
            arg.as_str(),
            "--web"
                | "--open"
                | "--json"
                | "--confirm"
                | "--tool-info"
                | "--port"
                | "--output-dir"
                | "--tool"
                | "--pid"
                | "--created-at"
                | "--run-dir"
                | "--request-file"
        ) {
            return Err(format!("未知参数：{arg}，使用--help查看Rust入口"));
        }
        let value = if matches!(
            arg.as_str(),
            "--web" | "--open" | "--json" | "--confirm" | "--tool-info"
        ) {
            "true".into()
        } else {
            i += 1;
            args.get(i)
                .filter(|value| !value.starts_with("--"))
                .ok_or_else(|| format!("{arg}缺少值"))?
                .clone()
        };
        if options.insert(arg.clone(), value).is_some() {
            return Err(format!("重复参数：{arg}"));
        }
        i += 1;
    }
    if options.contains_key("--tool-info") {
        println!("{}", catalog());
        return Ok(0);
    }
    let output = options
        .get("--output-dir")
        .map(PathBuf::from)
        .unwrap_or_else(|| PathBuf::from(".test-results/rust-runs"));
    if args.is_empty() || options.contains_key("--web") {
        if options.contains_key("--tool") || options.contains_key("--confirm") {
            return Err("网页启动不接受工具或维护确认参数".into());
        }
        let port = options
            .get("--port")
            .map(|value| {
                value
                    .parse::<u16>()
                    .map_err(|_| "端口必须为0..65535".to_string())
            })
            .transpose()?
            .unwrap_or(14800);
        let server = web::Workbench::bind(Arc::new(Application::default()), output, port)?;
        println!("{}", server.url());
        println!(
            "Rust {} · 真实系统 · 启动不采样，点击才整理",
            crate::VERSION
        );
        if options.contains_key("--open") {
            open(&server.url())?;
        }
        server.serve()?;
        return Ok(0);
    }
    let tool = options.get("--tool").ok_or("需要--tool或--web")?;
    let app = Application::default();
    let confirmed = options.contains_key("--confirm");
    let cancel = AtomicBool::new(false);
    let required = |name: &str| options.get(name).ok_or_else(|| format!("缺少{name}"));
    let report = match tool.as_str() {
        "memory_status" => app.memory_status()?,
        "memory_targets" => app.memory_targets()?,
        "memory_cleanup" => app.memory_cleanup(&output, confirmed, &cancel)?,
        "memory_process_cleanup" => {
            let pid = required("--pid")?.parse::<u32>().map_err(|_| "PID无效")?;
            let target = Target {
                pid,
                created_at: required("--created-at")?.clone(),
                name: format!("PID {pid}"),
                image_path: None,
                working_set_bytes: None,
            };
            app.memory_batch_cleanup(&output, &[target], &[], confirmed, &cancel)?
        }
        "memory_batch_cleanup" => {
            #[derive(serde::Deserialize)]
            #[serde(deny_unknown_fields)]
            struct Batch {
                targets: Vec<Target>,
                #[serde(default)]
                protections: Vec<crate::application::policy::Protection>,
            }
            let path = PathBuf::from(required("--request-file")?);
            if std::fs::metadata(&path).map_err(|e| e.to_string())?.len() > 1024 * 1024 {
                return Err("请求文件超过1MiB".into());
            }
            let request: Batch =
                serde_json::from_slice(&std::fs::read(path).map_err(|e| e.to_string())?)
                    .map_err(|e| e.to_string())?;
            app.memory_batch_cleanup(
                &output,
                &request.targets,
                &request.protections,
                confirmed,
                &cancel,
            )?
        }
        "memory_result" => app.memory_result(&PathBuf::from(required("--run-dir")?))?,
        _ => return Err("未知内存工具".into()),
    };
    let exit = if report.completion == "success" { 0 } else { 1 };
    let mut value = serde_json::to_value(report).map_err(|e| e.to_string())?;
    value["tool_schema_version"] = json!(1);
    value["tool"] = json!(tool);
    println!("{value}");
    Ok(exit)
}
fn open(url: &str) -> AppResult<()> {
    #[cfg(windows)]
    {
        std::process::Command::new("rundll32.exe")
            .args(["url.dll,FileProtocolHandler", url])
            .spawn()
            .map_err(|e| e.to_string())?;
        Ok(())
    }
    #[cfg(not(windows))]
    {
        let _ = url;
        Err("当前平台请手动打开本地地址".into())
    }
}
