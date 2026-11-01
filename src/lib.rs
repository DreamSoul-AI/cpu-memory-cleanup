//! Native package. Construction and the static tool catalog have no sampling side effects.
pub mod application;
pub mod contracts;
pub mod interaction;
pub mod system;

pub use application::service::Application;
pub use contracts::{Report, Target};

pub const VERSION: &str = env!("CARGO_PKG_VERSION");
