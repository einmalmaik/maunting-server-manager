// MSM Backup Recovery - Tauri v2 backend entry point.
//
// Registers all Tauri commands (extraction, save, preview, temp-file
// management) and installs a `RunEvent::Exit` handler that cleans up every
// tracked temp directory so no decrypted artifacts remain on disk after the
// app closes (VAL-CROSS-004).

mod commands;

use tauri::{Manager, RunEvent};

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    let app = tauri::Builder::default()
        .plugin(tauri_plugin_dialog::init())
        .plugin(tauri_plugin_fs::init())
        .invoke_handler(tauri::generate_handler![
            commands::extract_tar_gz,
            commands::save_extracted,
            commands::save_as_zip,
            commands::read_text_file,
            commands::create_temp_dir,
            commands::write_temp_file,
            commands::cleanup_temp_dir,
        ])
        .setup(|app| {
            #[cfg(debug_assertions)]
            {
                let window = app.get_webview_window("main").expect("main window missing");
                window.open_devtools();
            }
            Ok(())
        })
        .build(tauri::generate_context!())
        .expect("error while building the MSM Backup Recovery app");

    // VAL-CROSS-004: clean up all tracked temp directories on app exit so no
    // decrypted tar.gz or extracted files remain on disk.
    app.run(|_app_handle, event| {
        if let RunEvent::Exit = event {
            commands::cleanup_all_temp_dirs();
        }
    });
}
