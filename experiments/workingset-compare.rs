//! Developer-only interactive entry. Never invoked by the normal product.
use cpu_memory_cleanup::{application::comparison, contracts::AppResult};
#[cfg(windows)]
use std::io::{self, Write};
use std::path::Path;

fn main() {
    if let Err(error) = run(std::env::args().skip(1).collect()) {
        eprintln!("{error}");
        std::process::exit(2);
    }
}
fn run(args: Vec<String>) -> AppResult<()> {
    match args.first().map(String::as_str).unwrap_or("plan") {
        "plan" if args.len() <= 1 => {
            println!(
                "{}",
                serde_json::json!({"schema_version":1,"static_only":true,
                "commands":["plan","compare VERIFIED_JSON QUOTA_JSON","session owned|global MIB NEW_DIRECTORY"],
                "scope":"owned仅当前自有进程；global为会话建立时的固定全局PID清单",
                "budget":"负载1..256MiB，每方法每会话至多一次，规则关闭，不自动连跑",
                "confirmation":"会话输入PREPARE作用域才分配负载和读取；每次动作输入RUN方法名才维护",
                "limits":"固定全局清单不恢复其他应用，不加入新进程。仅离线比较不读系统或重放"})
            );
            Ok(())
        }
        "compare" if args.len() == 3 => {
            let a = comparison::read_evidence(Path::new(&args[1]))?;
            let b = comparison::read_evidence(Path::new(&args[2]))?;
            println!(
                "{}",
                serde_json::to_string_pretty(&comparison::compare(&a, &b)?)
                    .map_err(|e| e.to_string())?
            );
            Ok(())
        }
        "session" if args.len() == 4 => session(&args[1], &args[2], Path::new(&args[3])),
        _ => Err("参数无效，请先运行plan；没有执行读取或清理".into()),
    }
}
#[cfg(windows)]
fn confirmation(prompt: &str) -> AppResult<String> {
    print!("{prompt}\n> ");
    io::stdout().flush().map_err(|e| e.to_string())?;
    let mut line = String::new();
    io::stdin()
        .read_line(&mut line)
        .map_err(|e| e.to_string())?;
    Ok(line.trim().to_string())
}
#[cfg(windows)]
fn session(scope: &str, mib: &str, directory: &Path) -> AppResult<()> {
    use comparison::{Method, ResidentLoad, Scope, Session};
    use cpu_memory_cleanup::{Application, system::windows};
    use std::sync::Arc;
    let scope_kind = match scope {
        "owned" => Scope::Owned,
        "global" => Scope::GlobalFixed,
        _ => return Err("作用域必须为owned或global".into()),
    };
    let mib: usize = mib.parse().map_err(|_| "MiB需要整数")?;
    if !(1..=256).contains(&mib) || directory.exists() {
        return Err("预算1..256MiB，需要新的输出目录".into());
    }
    if confirmation(&format!(
        "尚未读取或维护。将分配{mib}MiB自有负载并建立{scope}范围清单。输入PREPARE {scope}继续，否则退出。"
    ))? != format!("PREPARE {scope}")
    {
        return Ok(());
    }
    let mut pids = if scope_kind == Scope::Owned {
        vec![std::process::id()]
    } else {
        windows::comparison_pids()?
    };
    pids.sort_unstable();
    let mut session = Session::create(directory, scope_kind, pids.clone(), mib * 1024 * 1024)?;
    let mut load = ResidentLoad::new(mib * 1024 * 1024)?;
    println!(
        "会话已准备，未执行整理。固定{}个PID。输入RUN verified或RUN quota执行对应一轮，quit退出。",
        pids.len()
    );
    loop {
        let line = confirmation("每种方法只允许一次，部分结果或失败不重试。")?;
        let method = match line.as_str() {
            "RUN verified" => Method::Verified,
            "RUN quota" => Method::Quota,
            "quit" | "" => break,
            _ => {
                println!("没有派发，输入RUN verified、RUN quota或quit。");
                continue;
            }
        };
        let system = windows::comparison_system(pids.clone(), method)?;
        let app = Application::new(Arc::new(system));
        match session.run_once(method, true, &app, &mut load, || {
            windows::comparison_observe(&pids)
        }) {
            Ok(evidence) => println!(
                "{}已保存，原始终态：{}。没有自动派发下一方法。",
                method.name(),
                evidence
                    .report
                    .as_ref()
                    .map(|r| r.completion.as_str())
                    .unwrap_or("未取得报告")
            ),
            Err(error) => {
                eprintln!("{error}。本方法不自动重试，检查started与before记录。");
            }
        }
    }
    Ok(())
}
#[cfg(not(windows))]
fn session(_: &str, _: &str, _: &Path) -> AppResult<()> {
    Err("真实对照只实现Windows，未采样或清理".into())
}
