// MSM Backup Recovery - Tauri v2 backend entry point.
//
// The tar.gz extraction commands (`extract_tar_gz`, `save_extracted`,
// `read_text_file`) are implemented in the `rust-extraction` feature (M2).
// This shell boots the Tauri app with the frontend webview only, which is all
// the M1 foundation milestone requires.

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    tauri::Builder::default()
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
