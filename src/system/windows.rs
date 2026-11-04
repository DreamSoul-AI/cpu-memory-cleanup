//! The unsafe boundary is restricted to Win32 calls and owned process handles.
use super::workingset::{self, Access, NativeCall, Process, Processes, Strategy};
use crate::contracts::{AppResult, Memory, Sweep, Target};
use std::{mem, ptr, sync::atomic::AtomicBool, time::Instant};
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

#[derive(Default)]
pub struct Windows {
    progress: std::sync::Mutex<Option<crate::contracts::CleanupProgress>>,
}
struct ProcessHandle(HANDLE);
impl ProcessHandle {
    fn open(pid: u32, access: Access) -> Result<Self, u32> {
        let rights = access_rights(access);
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
            commit_used_bytes: Some(bytes(value.CommitTotal)?),
            commit_limit_bytes: Some(bytes(value.CommitLimit)?),
            ..Memory::default()
        };
        if result.total_bytes == 0 || result.available_bytes > result.total_bytes {
            return Err("系统物理内存读数无效".into());
        }
        Ok(result)
    }
    fn targets(&self) -> AppResult<Vec<Target>> {
        let mut targets = Vec::new();
        for pid in ids()? {
            let Ok(handle) = ProcessHandle::open(pid, Access::Query) else {
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
    fn cleanup_progress(&self) -> Option<crate::contracts::CleanupProgress> {
        self.progress.lock().expect("progress lock").clone()
    }
    fn cleanup(&self, selected: Option<&[Target]>, cancel: &AtomicBool) -> AppResult<Sweep> {
        let result = workingset::sweep_with_progress(
            &Win32Processes,
            selected,
            Strategy::Verified,
            cancel,
            &mut |sweep| {
                *self.progress.lock().expect("progress lock") = Some(sweep.into());
            },
        );
        *self.progress.lock().expect("progress lock") = None;
        result
    }
}
pub fn normalize(path: &str) -> String {
    workingset::normalize(path)
}

fn access_rights(access: Access) -> u32 {
    match access {
        Access::Query => PROCESS_QUERY_LIMITED_INFORMATION,
        Access::VerifiedCleanup => PROCESS_QUERY_LIMITED_INFORMATION | PROCESS_SET_QUOTA,
        #[cfg(any(feature = "experimental-quota", test))]
        Access::QuotaOnlyCleanup => PROCESS_SET_QUOTA,
    }
}
struct Win32Processes;
#[cfg(feature = "experimental-quota")]
struct FixedProcesses(Vec<u32>);
#[cfg(feature = "experimental-quota")]
impl Processes for FixedProcesses {
    type Handle = ProcessHandle;
    fn enumerate(&self) -> AppResult<Vec<u32>> {
        Ok(self.0.clone())
    }
    fn open(&self, pid: u32, access: Access) -> Result<ProcessHandle, u32> {
        ProcessHandle::open(pid, access)
    }
}
impl Processes for Win32Processes {
    type Handle = ProcessHandle;
    fn enumerate(&self) -> AppResult<Vec<u32>> {
        ids()
    }
    fn open(&self, pid: u32, access: Access) -> Result<ProcessHandle, u32> {
        ProcessHandle::open(pid, access)
    }
}
impl Process for ProcessHandle {
    fn identity(&self) -> AppResult<String> {
        ProcessHandle::identity(self)
    }
    fn path(&self) -> Option<String> {
        ProcessHandle::path(self)
    }
    fn working_set(&self) -> Option<u64> {
        ProcessHandle::working_set(self)
    }
    fn trim(&self) -> NativeCall {
        let started = Instant::now();
        let ok = unsafe { SetProcessWorkingSetSize(self.0, usize::MAX, usize::MAX) };
        let status = if ok == 0 {
            unsafe { GetLastError() }
        } else {
            0
        };
        NativeCall {
            succeeded: ok != 0,
            status,
            elapsed_ms: started.elapsed().as_secs_f64() * 1000.,
        }
    }
    fn close(self) -> AppResult<()> {
        ProcessHandle::close(self)
    }
}

/// Development-only assembly for a future controlled comparison.
/// Construction performs no reads or maintenance. Explicit targets retain full verification.
/// Neither the default factory nor CLI/HTTP selects this assembly.
#[cfg(feature = "experimental-quota")]
pub fn quota_only_global_candidate() -> impl super::System {
    QuotaOnlyGlobal
}
#[cfg(feature = "experimental-quota")]
struct QuotaOnlyGlobal;
#[cfg(feature = "experimental-quota")]
impl super::System for QuotaOnlyGlobal {
    fn supported(&self) -> bool {
        true
    }
    fn snapshot(&self) -> AppResult<Memory> {
        super::System::snapshot(&Windows::default())
    }
    fn targets(&self) -> AppResult<Vec<Target>> {
        super::System::targets(&Windows::default())
    }
    fn cleanup(&self, selected: Option<&[Target]>, cancel: &AtomicBool) -> AppResult<Sweep> {
        workingset::sweep(&Win32Processes, selected, Strategy::QuotaOnlyGlobal, cancel)
    }
}

/// Experimental fixed *global-style* sweep. This is not the verified target API.
/// Construction validates input only. Normal CLI/HTTP never selects this system.
#[cfg(feature = "experimental-quota")]
pub fn comparison_system(
    pids: Vec<u32>,
    method: crate::contracts::ComparisonMethod,
) -> AppResult<impl super::System> {
    crate::contracts::validate_comparison_pids(&pids)?;
    Ok(ComparisonWindows { pids, method })
}
#[cfg(feature = "experimental-quota")]
pub fn comparison_pids() -> AppResult<Vec<u32>> {
    ids()
}
/// Read only the fixed list, outside the measured maintenance interval.
#[cfg(feature = "experimental-quota")]
pub fn comparison_observe(pids: &[u32]) -> AppResult<Vec<Target>> {
    let mut result = Vec::new();
    for &pid in pids {
        let Ok(handle) = ProcessHandle::open(pid, Access::Query) else {
            continue;
        };
        let identity = handle.identity();
        let working_set_bytes = handle.working_set();
        handle.close()?;
        if let Ok(created_at) = identity {
            result.push(Target {
                pid,
                created_at,
                name: String::new(),
                image_path: None,
                working_set_bytes,
            });
        }
    }
    Ok(result)
}
#[cfg(feature = "experimental-quota")]
struct ComparisonWindows {
    pids: Vec<u32>,
    method: crate::contracts::ComparisonMethod,
}
#[cfg(feature = "experimental-quota")]
impl super::System for ComparisonWindows {
    fn supported(&self) -> bool {
        true
    }
    fn snapshot(&self) -> AppResult<Memory> {
        super::System::snapshot(&Windows::default())
    }
    fn targets(&self) -> AppResult<Vec<Target>> {
        comparison_observe(&self.pids)
    }
    fn cleanup(&self, selected: Option<&[Target]>, cancel: &AtomicBool) -> AppResult<Sweep> {
        if selected.is_some() {
            return Err("开发固定全局清单不支持定向接口".into());
        }
        let strategy = match self.method {
            crate::contracts::ComparisonMethod::Verified => Strategy::Verified,
            crate::contracts::ComparisonMethod::Quota => Strategy::QuotaOnlyGlobal,
        };
        let mut result =
            workingset::sweep(&FixedProcesses(self.pids.clone()), None, strategy, cancel)?;
        if let Some(timing) = result.timing.as_mut() {
            timing["enumeration_source"] = serde_json::json!("fixed_developer_comparison");
            timing["fixed_pids"] = serde_json::json!(self.pids);
        }
        Ok(result)
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn access_masks_match_query_verified_and_quota_only_contracts() {
        assert_eq!(access_rights(Access::Query), 0x1000);
        assert_eq!(access_rights(Access::VerifiedCleanup), 0x1100);
        assert_eq!(access_rights(Access::QuotaOnlyCleanup), 0x0100);
    }
    #[cfg(feature = "experimental-quota")]
    #[test]
    fn developer_fixed_scope_validates_input_and_refuses_target_api_before_native_access() {
        use super::super::System;
        use crate::contracts::ComparisonMethod as Method;
        assert!(comparison_system(vec![], Method::Quota).is_err());
        assert!(comparison_system(vec![1, 1], Method::Verified).is_err());
        let system = comparison_system(vec![1], Method::Quota).unwrap();
        assert!(system.cleanup(Some(&[]), &AtomicBool::new(false)).is_err());
    }
}
