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
