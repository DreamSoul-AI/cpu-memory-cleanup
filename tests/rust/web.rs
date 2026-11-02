use cpu_memory_cleanup::{
    Application,
    contracts::{AppResult, Memory, Sweep, Target},
    interaction::web::Workbench,
    system::System,
};
use std::{
    io::{Read, Write},
    net::TcpStream,
    sync::{Arc, atomic::AtomicBool},
};
struct NoSystem;
impl System for NoSystem {
    fn supported(&self) -> bool {
        true
    }
    fn snapshot(&self) -> AppResult<Memory> {
        panic!("static web tests must not sample")
    }
    fn targets(&self) -> AppResult<Vec<Target>> {
        panic!("static web tests must not enumerate")
    }
    fn cleanup(&self, _: Option<&[Target]>, _: &AtomicBool) -> AppResult<Sweep> {
        panic!("web test must not clean")
    }
}
fn request(address: &str, method: &str, path: &str, headers: &str, body: &str) -> String {
    let mut stream = TcpStream::connect(address).unwrap();
    stream
        .set_read_timeout(Some(std::time::Duration::from_secs(5)))
        .unwrap();
    write!(stream,"{method} {path} HTTP/1.1\r\nHost: {address}\r\nConnection: close\r\nContent-Length: {}\r\n{headers}\r\n{body}",body.len()).unwrap();
    let mut output = String::new();
    stream.read_to_string(&mut output).unwrap();
    output
}
#[test]
fn server_is_native_readonly_on_start_and_requires_session_for_actions() {
    let app = Arc::new(Application::new(Arc::new(NoSystem)));
    let server = Workbench::bind(app, std::path::PathBuf::from("unused"), 0).unwrap();
    let address = server
        .url()
        .trim_start_matches("http://")
        .trim_end_matches('/')
        .to_string();
    let worker = std::thread::spawn(move || server.serve().unwrap());
    let response = request(&address, "GET", "/api/session", "", "");
    assert!(response.starts_with("HTTP/1.1 200"));
    let session: serde_json::Value =
        serde_json::from_str(response.split("\r\n\r\n").nth(1).unwrap()).unwrap();
    assert_eq!(session["implementation"], "rust");
    assert_eq!(session["preview"], false);
    assert_eq!(session["build_id"], cpu_memory_cleanup::BUILD_ID);
    let token = session["token"].as_str().unwrap();
    assert!(request(&address, "POST", "/api/action", "", "{}").starts_with("HTTP/1.1 403"));
    let headers = format!("X-Session-Token: {token}\r\n");
    let state = request(&address, "GET", "/api/state", &headers, "");
    assert!(state.contains("\"armed\":false"));
    let forbidden = request(
        &address,
        "POST",
        "/api/display",
        &format!("{headers}Origin: http://untrusted.example\r\n"),
        "{}",
    );
    assert!(forbidden.starts_with("HTTP/1.1 403"));
    assert!(
        request(
            &address,
            "POST",
            "/api/shutdown",
            &headers,
            "{\"confirmed\":true}"
        )
        .starts_with("HTTP/1.1 200")
    );
    worker.join().unwrap();
}

