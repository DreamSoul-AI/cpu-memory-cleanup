use crate::contracts::{AppResult, Target};
use std::mem::{MaybeUninit, size_of};
fn info<T>(pid: i32, flavor: i32) -> Option<T> {
    let mut result = MaybeUninit::<T>::zeroed();
    // Only the fixed C POD structures proc_bsdinfo/proc_taskinfo call this helper.
    let bytes = unsafe {
        libc::proc_pidinfo(
            pid,
            flavor,
            0,
            result.as_mut_ptr().cast(),
            size_of::<T>() as i32,
        )
    };
    (bytes == size_of::<T>() as i32).then(|| unsafe { result.assume_init() })
}
fn text(chars: &[libc::c_char]) -> String {
    let bytes: Vec<u8> = chars
        .iter()
        .take_while(|c| **c != 0)
        .map(|c| *c as u8)
        .collect();
    String::from_utf8_lossy(&bytes).into_owned()
}
fn identity(value: &libc::proc_bsdinfo) -> (u64, u64) {
    (value.pbi_start_tvsec, value.pbi_start_tvusec)
}
pub fn targets() -> AppResult<Vec<Target>> {
    let needed = unsafe {
        libc::proc_listpids(
            1, /* PROC_ALL_PIDS, sys/proc_info.h */
            0,
            std::ptr::null_mut(),
            0,
        )
    };
    if needed <= 0 {
        return Err(format!(
            "proc_listpids: {}",
            std::io::Error::last_os_error()
        ));
    }
    let count = (needed as usize) / size_of::<i32>() + 128;
    if count > 1_048_576 {
        return Err("Process list exceeds capacity budget".into());
    }
    let mut pids = vec![0i32; count];
    let capacity = pids.len() * size_of::<i32>();
    let bytes = unsafe {
        libc::proc_listpids(
            1, /* PROC_ALL_PIDS, sys/proc_info.h */
            0,
            pids.as_mut_ptr().cast(),
            capacity as i32,
        )
    };
    if bytes < 0 || bytes as usize >= capacity || bytes as usize % size_of::<i32>() != 0 {
        return Err("Process list failed or changed beyond buffer capacity".into());
    }
    pids.truncate(bytes as usize / size_of::<i32>());
    let mut targets = Vec::new();
    for pid in pids.into_iter().filter(|p| *p > 0) {
        let Some(first) = info::<libc::proc_bsdinfo>(pid, libc::PROC_PIDTBSDINFO) else {
            continue;
        };
        let rss =
            info::<libc::proc_taskinfo>(pid, libc::PROC_PIDTASKINFO).map(|p| p.pti_resident_size);
        let mut path = vec![0u8; 4096];
        let length =
            unsafe { libc::proc_pidpath(pid, path.as_mut_ptr().cast(), path.len() as u32) };
        let image_path = (length > 0).then(|| {
            String::from_utf8_lossy(
                &path[..path.iter().position(|b| *b == 0).unwrap_or(path.len())],
            )
            .into_owned()
        });
        let Some(second) = info::<libc::proc_bsdinfo>(pid, libc::PROC_PIDTBSDINFO) else {
            continue;
        };
        if first.pbi_pid != pid as u32
            || second.pbi_pid != first.pbi_pid
            || identity(&first) != identity(&second)
        {
            continue;
        }
        let name = text(&first.pbi_name);
        targets.push(Target {
            pid: pid as u32,
            created_at: format!("{}:{:06}", first.pbi_start_tvsec, first.pbi_start_tvusec),
            name: if name.is_empty() {
                text(&first.pbi_comm)
            } else {
                name
            },
            image_path,
            working_set_bytes: rss,
        });
    }
    targets.sort_by_key(|p| p.pid);
    targets.dedup_by_key(|p| p.pid);
    Ok(targets)
}
