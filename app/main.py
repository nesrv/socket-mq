import json
from html import escape
from datetime import datetime
from typing import TypedDict

from fastapi import FastAPI, Request, WebSocket
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from app.ws import manager


class ChatMessage(TypedDict):
    username: str
    text: str
    created_at: str


app = FastAPI(title="Socket MQ Chat V0")
app.mount("/static", StaticFiles(directory="app/static"), name="static")
templates = Jinja2Templates(directory="app/templates")

# In-memory history for demo purposes only.
messages_by_room: dict[str, list[ChatMessage]] = {}


def render_message_fragment(msg: ChatMessage) -> str:
    return (
        '<div id="messages" hx-swap-oob="beforeend">'
        '<article class="msg">'
        "<header>"
        f"<strong>{escape(msg['username'])}</strong>"
        f"<small>{escape(msg['created_at'])}</small>"
        "</header>"
        f"<p>{escape(msg['text'])}</p>"
        "</article>"
        "</div>"
    )


def parse_ws_payload(raw: str) -> ChatMessage | None:
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return None

    username = str(data.get("username", "")).strip()
    text = str(data.get("text", "")).strip()
    if not username or not text:
        return None

    return {
        "username": username,
        "text": text,
        "created_at": datetime.utcnow().isoformat(),
    }


@app.get("/", response_class=HTMLResponse)
async def home(request: Request):
    return templates.TemplateResponse(
        request=request,
        name="room.html",
        context={"request": request, "room_id": "room-1"},
    )


@app.get("/rooms/{room_id}", response_class=HTMLResponse)
async def room_page(request: Request, room_id: str):
    return templates.TemplateResponse(
        request=request,
        name="room.html",
        context={"request": request, "room_id": room_id},
    )


@app.websocket("/ws/{room_id}")
async def ws_room(ws: WebSocket, room_id: str):
    await manager.connect(room_id, ws)
    try:
        # Send history to newly connected client.
        for msg in messages_by_room.get(room_id, []):
            await ws.send_text(render_message_fragment(msg))

        while True:
            raw = await ws.receive_text()
            msg = parse_ws_payload(raw)
            if not msg:
                continue

            messages_by_room.setdefault(room_id, []).append(msg)
            await manager.broadcast(room_id, render_message_fragment(msg))
    finally:
        manager.disconnect(room_id, ws)


@app.get("/health")
async def health():
    return {"status": "ok"}
