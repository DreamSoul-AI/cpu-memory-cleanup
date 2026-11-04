use crate::contracts::{AppResult, AvailableKind, Memory};
use std::{
    ffi::CStr,
    mem::{MaybeUninit, offset_of, size_of},
};

// libc exposes the Mach host call and structures; these two public libSystem
// symbols acquire/release its send right without adding a second Mach binding crate.
unsafe extern "C" {
    fn mach_host_self() -> libc::mach_port_t;
    static mach_task_self_: libc::mach_port_t;
    fn mach_port_deallocate(
        task: libc::mach_port_t,
        name: libc::mach_port_t,
    ) -> libc::kern_return_t;
}
struct Host(libc::mach_port_t);
impl Drop for Host {
    fn drop(&mut self) {
        unsafe {
            mach_port_deallocate(mach_task_self_, self.0);
        }
    }
}
fn sysctl<T>(name: &CStr) -> AppResult<T> {
    let mut value = MaybeUninit::<T>::zeroed();
    let mut bytes = size_of::<T>();
    // The private callers request fixed-size scalar sysctl values only.
    let result = unsafe {
        libc::sysctlbyname(
            name.as_ptr(),
            value.as_mut_ptr().cast(),
            &mut bytes,
            std::ptr::null_mut(),
            0,
        )
    };
    if result != 0 || bytes != size_of::<T>() {
        return Err(format!(
            "sysctl {}: {}",
            name.to_string_lossy(),
            std::io::Error::last_os_error()
        ));
    }
    Ok(unsafe { value.assume_init() })
}
fn convert(
    total: u64,
    page: u64,
    free: u32,
    inactive: u32,
    compressed: Option<u32>,
) -> AppResult<Memory> {
    let available = u64::from(free)
        .checked_add(u64::from(inactive))
        .and_then(|n| n.checked_mul(page))
        .ok_or("Mac capacity overflow")?;
    if page == 0 || total == 0 || available > total {
        return Err("Invalid macOS physical capacity".into());
    }
    let compressed_bytes = compressed
        .map(|n| {
            u64::from(n)
                .checked_mul(page)
                .ok_or("Mac compressed capacity overflow")
        })
        .transpose()?;
    Ok(Memory {
        total_bytes: total,
        available_bytes: available,
        available_kind: AvailableKind::FreeAndInactiveEstimate,
        compressed_bytes,
        ..Memory::default()
    })
}
pub fn snapshot() -> AppResult<Memory> {
    let total: u64 = sysctl(c"hw.memsize")?;
    let page = u64::from(sysctl::<u32>(c"hw.pagesize")?);
    let host = Host(unsafe { mach_host_self() });
    let mut stats = MaybeUninit::<libc::vm_statistics64>::zeroed();
    let mut count = libc::HOST_VM_INFO64_COUNT;
    let code = unsafe {
        libc::host_statistics64(
            host.0,
            libc::HOST_VM_INFO64,
            stats.as_mut_ptr().cast(),
            &mut count,
        )
    };
    if code != libc::KERN_SUCCESS
        || (count as usize) * size_of::<libc::integer_t>()
            < offset_of!(libc::vm_statistics64, wire_count)
    {
        return Err(format!(
            "host_statistics64 failed or short: {code}, count={count}"
        ));
    }
    // Mach can return an older statistics revision. Read only fields it supplied.
    let stats = unsafe { stats.assume_init() };
    let compressor_end =
        offset_of!(libc::vm_statistics64, compressor_page_count) + size_of::<libc::natural_t>();
    let compressed = ((count as usize) * size_of::<libc::integer_t>() >= compressor_end)
        .then_some(stats.compressor_page_count);
    convert(
        total,
        page,
        stats.free_count,
        stats.inactive_count,
        compressed,
    )
}
#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn mac_page_estimate_keeps_unavailable_commit_null() {
        let value = convert(100 * 16384, 16384, 10, 20, Some(5)).unwrap();
        assert_eq!(value.available_bytes, 30 * 16384);
        assert_eq!(value.compressed_bytes, Some(5 * 16384));
        let metrics = value.metrics();
        assert!(metrics["commit"]["used_bytes"].is_null());
        assert_eq!(
            metrics["physical"]["available_kind"],
            "free_and_inactive_estimate"
        );
        assert!(convert(10, 16384, 10, 20, None).is_err());
    }
}
