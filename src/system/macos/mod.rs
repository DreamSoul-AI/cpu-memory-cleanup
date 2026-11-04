//! macOS native readonly system integration. No active reclaim is implemented.
mod memory;
mod processes;
use super::System;
use crate::contracts::{AppResult, Memory, Sweep, Target};
use std::sync::atomic::AtomicBool;
pub struct MacOs;
impl System for MacOs {
    fn supported(&self) -> bool {
        true
    }
    fn cleanup_supported(&self) -> bool {
        false
    }
    fn snapshot(&self) -> AppResult<Memory> {
        memory::snapshot()
    }
    fn targets(&self) -> AppResult<Vec<Target>> {
        processes::targets()
    }
    fn cleanup(&self, _: Option<&[Target]>, _: &AtomicBool) -> AppResult<Sweep> {
        Err("macOS当前仅支持真实读取，未派发主动整理".into())
    }
}
