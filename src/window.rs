#![cfg_attr(windows, windows_subsystem = "windows")]
fn main() {
    let args: Vec<String> = std::env::args().skip(1).collect();
    if args == ["--version"] || args == ["--tool-info"] {
        std::process::exit(cpu_memory_cleanup::interaction::cli::main(args));
    }
    #[cfg(windows)]
    if let Err(error) =
        cpu_memory_cleanup::interaction::desktop::run(std::env::args().skip(1).collect())
    {
        use windows_sys::Win32::UI::WindowsAndMessaging::{MB_ICONERROR, MB_OK, MessageBoxW};
        let text: Vec<u16> = error.encode_utf16().chain(Some(0)).collect();
        let title: Vec<u16> = "内存清理 · 窗口原型"
            .encode_utf16()
            .chain(Some(0))
            .collect();
        if !std::env::args().any(|arg| arg == "--smoke-test") {
            unsafe {
                MessageBoxW(
                    std::ptr::null_mut(),
                    text.as_ptr(),
                    title.as_ptr(),
                    MB_OK | MB_ICONERROR,
                );
            }
        }
        std::process::exit(2);
    }
    #[cfg(not(windows))]
    {
        eprintln!("This window prototype is currently validated only on Windows.");
        std::process::exit(2);
    }
}
