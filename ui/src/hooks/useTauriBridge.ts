import { useState } from "react";
import { invoke } from "@tauri-apps/api/core";
import { open } from "@tauri-apps/plugin-dialog";

export function useTauriBridge() {
  const [bridgeStatus, setBridgeStatus] = useState("尚未检查桌面连接");

  async function runBridgeCheck() {
    try {
      setBridgeStatus(await invoke<string>("agent_status", { workspace: "automata" }));
    } catch {
      setBridgeStatus("请通过 npm run tauri dev 启动，以使用桌面连接");
    }
  }

  async function chooseDirectory(): Promise<string | null> {
    const selected = await open({
      directory: true,
      multiple: false,
      title: "选择工作目录",
    });

    return typeof selected === "string" && selected.trim() ? selected : null;
  }

  return {
    bridgeStatus,
    runBridgeCheck,
    chooseDirectory,
  };
}