#[test]
fn http_job_keeps_live_reads_available_and_returns_full_facts_on_demand() {
    use serde_json::{Value, json};
    use std::sync::{
        Condvar, Mutex,
        atomic::{AtomicUsize, Ordering},
    };
    struct Controlled {
        reads: AtomicUsize,
        calls: AtomicUsize,
        entered: (Mutex<bool>, Condvar),
        release: (Mutex<bool>, Condvar),
    }
    impl System for Controlled {
        fn supported(&self) -> bool {
            true
        }
        fn snapshot(&self) -> AppResult<Memory> {
            self.reads.fetch_add(1, Ordering::SeqCst);
            Ok(Memory {
                total_bytes: 1000,
                available_bytes: 300,
                commit_used_bytes: 500,
                commit_limit_bytes: 2000,
            })
        }
        fn targets(&self) -> AppResult<Vec<Target>> {
            Ok(vec![Target {
                pid: 42,
                created_at: "100".into(),
                name: "isolated".into(),
                image_path: None,
                working_set_bytes: Some(70),
            }])
        }
        fn cleanup(&self, selected: Option<&[Target]>, _: &AtomicBool) -> AppResult<Sweep> {
            assert_eq!(selected.unwrap()[0].key(), "42:100");
            self.calls.fetch_add(1, Ordering::SeqCst);
            *self.entered.0.lock().unwrap() = true;
            self.entered.1.notify_all();
            let released = self.release.0.lock().unwrap();
            let (_released, timeout) = self
                .release
                .1
                .wait_timeout_while(released, std::time::Duration::from_secs(5), |v| !*v)
                .unwrap();
            assert!(!timeout.timed_out(), "test must release isolated cleanup");
            Ok(Sweep {
                enumerated_count: 1,
                visited_count: 1,
                attempted_count: 1,
                successful_count: 1,
                resources_closed: true,
                items: vec![
                    json!({"pid":42,"outcome":"success","working_set_before_bytes":70,"working_set_after_bytes":20}),
                ],
                ..Default::default()
            })
        }
    }
    fn parsed(response: &str) -> Value {
        assert!(response.starts_with("HTTP/1.1 200"), "{response}");
        serde_json::from_str(response.split("\r\n\r\n").nth(1).unwrap()).unwrap()
    }
    fn finish(address: &str, headers: &str, id: &str) -> Value {
        let deadline = std::time::Instant::now() + std::time::Duration::from_secs(5);
        loop {
            let job = parsed(&request(
                address,
                "GET",
                &format!("/api/job?id={id}"),
                headers,
                "",
            ));
            if job["state"] != "running" {
                return job;
            }
            assert!(std::time::Instant::now() < deadline);
            std::thread::yield_now();
        }
    }
    let system = Arc::new(Controlled {
        reads: AtomicUsize::new(0),
        calls: AtomicUsize::new(0),
        entered: (Mutex::new(false), Condvar::new()),
        release: (Mutex::new(false), Condvar::new()),
    });
    let directory = std::env::temp_dir().join(format!(
        "cpu-memory-http-{}",
        cpu_memory_cleanup::contracts::identifier().unwrap()
    ));
    let server = Workbench::bind(
        Arc::new(Application::new(system.clone())),
        directory.clone(),
        0,
    )
    .unwrap();
    let address = server
        .url()
        .trim_start_matches("http://")
        .trim_end_matches('/')
        .to_string();
    let worker = std::thread::spawn(move || server.serve().unwrap());
    let session = parsed(&request(&address, "GET", "/api/session", "", ""));
    let headers = format!(
        "X-Session-Token: {}\r\n",
        session["token"].as_str().unwrap()
    );
    assert_eq!(system.reads.load(Ordering::SeqCst), 0);
    let targets = parsed(&request(
        &address,
        "POST",
        "/api/action",
        &headers,
        r#"{"kind":"targets"}"#,
    ));
    let target_job = finish(&address, &headers, targets["id"].as_str().unwrap());
    assert_eq!(
        target_job["report"]["samples"][0]["processes"][0]["created_at"],
        "100"
    );
    let accepted = parsed(&request(
        &address,
        "POST",
        "/api/action",
        &headers,
        r#"{"kind":"batch","confirmed":true,"targets":["42:100"]}"#,
    ));
    let entered = system.entered.0.lock().unwrap();
    let (_entered, timeout) = system
        .entered
        .1
        .wait_timeout_while(entered, std::time::Duration::from_secs(5), |v| !*v)
        .unwrap();
    assert!(!timeout.timed_out());
    // HTTP reads must not wait for the maintenance worker or redispatch it.
    let live = parsed(&request(&address, "POST", "/api/display", &headers, "{}"));
    assert_eq!(
        live["samples"][0]["metrics"]["physical"]["available_bytes"],
        300
    );
    let duplicate = request(
        &address,
        "POST",
        "/api/action",
        &headers,
        r#"{"kind":"cleanup","mechanism":"working-set","confirmed":true}"#,
    );
    assert!(duplicate.starts_with("HTTP/1.1 400"));
    *system.release.0.lock().unwrap() = true;
    system.release.1.notify_all();
    let id = accepted["id"].as_str().unwrap();
    let job = finish(&address, &headers, id);
    assert_eq!(job["report"]["completion"], "success");
    assert!(
        job["report"]["action"]["process_sweep"]
            .get("items")
            .is_none()
    );
    let full = parsed(&request(
        &address,
        "GET",
        job["full_report_url"].as_str().unwrap(),
        &headers,
        "",
    ));
    assert_eq!(full["action"]["process_sweep"]["items"][0]["pid"], 42);
    let replay = parsed(&request(
        &address,
        "POST",
        "/api/action",
        &headers,
        &json!({"kind":"replay","source_job":id}).to_string(),
    ));
    assert_eq!(
        finish(&address, &headers, replay["id"].as_str().unwrap())["report"]["mode"],
        "review"
    );
    assert_eq!(system.calls.load(Ordering::SeqCst), 1);
    assert_eq!(system.reads.load(Ordering::SeqCst), 3);
    let state = parsed(&request(&address, "GET", "/api/state", &headers, ""));
    assert_eq!(state["rule"]["armed"], false);
    parsed(&request(
        &address,
        "POST",
        "/api/shutdown",
        &headers,
        r#"{"confirmed":true}"#,
    ));
    worker.join().unwrap();
    assert!(directory.starts_with(std::env::temp_dir()));
    std::fs::remove_dir_all(directory).unwrap();
}
