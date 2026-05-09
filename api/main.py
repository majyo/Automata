import asyncio
import json
from itertools import cycle
from typing import Any

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware


app = FastAPI(title="Automata Agent Demo API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:1420", "http://127.0.0.1:1420", "tauri://localhost"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


FAKE_REPLIES = [
    [
        "我会先扫描当前 workspace，确认前后端目录、可运行脚本和已有约定。\n\n",
        "从现在的结构看，第一步可以把 Python 后端固定成 FastAPI，并提供 `/health`、`/ws/chat` 两个最小入口。\n\n",
        "UI 侧保持一个很薄的 transport 层：负责连接 WebSocket、发送用户输入、接收 token 事件，然后把事件写入对话流。这样后面替换成真实 coding agent 时，只需要换后端执行器。",
    ],
    [
        "收到。我会把这个请求拆成三个执行阶段：\n\n",
        "1. 建立任务上下文：读取项目结构、识别当前分支和工作区状态。\n",
        "2. 生成执行计划：列出需要编辑的文件，并标出验证命令。\n",
        "3. 流式回传进度：每完成一个阶段就向 UI 推送事件，而不是等最终结果一次性返回。\n\n",
        "当前 demo 使用 fake reply，但事件形状会尽量贴近真实 agent。",
    ],
    [
        "这里适合保留高密度布局：左侧是会话与项目，中间是主对话流，右侧是任务、文件改动和运行状态。\n\n",
        "真正接入 agent 后，我建议消息流里混合三类事件：普通文本、工具调用状态、代码 diff 摘要。文本继续平铺，工具状态用紧凑行展示，diff 则进入右侧 inspector。",
    ],
]


reply_cycle = cycle(FAKE_REPLIES)


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


@app.websocket("/ws/chat")
async def chat(websocket: WebSocket) -> None:
    await websocket.accept()
    await websocket.send_json({"type": "ready", "message": "Fake agent backend connected"})

    try:
        while True:
            payload = await receive_payload(websocket)
            if payload.get("type") != "prompt":
                await websocket.send_json({"type": "error", "message": "Unsupported message type"})
                continue

            prompt = str(payload.get("prompt", "")).strip()
            reply = next(reply_cycle)
            await stream_reply(websocket, prompt, reply)
    except WebSocketDisconnect:
        return


async def receive_payload(websocket: WebSocket) -> dict[str, Any]:
    message = await websocket.receive_text()
    try:
        payload = json.loads(message)
    except json.JSONDecodeError:
        return {"type": "invalid"}

    if not isinstance(payload, dict):
        return {"type": "invalid"}

    return payload


async def stream_reply(websocket: WebSocket, prompt: str, reply: list[str]) -> None:
    await websocket.send_json({"type": "started", "prompt": prompt})

    for block in reply:
        for chunk in chunk_text(block):
            await websocket.send_json({"type": "token", "content": chunk})
            await asyncio.sleep(0.045)

    await websocket.send_json({"type": "done"})


def chunk_text(text: str, size: int = 8) -> list[str]:
    return [text[index : index + size] for index in range(0, len(text), size)]
