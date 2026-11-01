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
    let definitions = [
        (
            "memory_status",
            "读取当前真实内存容量",
            false,
            json!({}),
            json!([]),
            json!([]),
        ),
        (
            "memory_targets",
            "发现可访问进程及创建身份，供明确选择",
            false,
            json!({}),
            json!([]),
            json!([]),
        ),
        (
            "memory_cleanup",
            "执行一轮全局工作集整理，不结束进程",
            true,
            json!({}),
            json!([]),
            json!(["--confirm"]),
        ),
        (
            "memory_process_cleanup",
            "仅整理选定身份的进程工作集",
            true,
            json!({"pid":{"type":"integer","minimum":1},"created_at":{"type":"string","minLength":1}}),
            json!(["pid", "created_at"]),
            json!([
                "--pid",
                "{pid}",
                "--created-at",
                "{created_at}",
                "--confirm"
            ]),
        ),
        (
            "memory_batch_cleanup",
            "按请求文件整理明确目标并应用保护规则",
            true,
            json!({"request_file":{"type":"string","minLength":1,"description":"UTF-8 JSON: targets为memory_targets返回的目标对象数组，protections为可选保护规则数组"}}),
            json!(["request_file"]),
            json!(["--request-file", "{request_file}", "--confirm"]),
        ),
        (
            "memory_result",
            "只读回看已封存记录，不重放动作",
            false,
            json!({"run_dir":{"type":"string","minLength":1}}),
            json!(["run_dir"]),
            json!(["--run-dir", "{run_dir}"]),
        ),
    ];
    let tools: Vec<Value> = definitions.into_iter().map(|(name, description, confirm, properties, required, args)| {
        let mut argv = vec![json!("--tool"), json!(name)];
        argv.extend(args.as_array().expect("static arguments").iter().cloned());
        json!({"name":name,"description":description,"confirmation_required":confirm,
            "input_schema":{"type":"object","properties":properties,"required":required,"additionalProperties":false},
            "command":{"executable":"cpu-memory-cleanup","args":argv}})
    }).collect();
    json!({"tool_schema_version":1,"implementation":"rust","version":crate::VERSION,
        "output":"one JSON report on stdout","exit_codes":{"0":"完整成功","1":"partial/failed/unknown/cancelled，原始事实在JSON中","2":"参数或入口错误，原因在stderr"},
        "maintenance_output_option":["--output-dir","{directory}"],"tools":tools})
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
