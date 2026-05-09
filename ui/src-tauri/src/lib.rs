#[tauri::command]
fn agent_status(workspace: &str) -> String {
    format!("Tauri bridge online for {workspace}")
}

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    tauri::Builder::default()
        .plugin(tauri_plugin_opener::init())
        .invoke_handler(tauri::generate_handler![agent_status])
        .run(tauri::generate_context!())
        .expect("error while running tauri application");
}
