use crate::contracts::{AppResult, Memory, Sweep, Target};
use std::sync::{Arc, atomic::AtomicBool};
pub mod records;
#[cfg(windows)]
pub mod windows;

pub fn random_bytes(bytes: &mut [u8]) -> AppResult<()> {
    #[cfg(windows)]
    {
        use windows_sys::Win32::Security::Cryptography::{
            BCRYPT_USE_SYSTEM_PREFERRED_RNG, BCryptGenRandom,
        };
        let status = unsafe {
            BCryptGenRandom(
                std::ptr::null_mut(),
                bytes.as_mut_ptr(),
                bytes.len() as u32,
                BCRYPT_USE_SYSTEM_PREFERRED_RNG,
            )
        };
        if status < 0 {
            return Err(format!("系统随机源失败：NTSTATUS {status}"));
        }
        Ok(())
    }
    #[cfg(unix)]
    {
        use std::io::Read;
        std::fs::File::open("/dev/urandom")
            .and_then(|mut file| file.read_exact(bytes))
            .map_err(|e| e.to_string())
    }
    #[cfg(not(any(windows, unix)))]
    {
        let _ = bytes;
        Err("未实现当前平台的安全随机源".into())
    }
}

pub trait System: Send + Sync {
    fn supported(&self) -> bool;
    fn snapshot(&self) -> AppResult<Memory>;
    fn targets(&self) -> AppResult<Vec<Target>>;
    fn cleanup(&self, targets: Option<&[Target]>, cancel: &AtomicBool) -> AppResult<Sweep>;
}
pub fn native() -> Arc<dyn System> {
    #[cfg(windows)]
    {
        Arc::new(windows::Windows)
    }
    #[cfg(not(windows))]
    {
        Arc::new(Unsupported)
    }
}
#[cfg(not(windows))]
struct Unsupported;
#[cfg(not(windows))]
impl System for Unsupported {
    fn supported(&self) -> bool {
        false
    }
    fn snapshot(&self) -> AppResult<Memory> {
        Err("当前平台未实现真实内存读取".into())
    }
    fn targets(&self) -> AppResult<Vec<Target>> {
        Err("当前平台未实现目标发现".into())
    }
    fn cleanup(&self, _: Option<&[Target]>, _: &AtomicBool) -> AppResult<Sweep> {
        Err("当前平台未实现工作集整理".into())
    }
}
