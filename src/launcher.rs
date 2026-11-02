#![cfg_attr(windows, windows_subsystem = "windows")]

// Native desktop entry: shares the same library and HTTP server, without a console.
fn main() {
    let mut args: Vec<String> = std::env::args().skip(1).collect();
    let no_open = args.iter().any(|arg| arg == "--no-open");
    args.retain(|arg| arg != "--no-open");
    args.insert(0, "--web".into());
    if !no_open {
        args.push("--open".into());
    }
    if let Err(error) = cpu_memory_cleanup::interaction::cli::execute(args) {
        #[cfg(windows)]
        {
            use windows_sys::Win32::UI::WindowsAndMessaging::{MB_ICONERROR, MB_OK, MessageBoxW};
            let message: Vec<u16> = error.encode_utf16().chain(Some(0)).collect();
            let title: Vec<u16> = "内存清理 · 启动失败"
                .encode_utf16()
                .chain(Some(0))
                .collect();
            unsafe {
                MessageBoxW(
                    std::ptr::null_mut(),
                    message.as_ptr(),
                    title.as_ptr(),
                    MB_OK | MB_ICONERROR,
                );
            }
        }
        #[cfg(not(windows))]
        eprintln!("{error}");
        std::process::exit(2);
    }
}
