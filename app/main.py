import asyncio
import json
from html import escape
from datetime import datetime
from contextlib import asynccontextmanager
from fastapi import FastAPI, Form, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from app.db import init_models
from app.mq import mq, MQ_QUEUE_PERSISTED, MQ_ROUTING_KEY_CREATED
from app.ws import manager


templates = Jinja2Templates(directory="app/templates")
persisted_task: asyncio.Task | None = None


async def consume_persisted_events():
    """Читает очередь «сообщение уже в БД»: worker записал строку и опубликовал chat.message.persisted.
    Здесь web превращает событие в HTML и рассылает только подключениям этой комнаты (не через RabbitMQ к клиентам)."""
    queue = await mq.channel.get_queue(MQ_QUEUE_PERSISTED)
    async with queue.iterator() as iterator:
        async for message in iterator:
            # requeue=True: при сбое обработки сообщение вернётся в очередь
            async with message.process(requeue=True):
                data = json.loads(message.body.decode("utf-8"))
                # hx-swap-oob: фрагмент подменяет/дополняет #messages без перезагрузки страницы
                html_fragment = (
                    '<div id="messages" hx-swap-oob="beforeend">'
                    '<article class="msg">'
                    "<header>"
                    f"<strong>{escape(str(data['username']))}</strong>"
                    f"<small>{escape(str(data['created_at']))}</small>"
                    "</header>"
                    f"<p>{escape(str(data['text']))}</p>"
                    "</article>"
                    "</div>"
                )
                await manager.broadcast(
                    data["room_id"],
                    html_fragment,
                )


@asynccontextmanager
async def lifespan(app: FastAPI):
    global persisted_task
    await init_models()
    await mq.connect()
    # Фоном слушаем «persisted», иначе клиенты не получат сообщение после записи worker
    persisted_task = asyncio.create_task(consume_persisted_events())
    yield
    if persisted_task:
        persisted_task.cancel()
    await mq.close()


app = FastAPI(title="Socket MQ Chat", lifespan=lifespan)
app.mount("/static", StaticFiles(directory="app/static"), name="static")


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


@app.post("/rooms/{room_id}/messages")
async def send_message(room_id: str, username: str = Form(...), text: str = Form(...)):
    # Тот же путь в очередь, что и у WebSocket; удобно для POST-скриптов (например tests/ws_check.py)
    username = username.strip()
    text = text.strip()
    if not username or not text:
        raise HTTPException(status_code=400, detail="username/text cannot be empty")

    payload = {
        "room_id": room_id,
        "username": username,
        "text": text,
        "created_at": datetime.utcnow().isoformat(),
    }
    await mq.publish(MQ_ROUTING_KEY_CREATED, payload)
    return {"status": "queued"}


@app.websocket("/ws/{room_id}")
async def ws_room(ws: WebSocket, room_id: str):
    await manager.connect(room_id, ws)
    try:
        while True:
            # htmx-ext-ws с ws-send шлёт текстовые кадры: JSON с полями формы
            raw = await ws.receive_text()
            try:
                data = json.loads(raw)
            except json.JSONDecodeError:
                continue

            username = str(data.get("username", "")).strip()
            text = str(data.get("text", "")).strip()
            if not username or not text:
                continue

            payload = {
                "room_id": room_id,
                "username": username,
                "text": text,
                "created_at": datetime.utcnow().isoformat(),
            }
            await mq.publish(MQ_ROUTING_KEY_CREATED, payload)
    except WebSocketDisconnect:
        manager.disconnect(room_id, ws)
    except Exception:
        manager.disconnect(room_id, ws)


@app.get("/health")
async def health():
    return {"status": "ok"}
