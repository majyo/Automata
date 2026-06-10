import { useState } from "react";
import { invoke } from "@tauri-apps/api/core";
import { open } from "@tauri-apps/plugin-dialog";

export function useTauriBridge() {
  const [bridgeStatus, setBridgeStatus] = useState("Desktop bridge not checked");

  async function runBridgeCheck() {
    try {
      setBridgeStatus(await invoke<string>("agent_status", { workspace: "automata" }));
    } catch {
      setBridgeStatus("Open with npm run tauri dev to use the desktop bridge");
    }
  }

  async function chooseDirectory(): Promise<string | null> {
    const selected = await open({
      directory: true,
      multiple: false,
      title: "Select working directory",
    });

    return typeof selected === "string" && selected.trim() ? selected : null;
  }

  return {
    bridgeStatus,
    runBridgeCheck,
    chooseDirectory,
  };
}
