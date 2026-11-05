use crate::{
    Application,
    contracts::{AppResult, Report, Target},
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
    json!({"tool_schema_version":1,"implementation":"rust","version":crate::VERSION,"build_id":crate::BUILD_ID,
        "output":"one JSON report on stdout","exit_codes":{"0":"完整成功","1":"partial/failed/unknown/cancelled，原始事实在JSON中","2":"参数或入口错误，原因在stderr"},
        "platform_capabilities":{"operating_system":std::env::consts::OS,"reading":cfg!(any(windows,target_os="linux",target_os="macos")),"maintenance":cfg!(windows)},
        "result_persistence":{"default":"stdout_only","no_record_option":"--no-record","save_option":["--output-dir","{directory}"],"history_requires_saved_result":true},
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
pub fn execute(args: Vec<String>) -> AppResult<i32> {
    if args == ["--version"] || args == ["-V"] {
        println!("CPU 内存清理 {} ({})", crate::VERSION, crate::BUILD_ID);
        return Ok(0);
    }
    if args.iter().any(|arg| arg == "--help" || arg == "-h") {
        println!(
            "CPU 内存清理 Rust {}\n--web [--port 14800] [--open] [--output-dir DIR]\n--version\n--tool-info\n--tool memory_status|memory_targets|memory_cleanup|memory_process_cleanup|memory_batch_cleanup|memory_result\n维护需要--confirm，默认只返回JSON不保存；--no-record明确禁止记录，--output-dir DIR显式保存，两者互斥。单目标需要--pid及--created-at，批次需要--request-file，回看需要已保存的--run-dir。",
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
                | "--no-record"
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
            "--web" | "--open" | "--json" | "--confirm" | "--no-record" | "--tool-info"
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
    if options.contains_key("--no-record") && options.contains_key("--output-dir") {
        return Err("--no-record与--output-dir互斥，未派发任何操作".into());
    }
    let output = options.get("--output-dir").map(PathBuf::from);
    if args.is_empty() || options.contains_key("--web") {
        if options.contains_key("--tool")
            || options.contains_key("--confirm")
            || options.contains_key("--no-record")
        {
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
        let server = match web::Workbench::bind(
            Arc::new(Application::default()),
            output.unwrap_or_else(|| PathBuf::from(".test-results/rust-runs")),
            port,
        ) {
            Ok(server) => server,
            Err(_) if options.contains_key("--open") && running_workbench(port) => {
                open(&format!("http://127.0.0.1:{port}/"))?;
                return Ok(0);
            }
            Err(error) => return Err(error),
        };
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
    let report = run_tool(&options, &Application::default())?;
    let exit = if report.completion == "success" { 0 } else { 1 };
    let mut value = serde_json::to_value(report).map_err(|e| e.to_string())?;
    value["tool_schema_version"] = json!(1);
    value["tool"] = json!(tool);
    println!("{value}");
    Ok(exit)
}
fn run_tool(options: &HashMap<String, String>, app: &Application) -> AppResult<Report> {
    let output = options.get("--output-dir").map(PathBuf::from);
    let tool = options.get("--tool").ok_or("需要--tool或--web")?;
    let confirmed = options.contains_key("--confirm");
    let cancel = AtomicBool::new(false);
    let required = |name: &str| options.get(name).ok_or_else(|| format!("缺少{name}"));
    match tool.as_str() {
        "memory_status" => app.memory_status(),
        "memory_targets" => app.memory_targets(),
        "memory_cleanup" => app.memory_cleanup_with_output(output.as_deref(), confirmed, &cancel),
        "memory_process_cleanup" => {
            let pid = required("--pid")?.parse::<u32>().map_err(|_| "PID无效")?;
            let target = Target {
                pid,
                created_at: required("--created-at")?.clone(),
                name: format!("PID {pid}"),
                image_path: None,
                working_set_bytes: None,
            };
            app.memory_batch_cleanup_with_output(
                output.as_deref(),
                &[target],
                &[],
                confirmed,
                &cancel,
            )
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
            app.memory_batch_cleanup_with_output(
                output.as_deref(),
                &request.targets,
                &request.protections,
                confirmed,
                &cancel,
            )
        }
        "memory_result" => app.memory_result(&PathBuf::from(required("--run-dir")?)),
        _ => Err("未知内存工具".into()),
    }
}
fn running_workbench(port: u16) -> bool {
    use std::io::{Read, Write};
    use std::net::{Ipv4Addr, SocketAddrV4, TcpStream};
    use std::time::Duration;
    let Ok(mut stream) = TcpStream::connect_timeout(
        &SocketAddrV4::new(Ipv4Addr::LOCALHOST, port).into(),
        Duration::from_secs(1),
    ) else {
        return false;
    };
    let _ = stream.set_read_timeout(Some(Duration::from_secs(1)));
    let _ = stream.set_write_timeout(Some(Duration::from_secs(1)));
    if write!(
        stream,
        "GET /api/session HTTP/1.1\r\nHost: 127.0.0.1:{port}\r\nConnection: close\r\n\r\n"
    )
    .is_err()
    {
        return false;
    }
    let mut bytes = Vec::new();
    if stream.take(16385).read_to_end(&mut bytes).is_err() || bytes.len() > 16384 {
        return false;
    }
    let Ok(response) = std::str::from_utf8(&bytes) else {
        return false;
    };
    if !response.starts_with("HTTP/1.1 200 ") {
        return false;
    }
    let Some(body) = response.split("\r\n\r\n").nth(1) else {
        return false;
    };
    serde_json::from_str::<Value>(body).is_ok_and(|value| {
        value["implementation"] == "rust"
            && value["version"] == crate::VERSION
            && value["build_id"] == crate::BUILD_ID
            && value["preview"] == false
    })
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

#[cfg(test)]
mod tests {
    use super::*;
    use crate::{
        contracts::{Memory, Sweep},
        system::System,
    };
    use std::sync::atomic::{AtomicUsize, Ordering};

    struct ToolSystem {
        actions: AtomicUsize,
        selected_count: AtomicUsize,
        partial: bool,
    }
    impl System for ToolSystem {
        fn supported(&self) -> bool {
            true
        }
        fn snapshot(&self) -> AppResult<Memory> {
            Ok(Memory {
                total_bytes: 1000,
                available_bytes: 500,
                ..Memory::default()
            })
        }
        fn targets(&self) -> AppResult<Vec<Target>> {
            Ok(vec![])
        }
        fn cleanup(&self, targets: Option<&[Target]>, _: &AtomicBool) -> AppResult<Sweep> {
            self.actions.fetch_add(1, Ordering::SeqCst);
            self.selected_count
                .store(targets.map_or(0, <[Target]>::len), Ordering::SeqCst);
            Ok(Sweep {
                attempted_count: 1 + usize::from(self.partial),
                successful_count: 1,
                failed_count: usize::from(self.partial),
                resources_closed: true,
                ..Sweep::default()
            })
        }
    }
    fn tool_app(partial: bool) -> (Arc<ToolSystem>, Application) {
        let system = Arc::new(ToolSystem {
            actions: AtomicUsize::new(0),
            selected_count: AtomicUsize::new(0),
            partial,
        });
        (system.clone(), Application::new(system))
    }
    fn tool_options(name: &str) -> HashMap<String, String> {
        HashMap::from([
            ("--tool".into(), name.into()),
            ("--confirm".into(), "true".into()),
        ])
    }
    #[test]
    fn tool_cleanup_defaults_to_stdout_without_history() {
        let (system, app) = tool_app(false);
        let report = run_tool(&tool_options("memory_cleanup"), &app).unwrap();
        assert_eq!(report.completion, "success");
        assert_eq!(system.actions.load(Ordering::SeqCst), 1);
        assert_eq!(system.selected_count.load(Ordering::SeqCst), 0);
        assert!(report.output_dir.is_none());
        assert_eq!(report.metadata["timing"]["scope"], "business_stdout_only");
        assert!(
            report.metadata["timing"]["stages_ms"]
                .get("record_intent")
                .is_none()
        );
        assert!(
            report.metadata["timing"]["stages_ms"]
                .get("record_action")
                .is_none()
        );
        assert!(
            report.metadata["timing"]["stages_ms"]
                .get("record_final")
                .is_none()
        );
    }
    #[test]
    fn targeted_tool_also_returns_without_a_history_directory() {
        let (system, app) = tool_app(false);
        let mut options = tool_options("memory_process_cleanup");
        options.insert("--pid".into(), "42".into());
        options.insert("--created-at".into(), "test-identity".into());
        options.insert("--no-record".into(), "true".into());
        let report = run_tool(&options, &app).unwrap();
        assert!(report.output_dir.is_none());
        assert_eq!(report.mode, "batch");
        assert_eq!(system.actions.load(Ordering::SeqCst), 1);
        assert_eq!(system.selected_count.load(Ordering::SeqCst), 1);
    }
    #[test]
    fn batch_tool_reads_its_request_without_creating_result_files() {
        let (system, app) = tool_app(false);
        let directory = std::env::temp_dir().join(format!(
            "memory-tool-input-{}",
            crate::contracts::identifier().unwrap()
        ));
        std::fs::create_dir(&directory).unwrap();
        let request = directory.join("request.json");
        std::fs::write(
            &request,
            br#"{"targets":[{"pid":42,"created_at":"test-identity"}]}"#,
        )
        .unwrap();
        let mut options = tool_options("memory_batch_cleanup");
        options.insert(
            "--request-file".into(),
            request.to_string_lossy().into_owned(),
        );
        let report = run_tool(&options, &app).unwrap();
        assert!(report.output_dir.is_none());
        assert_eq!(system.actions.load(Ordering::SeqCst), 1);
        assert_eq!(system.selected_count.load(Ordering::SeqCst), 1);
        assert_eq!(std::fs::read_dir(&directory).unwrap().count(), 1);
        std::fs::remove_file(request).unwrap();
        std::fs::remove_dir(directory).unwrap();
    }
    #[test]
    fn partial_tool_outcome_is_returned_without_creating_history() {
        let (system, app) = tool_app(true);
        let report = run_tool(&tool_options("memory_cleanup"), &app).unwrap();
        assert_eq!(report.completion, "partial");
        assert_eq!(report.action["process_sweep"]["failed_count"], 1);
        assert!(report.output_dir.is_none());
        assert_eq!(report.samples.len(), 2);
        assert_eq!(system.actions.load(Ordering::SeqCst), 1);
        assert!(report.metadata.get("timing_record_error").is_none());
    }
    #[test]
    fn no_record_conflicts_with_a_save_directory_before_dispatch() {
        // Missing confirmation would block dispatch even if argument validation regressed.
        let error = execute(vec![
            "--tool".into(),
            "memory_cleanup".into(),
            "--no-record".into(),
            "--output-dir".into(),
            "unused".into(),
        ])
        .unwrap_err();
        assert!(error.contains("互斥"));
    }
    #[test]
    fn catalog_declares_stdout_only_default_and_explicit_no_record_guarantee() {
        assert_eq!(catalog()["result_persistence"]["default"], "stdout_only");
        assert_eq!(
            catalog()["result_persistence"]["no_record_option"],
            "--no-record"
        );
    }
    #[test]
    fn only_matching_real_rust_service_is_reused() {
        use std::io::{Read, Write};
        for (body, expected) in [
            (
                serde_json::json!({"implementation":"rust","version":crate::VERSION,"build_id":crate::BUILD_ID,"preview":false}),
                true,
            ),
            (
                serde_json::json!({"implementation":"python","version":crate::VERSION,"preview":false}),
                false,
            ),
            (
                serde_json::json!({"implementation":"rust","version":"old","preview":false}),
                false,
            ),
            (
                serde_json::json!({"implementation":"rust","version":crate::VERSION,"build_id":"old-build","preview":false}),
                false,
            ),
        ] {
            let listener = std::net::TcpListener::bind(("127.0.0.1", 0)).unwrap();
            let port = listener.local_addr().unwrap().port();
            let worker = std::thread::spawn(move || {
                let (mut stream, _) = listener.accept().unwrap();
                stream
                    .set_read_timeout(Some(std::time::Duration::from_secs(2)))
                    .unwrap();
                let mut request = Vec::new();
                while !request.ends_with(b"\r\n\r\n") {
                    let mut byte = [0u8; 1];
                    stream.read_exact(&mut byte).unwrap();
                    request.push(byte[0]);
                    assert!(request.len() < 4096);
                }
                assert!(
                    std::str::from_utf8(&request)
                        .unwrap()
                        .starts_with("GET /api/session")
                );
                let text = body.to_string();
                write!(
                    stream,
                    "HTTP/1.1 200 OK\r\nContent-Length: {}\r\nConnection: close\r\n\r\n{text}",
                    text.len()
                )
                .unwrap();
            });
            assert_eq!(super::running_workbench(port), expected);
            worker.join().unwrap();
        }
    }
}
