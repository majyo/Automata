use std::fs;
use std::net::TcpListener;
use std::sync::Mutex;

use tauri::{Manager, WindowEvent};
use tauri_plugin_shell::{
    process::{CommandChild, CommandEvent},
    ShellExt,
};

const API_ADDRESS: &str = "127.0.0.1:8765";
const API_SIDECAR: &str = "automata-api";

#[derive(Default)]
struct BackendState {
    child: Mutex<Option<CommandChild>>,
    status: Mutex<String>,
}

#[tauri::command]
fn agent_status(workspace: &str, state: tauri::State<'_, BackendState>) -> String {
    let backend_status = state.status.lock().expect("backend status lock").clone();
    format!("Tauri bridge online for {workspace}. Backend: {backend_status}")
}

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    tauri::Builder::default()
        .manage(BackendState {
            child: Mutex::new(None),
            status: Mutex::new("Starting".to_string()),
        })
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
        .invoke_handler(tauri::generate_handler![agent_status])
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

    if TcpListener::bind(API_ADDRESS).is_err() {
        set_backend_status(
            &app_handle,
            format!("Port {API_ADDRESS} is already in use. Close the existing process and restart."),
        );
        return;
    }

    let data_dir = match app.path().app_data_dir() {
        Ok(path) => path,
        Err(error) => {
            set_backend_status(&app_handle, format!("Failed to resolve app data dir: {error}"));
            return;
        }
    };

    if let Err(error) = fs::create_dir_all(&data_dir) {
        set_backend_status(&app_handle, format!("Failed to create app data dir: {error}"));
        return;
    }

    let sidecar = match app.shell().sidecar(API_SIDECAR) {
        Ok(command) => command,
        Err(error) => {
            set_backend_status(&app_handle, format!("Failed to locate sidecar: {error}"));
            return;
        }
    };

    match sidecar.env("AUTOMATA_DATA_DIR", data_dir).spawn() {
        Ok((mut receiver, child)) => {
            {
                let state = app.state::<BackendState>();
                *state.child.lock().expect("backend child lock") = Some(child);
            }

            set_backend_status(&app_handle, "Starting sidecar");
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
