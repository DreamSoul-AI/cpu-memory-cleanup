//! Optional Windows WebView shell; business operations remain in Application.
use crate::{
    Application,
    contracts::AppResult,
    interaction::web::{Workbench, WorkbenchControl},
};
use std::{
    path::PathBuf,
    sync::Arc,
    thread,
    time::{Duration, Instant},
};
use tao::{
    event::{Event, WindowEvent},
    event_loop::{ControlFlow, EventLoopBuilder},
    platform::run_return::EventLoopExtRunReturn,
    window::WindowBuilder,
};
use wry::{WebContext, WebViewBuilder};

#[derive(Debug)]
enum Message {
    Stopped(AppResult<()>),
    Read(serde_json::Value),
}
struct Service {
    control: WorkbenchControl,
    worker: Option<thread::JoinHandle<()>>,
}
impl Drop for Service {
    fn drop(&mut self) {
        self.control.request_stop();
        if let Some(worker) = self.worker.take() {
            let _ = worker.join();
        }
    }
}
struct Instance(windows_sys::Win32::Foundation::HANDLE);
impl Drop for Instance {
    fn drop(&mut self) {
        unsafe {
            windows_sys::Win32::Foundation::CloseHandle(self.0);
        }
    }
}
fn wide(text: &str) -> Vec<u16> {
    text.encode_utf16().chain(Some(0)).collect()
}
fn single_instance(port: u16, title: &str, quiet: bool) -> AppResult<Option<Instance>> {
    use windows_sys::Win32::{
        Foundation::{ERROR_ALREADY_EXISTS, GetLastError},
        System::Threading::CreateMutexW,
        UI::WindowsAndMessaging::{FindWindowW, SW_RESTORE, SetForegroundWindow, ShowWindow},
    };
    let name = wide(&format!("Local\\DreamSoul.MemoryWindow.{port}"));
    let handle = unsafe { CreateMutexW(std::ptr::null(), 0, name.as_ptr()) };
    if handle.is_null() {
        return Err("无法建立窗口实例标识".into());
    }
    let guard = Instance(handle);
    if unsafe { GetLastError() } == ERROR_ALREADY_EXISTS {
        if !quiet {
            let title = wide(title);
            let window = unsafe { FindWindowW(std::ptr::null(), title.as_ptr()) };
            if window.is_null() {
                return Err("同端口窗口正在启动或使用另一构建，请先关闭它".into());
            }
            unsafe {
                ShowWindow(window, SW_RESTORE);
                SetForegroundWindow(window);
            }
        }
        return Ok(None);
    }
    Ok(Some(guard))
}

pub fn run(args: Vec<String>) -> AppResult<()> {
    let mut port = 14801u16;
    let mut output =
        PathBuf::from(std::env::var_os("LOCALAPPDATA").ok_or("LOCALAPPDATA unavailable")?)
            .join("DreamSoul/CPU Memory Cleanup/runs");
    let mut smoke = None;
    let mut options = args.chunks_exact(2);
    for pair in &mut options {
        match pair[0].as_str() {
            "--port" => {
                port = pair[1].parse().map_err(|_| "Invalid port")?;
                if port == 0 {
                    return Err("Port must be nonzero".into());
                }
            }
            "--output-dir" => output = PathBuf::from(&pair[1]),
            "--smoke-test" => smoke = Some(PathBuf::from(&pair[1])),
            _ => return Err(format!("Unknown window option: {}", pair[0])),
        }
    }
    if !options.remainder().is_empty() {
        return Err("Window options require a value".into());
    }
    if smoke.as_ref().is_some_and(|p| p.exists()) {
        return Err("Smoke receipt already exists".into());
    }
    let title = format!("内存清理 · {}", crate::BUILD_ID);
    let Some(_instance) = single_instance(port, &title, smoke.is_some())? else {
        return Ok(());
    };
    let server = Workbench::bind(Arc::new(Application::default()), output, port)?;
    let base = server.url();
    let mut events = EventLoopBuilder::<Message>::with_user_event().build();
    let proxy = events.create_proxy();
    let service = Service {
        control: server.control(),
        worker: Some(thread::spawn(move || {
            let _ = proxy.send_event(Message::Stopped(server.serve()));
        })),
    };
    let window = WindowBuilder::new()
        .with_title(&title)
        .with_inner_size(tao::dpi::LogicalSize::new(620.0, 840.0))
        .with_min_inner_size(tao::dpi::LogicalSize::new(420.0, 600.0))
        .with_visible(smoke.is_none())
        .build(&events)
        .map_err(|e| e.to_string())?;
    let allowed = base.clone();
    let profile =
        PathBuf::from(std::env::var_os("LOCALAPPDATA").ok_or("LOCALAPPDATA unavailable")?)
            .join("DreamSoul/CPU Memory Cleanup/webview-profile");
    let mut context = WebContext::new(Some(profile));
    let mut builder = WebViewBuilder::new_with_web_context(&mut context)
        .with_url(&base)
        .with_navigation_handler(move |url| url.starts_with(&allowed))
        .with_new_window_req_handler(|_, _| wry::NewWindowResponse::Deny);
    if smoke.is_some() {
        let proxy = events.create_proxy();
        builder = builder.with_initialization_script(r#"window.addEventListener('DOMContentLoaded',()=>{const t=setInterval(()=>{const actual=document.getElementById('actual-percent')?.textContent; const capacity=document.getElementById('capacity')?.textContent; if(actual?.endsWith('%') && capacity?.includes('GiB')){clearInterval(t);window.ipc.postMessage(JSON.stringify({actual,capacity,recent:document.getElementById('recent-title').textContent,rules:document.getElementById('rule-state').textContent}));}},100);});"#).with_ipc_handler(move |request| { if let Ok(value) = serde_json::from_str(request.body()) { let _ = proxy.send_event(Message::Read(value)); } });
    }
    let _view = builder.build(&window).map_err(|e| e.to_string())?;
    let deadline = Instant::now() + Duration::from_secs(20);
    let mut failure = None;
    let mut read = None;
    let mut stopping = false;
    events.run_return(|event, _, flow| {
        *flow = if smoke.is_some() {
            ControlFlow::WaitUntil(Instant::now() + Duration::from_millis(100))
        } else {
            ControlFlow::Wait
        };
        match event {
            Event::WindowEvent {
                event: WindowEvent::CloseRequested,
                ..
            } => {
                service.control.request_stop();
                stopping = true;
                window.set_title("内存清理 · 正在收尾");
            }
            Event::UserEvent(Message::Read(value)) => {
                read = Some(value);
                service.control.request_stop();
                stopping = true;
            }
            Event::UserEvent(Message::Stopped(result)) => {
                if let Err(error) = result {
                    failure = Some(error);
                }
                *flow = ControlFlow::Exit;
            }
            Event::MainEventsCleared
                if smoke.is_some() && !stopping && Instant::now() >= deadline =>
            {
                failure = Some("Window readonly smoke timed out".into());
                service.control.request_stop();
                stopping = true;
            }
            _ => {}
        }
    });
    drop(service);
    if let Some(error) = failure {
        return Err(error);
    }
    if let Some(path) = smoke {
        let reading = read.ok_or("No actual WebView reading was observed")?;
        let receipt = serde_json::json!({"version":crate::VERSION,"build_id":crate::BUILD_ID,"window":"WebView2","url":base,"reading":reading,"service_stopped":true,"maintenance_dispatched":false});
        std::fs::write(
            path,
            serde_json::to_vec_pretty(&receipt).map_err(|e| e.to_string())?,
        )
        .map_err(|e| e.to_string())?;
    }
    Ok(())
}
