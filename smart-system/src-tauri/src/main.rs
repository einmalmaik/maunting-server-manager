// Prevents an extra console window on Windows in release.
#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

fn main() {
    updater_core::velopack_start();

    maunting_smart_system_lib::run()
}
