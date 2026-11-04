pub mod cli;
pub mod web;

#[cfg(all(windows, feature = "desktop-window"))]
pub mod desktop;
