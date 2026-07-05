// MSM Backup Recovery - Tauri v2 backend entry point.
//
// Registers the M2 `rust-extraction` commands (extract_tar_gz,
// save_extracted, read_text_file) alongside the M1 frontend bootstrap.

mod commands;

use tauri::Manager;

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    tauri::Builder::default()
        .plugin(tauri_plugin_dialog::init())
        .plugin(tauri_plugin_fs::init())
        .invoke_handler(tauri::generate_handler![
            commands::extract_tar_gz,
            commands::save_extracted,
            commands::read_text_file,
        ])
        .setup(|app| {
            #[cfg(debug_assertions)]
            {
                let window = app.get_webview_window("main").expect("main window missing");
                window.open_devtools();
            }
            Ok(())
        })
        .run(tauri::generate_context!())
        .expect("error while running the MSM Backup Recovery app");
}
