use crate::{
    Application,
    application::{
        policy::{Policy, Protection, Rule, Settings},
        service::{brief, presentation},
    },
    contracts::{AppResult, Report, Target, identifier},
};
use serde::Deserialize;
use serde_json::{Value, json};
use std::{
    collections::{HashMap, VecDeque},
    fs,
    io::Read,
    path::PathBuf,
    sync::{
        Arc, Mutex,
        atomic::{AtomicBool, Ordering},
    },
    thread,
    time::{Duration, Instant},
};
use tiny_http::{Header, Method, Request, Response, Server, StatusCode};

#[derive(Clone, Deserialize)]
#[serde(tag = "kind", deny_unknown_fields)]
enum Action {
    #[serde(rename = "status")]
    Status,
    #[serde(rename = "targets")]
    Targets,
    #[serde(rename = "cleanup")]
    Cleanup { mechanism: String, confirmed: bool },
    #[serde(rename = "batch")]
    Batch {
        targets: Vec<String>,
        confirmed: bool,
    },
    #[serde(rename = "replay")]
    Replay { source_job: String },
}
enum Work {
    Status,
    Targets,
    Cleanup,
    Batch(Vec<Target>, Vec<Protection>),
    Replay(PathBuf),
}
struct Job {
    kind: String,
    state: String,
    report: Option<Arc<Report>>,
    error: Option<String>,
}
struct Inner {
    jobs: HashMap<String, Job>,
    order: VecDeque<String>,
    targets: HashMap<String, Target>,
    settings: Settings,
    policy: Policy,
    generation: u64,
    active: Option<String>,
    cancel: Arc<AtomicBool>,
}
struct State {
    app: Arc<Application>,
    output: PathBuf,
    token: String,
    port: u16,
    inner: Mutex<Inner>,
    stopping: AtomicBool,
    tasks: Mutex<Vec<thread::JoinHandle<()>>>,
}
pub struct Workbench {
    server: Arc<Server>,
    state: Arc<State>,
}
impl Workbench {
    pub fn bind(app: Arc<Application>, output: PathBuf, port: u16) -> AppResult<Self> {
        let server = Server::http(("127.0.0.1", port)).map_err(|e| e.to_string())?;
        let port = server
            .server_addr()
            .to_ip()
            .ok_or("不是回环TCP地址")?
            .port();
        let preferences = output.join("preferences.json");
        let settings: Settings = if preferences.exists() {
            serde_json::from_slice(&fs::read(preferences).map_err(|e| e.to_string())?)
                .map_err(|e| format!("设置损坏：{e}"))?
        } else {
            Settings::default()
        };
        settings.validate()?;
        Ok(Self {
            server: Arc::new(server),
            state: Arc::new(State {
                app,
                output,
                token: identifier()?,
                port,
                inner: Mutex::new(Inner {
                    jobs: HashMap::new(),
                    order: VecDeque::new(),
                    targets: HashMap::new(),
                    settings,
                    policy: Policy::default(),
                    generation: 0,
                    active: None,
                    cancel: Arc::new(AtomicBool::new(false)),
                }),
                stopping: AtomicBool::new(false),
                tasks: Mutex::new(Vec::new()),
            }),
        })
    }
    pub fn url(&self) -> String {
        format!("http://127.0.0.1:{}/", self.state.port)
    }
    pub fn serve(self) -> AppResult<()> {
        let mut workers = Vec::new();
        for _ in 0..4 {
            let server = self.server.clone();
            let state = self.state.clone();
            workers.push(thread::spawn(move || {
                while !state.stopping.load(Ordering::Acquire) {
                    match server.recv_timeout(Duration::from_millis(200)) {
                        Ok(Some(request)) => handle(request, &state),
                        Ok(None) => {}
                        Err(_) => break,
                    }
                }
            }));
        }
        let rule_state = self.state.clone();
        let rule_thread = thread::spawn(move || {
            while !rule_state.stopping.load(Ordering::Acquire) {
                rule_state.tick_rule();
                thread::sleep(Duration::from_secs(1));
            }
        });
        for worker in workers {
            worker.join().map_err(|_| "HTTP工作线程异常")?;
        }
        rule_thread.join().map_err(|_| "规则线程异常")?;
        let tasks = std::mem::take(&mut *self.state.tasks.lock().map_err(|_| "任务锁损坏")?);
        for task in tasks {
            task.join().map_err(|_| "业务工作线程异常")?;
        }
        Ok(())
    }
}
impl State {
    fn state(&self) -> Value {
        let inner = self.inner.lock().expect("state lock");
        json!({"settings":inner.settings,"busy":inner.active.is_some(),"rule":{"armed":inner.policy.armed,"target_count":inner.policy.targets.len(),"last_job":inner.policy.last_job,"error":inner.policy.error}})
    }
    fn submit(self: &Arc<Self>, action: Action) -> AppResult<String> {
        let inner = self.inner.lock().map_err(|_| "任务锁损坏")?;
        if self.stopping.load(Ordering::Acquire) {
            return Err("服务正在停止".into());
        }
        let work = match action {
            Action::Status => Work::Status,
            Action::Targets => Work::Targets,
            Action::Cleanup {
                mechanism,
                confirmed,
            } => {
                if mechanism != "working-set" || !confirmed {
                    return Err("仅支持明确确认的工作集整理".into());
                }
                Work::Cleanup
            }
            Action::Batch { targets, confirmed } => {
                if !confirmed {
                    return Err("批次需要本次明确确认".into());
                }
                Work::Batch(
                    resolve(&inner, &targets)?,
                    inner.settings.protections.clone(),
                )
            }
            Action::Replay { source_job } => {
                let job = inner.jobs.get(&source_job).ok_or("本会话没有该记录")?;
                let directory = job
                    .report
                    .as_ref()
                    .and_then(|r| r.output_dir.as_ref())
                    .ok_or("该任务没有封存记录")?;
                Work::Replay(PathBuf::from(directory))
            }
        };
        drop(inner);
        self.start(work, None)
    }
    fn start(self: &Arc<Self>, work: Work, automatic: Option<u64>) -> AppResult<String> {
        let mut inner = self.inner.lock().map_err(|_| "任务锁损坏")?;
        if self.stopping.load(Ordering::Acquire) || inner.active.is_some() {
            return Err("当前任务尚未收尾".into());
        }
        if automatic.is_some_and(|generation| !inner.policy.armed || generation != inner.generation)
        {
            return Err("规则已停用或替换".into());
        }
        let kind = match work {
            Work::Status => "status",
            Work::Targets => "targets",
            Work::Cleanup => "cleanup",
            Work::Batch(..) => "batch",
            Work::Replay(_) => "replay",
        }
        .to_string();
        let id = identifier()?;
        let cancel = Arc::new(AtomicBool::new(false));
        inner.cancel = cancel.clone();
        inner.active = Some(id.clone());
        inner.jobs.insert(
            id.clone(),
            Job {
                kind: kind.clone(),
                state: "running".into(),
                report: None,
                error: None,
            },
        );
        inner.order.push_back(id.clone());
        while inner.order.len() > 32 {
            if let Some(old) = inner.order.pop_front() {
                inner.jobs.remove(&old);
            }
        }
        if automatic.is_some() {
            inner.policy.dispatched(Instant::now());
            inner.policy.last_job = Some(id.clone());
        }
        drop(inner);
        let state = self.clone();
        let job_id = id.clone();
        let started = Instant::now();
        let worker = thread::spawn(move || {
            let result = match work {
                Work::Status => state.app.memory_status(),
                Work::Targets => state.app.memory_targets(),
                Work::Cleanup => state.app.memory_cleanup(&state.output, true, &cancel),
                Work::Batch(targets, protections) => state.app.memory_batch_cleanup(
                    &state.output,
                    &targets,
                    &protections,
                    true,
                    &cancel,
                ),
                Work::Replay(path) => state.app.memory_result(&path),
            };
            let mut inner = state.inner.lock().expect("state lock");
            match result {
                Ok(mut report) => {
                    report.metadata["web_timing"] = json!({"accepted_to_report_ms":started.elapsed().as_secs_f64()*1000.,"scope":"server_job","ui_render_ms":null});
                    if kind == "targets" {
                        inner.targets = report
                            .samples
                            .first()
                            .and_then(|sample| sample["processes"].as_array())
                            .into_iter()
                            .flatten()
                            .filter_map(|row| serde_json::from_value::<Target>(row.clone()).ok())
                            .map(|target| (target.key(), target))
                            .collect();
                    }
                    if automatic.is_some() && report.completion != "success" {
                        inner.policy.armed = false;
                        inner.policy.error = Some("规则动作未完整成功，已停用，不重试".into());
                    }
                    if let Some(job) = inner.jobs.get_mut(&job_id) {
                        job.state = "finished".into();
                        job.report = Some(Arc::new(report));
                    }
                }
                Err(error) => {
                    if let Some(job) = inner.jobs.get_mut(&job_id) {
                        job.state = "error".into();
                        job.error = Some(error);
                    }
                    if automatic.is_some() {
                        inner.policy.armed = false;
                        inner.policy.error = Some("规则执行失败，已停用".into());
                    }
                }
            }
            inner.active = None;
        });
        let mut tasks = self.tasks.lock().map_err(|_| "任务句柄锁损坏")?;
        tasks.retain(|task| !task.is_finished());
        tasks.push(worker);
        Ok(id)
    }
    fn job(&self, id: &str, full: bool) -> AppResult<Value> {
        let inner = self.inner.lock().map_err(|_| "任务锁损坏")?;
        let job = inner.jobs.get(id).ok_or("本会话没有该任务")?;
        let (kind, state, error, report) = (
            job.kind.clone(),
            job.state.clone(),
            job.error.clone(),
            job.report.clone(),
        );
        drop(inner);
        if full {
            return report
                .map(|r| serde_json::to_value(&*r).expect("report serialization"))
                .ok_or("报告尚未就绪".into());
        }
        let mut result = json!({"id":id,"kind":kind,"state":state});
        if let Some(error) = error {
            result["error"] = json!(error);
        }
        if let Some(report) = report {
            result["report"] = brief(&report);
            result["presentation"] = presentation(&report);
            result["full_report_url"] = json!(format!("/api/report?id={id}"));
        }
        Ok(result)
    }
    fn save_settings(&self, value: Value) -> AppResult<Value> {
        let settings: Settings = serde_json::from_value(value).map_err(|e| e.to_string())?;
        settings.validate()?;
        let mut inner = self.inner.lock().map_err(|_| "设置锁损坏")?;
        if inner.active.is_some() {
            return Err("任务执行时不能更改设置".into());
        }
        fs::create_dir_all(&self.output).map_err(|e| e.to_string())?;
        let temporary = self.output.join("preferences.tmp");
        fs::write(
            &temporary,
            serde_json::to_vec(&settings).map_err(|e| e.to_string())?,
        )
        .map_err(|e| e.to_string())?;
        fs::rename(temporary, self.output.join("preferences.json")).map_err(|e| e.to_string())?;
        inner.settings = settings;
        drop(inner);
        Ok(self.state())
    }
    fn rule(&self, value: Value) -> AppResult<Value> {
        #[derive(Deserialize)]
        #[serde(deny_unknown_fields)]
        struct Request {
            enabled: bool,
            #[serde(default)]
            confirmed: bool,
            #[serde(default)]
            targets: Vec<String>,
            rule: Option<Rule>,
        }
        let request: Request = serde_json::from_value(value).map_err(|e| e.to_string())?;
        let mut inner = self.inner.lock().map_err(|_| "规则锁损坏")?;
        if request.enabled {
            if !request.confirmed || inner.active.is_some() {
                return Err("启用规则需要明确确认且无任务执行".into());
            }
            let targets = resolve(&inner, &request.targets)?;
            let protections = inner.settings.protections.clone();
            inner.policy.arm(
                request.rule.ok_or("缺少规则参数")?,
                targets,
                protections,
                Instant::now(),
            )?;
        } else {
            inner.policy.armed = false;
        }
        inner.generation += 1;
        drop(inner);
        Ok(self.state())
    }
    fn tick_rule(self: &Arc<Self>) {
        let (generation, pressure) = {
            let inner = self.inner.lock().expect("state lock");
            if !inner.policy.armed || inner.active.is_some() {
                return;
            }
            (
                inner.generation,
                inner
                    .policy
                    .rule
                    .as_ref()
                    .is_some_and(|r| r.mode == "pressure"),
            )
        };
        let available = if pressure {
            match self.app.system.snapshot() {
                Ok(m) => Some(m.available_bytes as f64 / m.total_bytes as f64 * 100.),
                Err(error) => {
                    let mut inner = self.inner.lock().expect("state lock");
                    if inner.generation == generation {
                        inner.policy.armed = false;
                        inner.policy.error = Some(error);
                    }
                    return;
                }
            }
        } else {
            None
        };
        let work = {
            let mut inner = self.inner.lock().expect("state lock");
            if inner.generation != generation
                || inner.active.is_some()
                || !inner.policy.due(Instant::now(), available)
            {
                return;
            }
            Work::Batch(
                inner.policy.targets.clone(),
                inner.policy.protections.clone(),
            )
        };
        let _ = self.start(work, Some(generation));
    }
}
fn resolve(inner: &Inner, keys: &[String]) -> AppResult<Vec<Target>> {
    if keys.is_empty() || keys.len() > 4096 {
        return Err("需要明确选择1..4096个目标".into());
    }
    let mut seen = std::collections::HashSet::new();
    keys.iter()
        .map(|key| {
            if !seen.insert(key) {
                return Err("重复目标".into());
            }
            inner
                .targets
                .get(key)
                .cloned()
                .ok_or("选择已失效，请重新读取列表".into())
        })
        .collect()
}
fn header<'a>(request: &'a Request, name: &'static str) -> Option<&'a str> {
    request
        .headers()
        .iter()
        .find(|h| h.field.equiv(name))
        .map(|h| h.value.as_str())
}
fn handle(mut request: Request, state: &Arc<State>) {
    let authority = header(&request, "Host").unwrap_or("").to_string();
    let valid_host = authority == format!("127.0.0.1:{}", state.port)
        || authority == format!("localhost:{}", state.port);
    let valid_origin =
        header(&request, "Origin").is_none_or(|origin| origin == format!("http://{authority}"));
    if !valid_host || !valid_origin {
        respond(request, 403, json!({"error":"仅接受本地同源请求"}));
        return;
    }
    let url = request.url().to_string();
    let path = url.split('?').next().unwrap_or("");
    if request.method() == &Method::Get {
        let asset = match path {
            "/" => Some((
                include_bytes!("web_assets/index.html").as_slice(),
                "text/html; charset=utf-8",
            )),
            "/app.js" => Some((
                include_bytes!("web_assets/app.js").as_slice(),
                "text/javascript; charset=utf-8",
            )),
            "/cleanup-flow.js" => Some((
                include_bytes!("web_assets/cleanup-flow.js").as_slice(),
                "text/javascript; charset=utf-8",
            )),
            "/memory-display.js" => Some((
                include_bytes!("web_assets/memory-display.js").as_slice(),
                "text/javascript; charset=utf-8",
            )),
            "/styles.css" => Some((
                include_bytes!("web_assets/styles.css").as_slice(),
                "text/css; charset=utf-8",
            )),
            _ => None,
        };
        if let Some((bytes, mime)) = asset {
            respond_bytes(request, 200, bytes.to_vec(), mime);
            return;
        }
        if path == "/api/session" {
            let supported = state.app.system.supported();
            respond(
                request,
                200,
                json!({"token":state.token,"preview":false,"implementation":"rust","version":crate::VERSION,"build_id":crate::BUILD_ID,"operating_system":std::env::consts::OS,"reading":if supported{"available"}else{"unsupported"},"capabilities":{"mechanisms":if supported{vec!["working-set"]}else{vec![]},"scopes":["system","process"]}}),
            );
            return;
        }
    }
    if header(&request, "X-Session-Token") != Some(state.token.as_str()) {
        respond(request, 403, json!({"error":"会话无效，请刷新本地页面"}));
        return;
    }
    let result = if request.method() == &Method::Get {
        match path {
            "/api/state" => Ok(state.state()),
            "/api/job" | "/api/report" => url
                .strip_prefix(&format!("{path}?id="))
                .ok_or("缺少任务ID".into())
                .and_then(|id| state.job(id, path == "/api/report")),
            _ => Err("没有该入口".into()),
        }
    } else if request.method() == &Method::Post {
        let mut body = Vec::new();
        let read = request
            .as_reader()
            .take(1024 * 1024 + 1)
            .read_to_end(&mut body);
        if read.is_err() || body.len() > 1024 * 1024 {
            respond(request, 400, json!({"error":"请求读取失败或超过1MiB"}));
            return;
        }
        let value: Value = match serde_json::from_slice(&body) {
            Ok(value) => value,
            Err(error) => {
                respond(request, 400, json!({"error":error.to_string()}));
                return;
            }
        };
        match path {
            "/api/action" => serde_json::from_slice::<Action>(&body)
                .map_err(|e| e.to_string())
                .and_then(|action| state.submit(action))
                .map(|id| json!({"id":id,"state":"running"})),
            "/api/display" if value == json!({}) => state
                .app
                .memory_status()
                .and_then(|report| serde_json::to_value(report).map_err(|e| e.to_string())),
            "/api/settings" => state.save_settings(value),
            "/api/rule" => state.rule(value),
            "/api/cancel" => {
                let inner = state.inner.lock().expect("state lock");
                if value["id"].as_str() == inner.active.as_deref() {
                    inner.cancel.store(true, Ordering::Release);
                    Ok(json!({"accepted":true}))
                } else {
                    Err("当前没有该运行中任务".into())
                }
            }
            "/api/shutdown" if value == json!({"confirmed":true}) => {
                let mut inner = state.inner.lock().expect("state lock");
                inner.policy.armed = false;
                inner.cancel.store(true, Ordering::Release);
                state.stopping.store(true, Ordering::Release);
                Ok(json!({"stopping":true}))
            }
            _ => Err("没有该入口或请求字段无效".into()),
        }
    } else {
        Err("不支持该请求方法".into())
    };
    match result {
        Ok(value) => respond(request, 200, value),
        Err(error) => respond(request, 400, json!({"error":error})),
    }
}
fn respond(request: Request, status: u16, value: Value) {
    respond_bytes(
        request,
        status,
        serde_json::to_vec(&value).expect("json response"),
        "application/json; charset=utf-8",
    );
}
fn respond_bytes(request: Request, status: u16, bytes: Vec<u8>, mime: &str) {
    let mut response = Response::from_data(bytes).with_status_code(StatusCode(status));
    for (name, value) in [
        ("Content-Type", mime),
        ("Cache-Control", "no-store"),
        ("X-Content-Type-Options", "nosniff"),
        ("Referrer-Policy", "no-referrer"),
        (
            "Content-Security-Policy",
            "default-src 'self'; frame-ancestors 'none'; object-src 'none'; base-uri 'none'",
        ),
    ] {
        response.add_header(
            Header::from_bytes(name.as_bytes(), value.as_bytes()).expect("static header"),
        );
    }
    let _ = request.respond(response);
}
