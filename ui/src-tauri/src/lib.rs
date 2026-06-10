use std::env;
use std::fs;
use std::net::TcpListener;
use std::path::PathBuf;
use std::sync::Mutex;

use serde::Serialize;
use tauri::{Manager, WindowEvent};
use tauri_plugin_shell::{
    process::{CommandChild, CommandEvent},
    ShellExt,
};

const DEFAULT_API_HOST: &str = "127.0.0.1";
const DEFAULT_API_PORT: u16 = 8765;
const API_SIDECAR: &str = "automata-api";

#[derive(Default)]
struct BackendState {
    child: Mutex<Option<CommandChild>>,
    status: Mutex<String>,
}

#[derive(Clone)]
struct ResolvedApiConfig {
    host: String,
    port: u16,
}

#[derive(Serialize)]
#[serde(rename_all = "camelCase")]
struct ApiConfigResponse {
    http_base_url: String,
    ws_chat_url: String,
    default_working_directory: String,
}

#[tauri::command]
fn agent_status(workspace: &str, state: tauri::State<'_, BackendState>) -> String {
    let backend_status = state.status.lock().expect("backend status lock").clone();
    format!("Tauri bridge online for {workspace}. Backend: {backend_status}")
}

#[tauri::command]
fn api_config() -> ApiConfigResponse {
    resolve_api_config().to_response()
}

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    tauri::Builder::default()
        .manage(BackendState {
            child: Mutex::new(None),
            status: Mutex::new("Starting".to_string()),
        })
        .plugin(tauri_plugin_dialog::init())
        .plugin(tauri_plugin_opener::init())
        .plugin(tauri_plugin_shell::init())
        .setup(|app| {
            start_api_sidecar(app);
            Ok(())
        })
        .on_window_event(|window, event| {
            if matches!(event, WindowEvent::CloseRequested { .. }) {
                stop_api_sidecar(window.app_handle());
            }
        })
        .invoke_handler(tauri::generate_handler![agent_status, api_config])
        .build(tauri::generate_context!())
        .expect("error while building tauri application")
        .run(|app_handle, event| {
            if matches!(event, tauri::RunEvent::ExitRequested { .. }) {
                stop_api_sidecar(app_handle);
            }
        });
}

fn start_api_sidecar(app: &mut tauri::App) {
    let app_handle = app.handle().clone();
    let api_config = resolve_api_config();
    let api_address = api_config.address();

    if TcpListener::bind(&api_address).is_err() {
        set_backend_status(
            &app_handle,
            format!(
                "Port {api_address} is already in use. Close the existing process and restart."
            ),
        );
        return;
    }

    let data_dir = match app.path().app_data_dir() {
        Ok(path) => path,
        Err(error) => {
            set_backend_status(
                &app_handle,
                format!("Failed to resolve app data dir: {error}"),
            );
            return;
        }
    };

    if let Err(error) = fs::create_dir_all(&data_dir) {
        set_backend_status(
            &app_handle,
            format!("Failed to create app data dir: {error}"),
        );
        return;
    }

    let sidecar = match app.shell().sidecar(API_SIDECAR) {
        Ok(command) => command,
        Err(error) => {
            set_backend_status(&app_handle, format!("Failed to locate sidecar: {error}"));
            return;
        }
    };
    let workspace_dir = resolve_workspace_dir();

    match sidecar
        .env("AUTOMATA_DATA_DIR", data_dir)
        .env("AUTOMATA_API_HOST", api_config.host)
        .env("AUTOMATA_API_PORT", api_config.port.to_string())
        .env(
            "AUTOMATA_WORKSPACE_DIR",
            workspace_dir.to_string_lossy().to_string(),
        )
        .spawn()
    {
        Ok((mut receiver, child)) => {
            {
                let state = app.state::<BackendState>();
                *state.child.lock().expect("backend child lock") = Some(child);
            }

            set_backend_status(&app_handle, format!("Starting sidecar at {api_address}"));
            tauri::async_runtime::spawn(async move {
                while let Some(event) = receiver.recv().await {
                    match event {
                        CommandEvent::Stdout(output) => {
                            println!("[automata-api] {}", String::from_utf8_lossy(&output));
                        }
                        CommandEvent::Stderr(output) => {
                            eprintln!("[automata-api] {}", String::from_utf8_lossy(&output));
                        }
                        CommandEvent::Terminated(payload) => {
                            set_backend_status(
                                &app_handle,
                                format!("Sidecar exited with code {:?}", payload.code),
                            );
                            break;
                        }
                        _ => {}
                    }
                }
            });
        }
        Err(error) => {
            set_backend_status(&app_handle, format!("Failed to start sidecar: {error}"));
        }
    }
}

fn resolve_workspace_dir() -> PathBuf {
    if let Some(path) = env::var("AUTOMATA_WORKSPACE_DIR")
        .ok()
        .map(|value| value.trim().to_string())
        .filter(|value| !value.is_empty())
    {
        return PathBuf::from(path);
    }

    if cfg!(debug_assertions) {
        if let Some(project_root) = PathBuf::from(env!("CARGO_MANIFEST_DIR"))
            .parent()
            .and_then(|path| path.parent())
        {
            return project_root.to_path_buf();
        }
    }

    env::current_dir().unwrap_or_else(|_| PathBuf::from("."))
}

fn stop_api_sidecar(app_handle: &tauri::AppHandle) {
    let state = app_handle.state::<BackendState>();
    let child = {
        let mut guard = state.child.lock().expect("backend child lock");
        guard.take()
    };

    if let Some(child) = child {
        let _ = child.kill();
        set_backend_status(app_handle, "Stopped sidecar");
    }
}

fn set_backend_status(app_handle: &tauri::AppHandle, status: impl Into<String>) {
    let state = app_handle.state::<BackendState>();
    *state.status.lock().expect("backend status lock") = status.into();
}

fn resolve_api_config() -> ResolvedApiConfig {
    let host = env::var("AUTOMATA_API_HOST")
        .ok()
        .map(|value| value.trim().to_string())
        .filter(|value| !value.is_empty())
        .unwrap_or_else(|| DEFAULT_API_HOST.to_string());
    let port = env::var("AUTOMATA_API_PORT")
        .ok()
        .and_then(|value| value.trim().parse::<u16>().ok())
        .filter(|port| *port > 0)
        .unwrap_or(DEFAULT_API_PORT);

    ResolvedApiConfig { host, port }
}

impl ResolvedApiConfig {
    fn address(&self) -> String {
        format!("{}:{}", self.host, self.port)
    }

    fn to_response(&self) -> ApiConfigResponse {
        ApiConfigResponse {
            http_base_url: format!("http://{}", self.address()),
            ws_chat_url: format!("ws://{}/ws/chat", self.address()),
            default_working_directory: resolve_workspace_dir().to_string_lossy().to_string(),
        }
    }
}
