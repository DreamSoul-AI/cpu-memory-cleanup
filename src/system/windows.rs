//! The unsafe boundary is restricted to Win32 calls and owned process handles.
use crate::contracts::{AppResult, Memory, Sweep, Target};
use serde_json::json;
use std::{
    mem, ptr,
    sync::atomic::{AtomicBool, Ordering},
    time::Instant,
};
use windows_sys::Win32::{
    Foundation::{CloseHandle, FILETIME, GetLastError, HANDLE},
    System::{
        ProcessStatus::{
            EnumProcesses, GetPerformanceInfo, GetProcessMemoryInfo, PERFORMANCE_INFORMATION,
            PROCESS_MEMORY_COUNTERS,
        },
        Threading::{
            GetProcessTimes, OpenProcess, PROCESS_QUERY_LIMITED_INFORMATION, PROCESS_SET_QUOTA,
            QueryFullProcessImageNameW, SetProcessWorkingSetSize,
        },
    },
};

pub struct Windows;
struct ProcessHandle(HANDLE);
impl ProcessHandle {
    fn open(pid: u32, write: bool) -> Result<Self, u32> {
        let rights = PROCESS_QUERY_LIMITED_INFORMATION | if write { PROCESS_SET_QUOTA } else { 0 };
        let handle = unsafe { OpenProcess(rights, 0, pid) };
        if handle.is_null() {
            Err(unsafe { GetLastError() })
        } else {
            Ok(Self(handle))
        }
    }
    fn identity(&self) -> AppResult<String> {
        let mut times: [FILETIME; 4] = unsafe { mem::zeroed() };
        let ok = unsafe {
            GetProcessTimes(
                self.0,
                &mut times[0],
                &mut times[1],
                &mut times[2],
                &mut times[3],
            )
        };
        if ok == 0 {
            return Err(last_error("GetProcessTimes"));
        }
        Ok(
            ((u64::from(times[0].dwHighDateTime) << 32) | u64::from(times[0].dwLowDateTime))
                .to_string(),
        )
    }
    fn path(&self) -> Option<String> {
        let mut text = vec![0u16; 32768];
        let mut size = text.len() as u32;
        let ok = unsafe { QueryFullProcessImageNameW(self.0, 0, text.as_mut_ptr(), &mut size) };
        (ok != 0).then(|| String::from_utf16_lossy(&text[..size as usize]))
    }
    fn working_set(&self) -> Option<u64> {
        let mut counters: PROCESS_MEMORY_COUNTERS = unsafe { mem::zeroed() };
        let size = mem::size_of::<PROCESS_MEMORY_COUNTERS>() as u32;
        counters.cb = size;
        let ok = unsafe { GetProcessMemoryInfo(self.0, &mut counters, size) };
        (ok != 0).then_some(counters.WorkingSetSize as u64)
    }
    fn close(mut self) -> AppResult<()> {
        let handle = mem::replace(&mut self.0, ptr::null_mut());
        if unsafe { CloseHandle(handle) } == 0 {
            Err(last_error("CloseHandle"))
        } else {
            Ok(())
        }
    }
}
impl Drop for ProcessHandle {
    fn drop(&mut self) {
        if !self.0.is_null() {
            unsafe {
                CloseHandle(self.0);
            }
        }
    }
}
fn last_error(operation: &str) -> String {
    format!("{operation}失败，Win32错误{}", unsafe {
        GetLastError()
    })
}
fn ids() -> AppResult<Vec<u32>> {
    let mut size = 1024;
    loop {
        let mut ids = vec![0u32; size];
        let mut used = 0;
        if unsafe { EnumProcesses(ids.as_mut_ptr(), (ids.len() * 4) as u32, &mut used) } == 0 {
            return Err(last_error("EnumProcesses"));
        }
        if (used as usize) < ids.len() * 4 {
            ids.truncate(used as usize / 4);
            ids.retain(|pid| *pid != 0);
            return Ok(ids);
        }
        size *= 2;
        if size > 131072 {
            return Err("进程枚举超过预算".into());
        }
    }
}
impl super::System for Windows {
    fn supported(&self) -> bool {
        true
    }
    fn snapshot(&self) -> AppResult<Memory> {
        let mut value: PERFORMANCE_INFORMATION = unsafe { mem::zeroed() };
        value.cb = mem::size_of::<PERFORMANCE_INFORMATION>() as u32;
        if unsafe {
            GetPerformanceInfo(&mut value, mem::size_of::<PERFORMANCE_INFORMATION>() as u32)
        } == 0
        {
            return Err(last_error("GetPerformanceInfo"));
        }
        let bytes = |pages: usize| {
            (pages as u64)
                .checked_mul(value.PageSize as u64)
                .ok_or_else(|| "系统页计数溢出".to_string())
        };
        let result = Memory {
            total_bytes: bytes(value.PhysicalTotal)?,
            available_bytes: bytes(value.PhysicalAvailable)?,
            commit_used_bytes: bytes(value.CommitTotal)?,
            commit_limit_bytes: bytes(value.CommitLimit)?,
        };
        if result.total_bytes == 0 || result.available_bytes > result.total_bytes {
            return Err("系统物理内存读数无效".into());
        }
        Ok(result)
    }
    fn targets(&self) -> AppResult<Vec<Target>> {
        let mut targets = Vec::new();
        for pid in ids()? {
            let Ok(handle) = ProcessHandle::open(pid, false) else {
                continue;
            };
            let identity = handle.identity();
            let image_path = handle.path();
            let working_set_bytes = handle.working_set();
            handle.close()?;
            if let Ok(created_at) = identity {
                let name = image_path
                    .as_deref()
                    .and_then(|path| path.rsplit(['\\', '/']).next())
                    .unwrap_or("路径未知")
                    .to_string();
                targets.push(Target {
                    pid,
                    created_at,
                    name,
                    image_path,
                    working_set_bytes,
                });
            }
        }
        Ok(targets)
    }
    fn cleanup(&self, selected: Option<&[Target]>, cancel: &AtomicBool) -> AppResult<Sweep> {
        let ids = match selected {
            Some(targets) => targets.iter().map(|t| t.pid).collect(),
            None => ids()?,
        };
        let mut sweep = Sweep {
            enumerated_count: ids.len(),
            resources_closed: true,
            ..Default::default()
        };
        for pid in ids {
            if cancel.load(Ordering::Acquire) {
                sweep.cancelled = true;
                break;
            }
            sweep.visited_count += 1;
            let handle = match ProcessHandle::open(pid, true) {
                Ok(handle) => handle,
                Err(status) => {
                    sweep.skipped_count += 1;
                    sweep.push(json!({"pid":pid,"outcome":"skipped","status":status}));
                    continue;
                }
            };
            let identity = handle.identity();
            let target = selected.and_then(|targets| targets.iter().find(|t| t.pid == pid));
            let mismatch = match &identity {
                Ok(created) => target.is_some_and(|t| {
                    t.created_at != *created
                        || t.image_path.as_ref().is_some_and(|p| {
                            handle
                                .path()
                                .is_none_or(|current| normalize(&current) != normalize(p))
                        })
                }),
                Err(_) => true,
            };
            if mismatch {
                sweep.skipped_count += 1;
                sweep.push(json!({"pid":pid,"outcome":"skipped","reason":"目标身份或路径未确认"}));
            } else if cancel.load(Ordering::Acquire) {
                sweep.cancelled = true;
            } else {
                sweep.attempted_count += 1;
                let before = selected.and_then(|_| handle.working_set());
                let started = Instant::now();
                let ok = unsafe { SetProcessWorkingSetSize(handle.0, usize::MAX, usize::MAX) };
                let status = if ok == 0 {
                    unsafe { GetLastError() }
                } else {
                    0
                };
                let elapsed = started.elapsed().as_secs_f64() * 1000.;
                sweep.native_call_ms += elapsed;
                if ok != 0 {
                    sweep.successful_count += 1;
                } else {
                    sweep.failed_count += 1;
                }
                let after = selected.and_then(|_| handle.working_set());
                sweep.push(json!({"pid":pid,"created_at":identity.ok(),"outcome":if ok!=0 {"success"} else {"failure"},"status":status,"native_call_ms":elapsed,"working_set_before_bytes":before,"working_set_after_bytes":after}));
            }
            if let Err(error) = handle.close() {
                sweep.resources_closed = false;
                sweep.unknown_count += 1;
                sweep.push(json!({"pid":pid,"close_error":error,"outcome":"unknown"}));
                break;
            }
            if sweep.cancelled {
                break;
            }
        }
        Ok(sweep)
    }
}
pub fn normalize(path: &str) -> String {
    path.replace('\\', "/").to_lowercase()
}
