# Лабораторная работа: чат на FastAPI + HTMX + RabbitMQ (упрощенная структура)

## 1. Цель

Сделать учебный realtime-чат, где:
- фронтенд отправляет сообщение через HTMX;
- FastAPI публикует событие в RabbitMQ;
- worker сохраняет сообщение в PostgreSQL;
- FastAPI рассылает сохраненное сообщение в WebSocket.

---

## 2. Упрощенная структура проекта

```txt
socket-mq/
  app/
    main.py
    db.py
    mq.py
    ws.py
    templates/
      base.html
      room.html
    static/
      styles.css
  worker.py
  tests/
    test_health.py
  requirements.txt
  Dockerfile
  docker-compose.yml
  .env.example
  README.md
```

---

## 3. Подготовка окружения

## 3.1 `.env.example`

```env
APP_HOST=0.0.0.0
APP_PORT=8000

DATABASE_URL=postgresql+asyncpg://chat:chat@postgres:5432/chat
RABBITMQ_URL=amqp://guest:guest@rabbitmq:5672/

MQ_EXCHANGE=chat.events
MQ_QUEUE_INCOMING=chat.messages.incoming
MQ_QUEUE_PERSISTED=chat.messages.persisted
MQ_ROUTING_KEY_CREATED=chat.message.created
MQ_ROUTING_KEY_PERSISTED=chat.message.persisted
```

## 3.2 `requirements.txt`

```txt
fastapi
uvicorn[standard]
jinja2
python-multipart
aio-pika
sqlalchemy
asyncpg
pytest
httpx
```

## 3.3 `Dockerfile`

```dockerfile
FROM python:3.13-slim

WORKDIR /code
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .
```

## 3.4 `docker-compose.yml`

```yaml
services:
  web:
    build: .
    container_name: socket_mq_web
    command: uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
    env_file:
      - .env
    volumes:
      - ./:/code
    ports:
      - "8000:8000"
    depends_on:
      - postgres
      - rabbitmq

  worker:
    build: .
    container_name: socket_mq_worker
    command: python worker.py
    env_file:
      - .env
    volumes:
      - ./:/code
    depends_on:
      - postgres
      - rabbitmq

  postgres:
    image: postgres:16
    container_name: socket_mq_postgres
    environment:
      POSTGRES_DB: chat
      POSTGRES_USER: chat
      POSTGRES_PASSWORD: chat
    ports:
      - "5432:5432"

  rabbitmq:
    image: rabbitmq:3-management
    container_name: socket_mq_rabbitmq
    ports:
      - "5672:5672"
      - "15672:15672"
```

Запуск:
```bash
cp .env.example .env
docker compose up -d --build
```

Проверка:
- `http://localhost:8000/health` возвращает `{"status":"ok"}`;
- RabbitMQ UI доступен на `http://localhost:15672`.

---

## 4. Backend (упрощенные Python-файлы)

## 4.1 `app/db.py`

```python
from datetime import datetime
import os
from sqlalchemy import DateTime, Integer, String, Text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession


DATABASE_URL = os.getenv("DATABASE_URL", "postgresql+asyncpg://chat:chat@postgres:5432/chat")


class Base(DeclarativeBase):
    pass


class Message(Base):
    __tablename__ = "messages"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    room_id: Mapped[str] = mapped_column(String(100), index=True)
    username: Mapped[str] = mapped_column(String(64))
    text: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, index=True)


engine = create_async_engine(DATABASE_URL, future=True, echo=False)
SessionLocal = async_sessionmaker(bind=engine, class_=AsyncSession, expire_on_commit=False)


async def init_models() -> None:
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
```

## 4.2 `app/ws.py`

```python
from collections import defaultdict
from fastapi import WebSocket


class WSManager:
    def __init__(self) -> None:
        self.rooms: dict[str, set[WebSocket]] = defaultdict(set)

    async def connect(self, room_id: str, ws: WebSocket) -> None:
        await ws.accept()
        self.rooms[room_id].add(ws)

    def disconnect(self, room_id: str, ws: WebSocket) -> None:
        if room_id in self.rooms and ws in self.rooms[room_id]:
            self.rooms[room_id].remove(ws)
            if not self.rooms[room_id]:
                del self.rooms[room_id]

    async def broadcast(self, room_id: str, payload: str) -> None:
        dead = []
        for ws in self.rooms.get(room_id, set()):
            try:
                await ws.send_text(payload)
            except Exception:
                dead.append(ws)
        for ws in dead:
            self.disconnect(room_id, ws)


manager = WSManager()
```

## 4.3 `app/mq.py`

```python
import json
import os
import asyncio
import aio_pika


RABBITMQ_URL = os.getenv("RABBITMQ_URL", "amqp://guest:guest@rabbitmq:5672/")
MQ_EXCHANGE = os.getenv("MQ_EXCHANGE", "chat.events")
MQ_QUEUE_INCOMING = os.getenv("MQ_QUEUE_INCOMING", "chat.messages.incoming")
MQ_QUEUE_PERSISTED = os.getenv("MQ_QUEUE_PERSISTED", "chat.messages.persisted")
MQ_ROUTING_KEY_CREATED = os.getenv("MQ_ROUTING_KEY_CREATED", "chat.message.created")
MQ_ROUTING_KEY_PERSISTED = os.getenv("MQ_ROUTING_KEY_PERSISTED", "chat.message.persisted")


class MQ:
    def __init__(self):
        self.connection = None
        self.channel = None
        self.exchange = None

    async def connect(self):
        last_error = None
        for _ in range(20):
            try:
                self.connection = await aio_pika.connect_robust(RABBITMQ_URL)
                break
            except Exception as exc:
                last_error = exc
                await asyncio.sleep(1)
        if self.connection is None:
            raise RuntimeError(f"Cannot connect to RabbitMQ: {last_error}")
        self.channel = await self.connection.channel()
        self.exchange = await self.channel.declare_exchange(
            MQ_EXCHANGE, aio_pika.ExchangeType.TOPIC, durable=True
        )
        await self.channel.declare_queue(MQ_QUEUE_INCOMING, durable=True)
        await self.channel.declare_queue(MQ_QUEUE_PERSISTED, durable=True)
        q_in = await self.channel.get_queue(MQ_QUEUE_INCOMING)
        q_out = await self.channel.get_queue(MQ_QUEUE_PERSISTED)
        await q_in.bind(self.exchange, routing_key=MQ_ROUTING_KEY_CREATED)
        await q_out.bind(self.exchange, routing_key=MQ_ROUTING_KEY_PERSISTED)

    async def publish(self, routing_key: str, payload: dict):
        body = json.dumps(payload).encode("utf-8")
        msg = aio_pika.Message(body=body, delivery_mode=aio_pika.DeliveryMode.PERSISTENT)
        await self.exchange.publish(msg, routing_key=routing_key)

    async def close(self):
        if self.connection:
            await self.connection.close()


mq = MQ()
```

## 4.4 `app/main.py`

```python
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
    queue = await mq.channel.get_queue(MQ_QUEUE_PERSISTED)
    async with queue.iterator() as iterator:
        async for message in iterator:
            async with message.process(requeue=True):
                data = json.loads(message.body.decode("utf-8"))
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
```

## 4.5 `worker.py`

```python
import asyncio
import json
from sqlalchemy import insert
import aio_pika

from app.db import Message, SessionLocal, init_models
from app.mq import (
    RABBITMQ_URL,
    MQ_EXCHANGE,
    MQ_QUEUE_INCOMING,
    MQ_ROUTING_KEY_CREATED,
    MQ_ROUTING_KEY_PERSISTED,
)


async def run_worker():
    await init_models()
    connection = await aio_pika.connect_robust(RABBITMQ_URL)
    channel = await connection.channel()
    exchange = await channel.declare_exchange(
        MQ_EXCHANGE, aio_pika.ExchangeType.TOPIC, durable=True
    )
    queue = await channel.declare_queue(MQ_QUEUE_INCOMING, durable=True)
    await queue.bind(exchange, routing_key=MQ_ROUTING_KEY_CREATED)

    async with queue.iterator() as iterator:
        async for incoming in iterator:
            async with incoming.process(requeue=True):
                payload = json.loads(incoming.body.decode("utf-8"))

                async with SessionLocal() as session:
                    stmt = insert(Message).values(
                        room_id=payload["room_id"],
                        username=payload["username"],
                        text=payload["text"],
                    ).returning(Message.id, Message.created_at)
                    row = (await session.execute(stmt)).one()
                    await session.commit()

                persisted = {
                    "id": row.id,
                    "room_id": payload["room_id"],
                    "username": payload["username"],
                    "text": payload["text"],
                    "created_at": row.created_at.isoformat(),
                }
                msg = aio_pika.Message(
                    body=json.dumps(persisted).encode("utf-8"),
                    delivery_mode=aio_pika.DeliveryMode.PERSISTENT,
                )
                await exchange.publish(msg, routing_key=MQ_ROUTING_KEY_PERSISTED)


if __name__ == "__main__":
    asyncio.run(run_worker())
```

---

## 5. Frontend (HTML + HTMX ws extension)

## 5.1 `app/templates/base.html`

```html
<!doctype html>
<html lang="ru">
  <head>
    <meta charset="utf-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1" />
    <title>Socket MQ Chat</title>
    <script src="https://unpkg.com/htmx.org@1.9.12"></script>
    <script src="https://unpkg.com/htmx-ext-ws@2.0.2/ws.js"></script>
    <link rel="stylesheet" href="/static/styles.css" />
  </head>
  <body><main class="container">{% block content %}{% endblock %}</main></body>
</html>
```

## 5.2 `app/templates/room.html`

```html
{% extends "base.html" %}
{% block content %}
<h1>Комната: {{ room_id }}</h1>

<section id="chat-root" hx-ext="ws" ws-connect="/ws/{{ room_id }}">
  <div id="messages" class="messages"></div>
  <form id="message-form" class="message-form" ws-send>
    <input type="text" name="username" placeholder="Ваш ник" required />
    <input type="text" name="text" placeholder="Сообщение" required />
    <button type="submit">Отправить</button>
  </form>
</section>
{% endblock %}
```

## 5.3 `app/static/styles.css`

```css
body {
  margin: 0;
  font-family: Arial, sans-serif;
  background: #f5f6f8;
}

.container {
  max-width: 760px;
  margin: 0 auto;
  padding: 24px;
}

.messages {
  min-height: 320px;
  max-height: 500px;
  overflow-y: auto;
  background: #fff;
  border: 1px solid #ddd;
  border-radius: 8px;
  padding: 12px;
  margin-bottom: 12px;
}

.msg {
  border-bottom: 1px solid #eee;
  padding: 8px 0;
}

.msg header {
  display: flex;
  justify-content: space-between;
}

.message-form {
  display: grid;
  grid-template-columns: 160px 1fr 120px;
  gap: 8px;
}
```

---

## 6. Проверка по шагам

## Шаг 1: инфраструктура
Команда:
```bash
docker compose up -d postgres rabbitmq
```
Проверка:
- оба контейнера `running`;
- RabbitMQ UI открывается.

## Шаг 2: web
Команда:
```bash
docker compose up -d --build web
```
Проверка:
- `/health` -> ok;
- `/rooms/room-1` открывается.

## Шаг 3: worker
Команда:
```bash
docker compose up -d --build worker
```
Проверка:
- worker живой, не падает при старте.
- в логах нет ошибки `relation "messages" does not exist`.

## Шаг 4: realtime
Проверка:
1. Откройте 2 вкладки `/rooms/room-1`.
2. Отправьте сообщение из 1 вкладки.
3. Сообщение должно появиться в обеих вкладках.
4. Убедитесь, что подключается `ws` extension (в DevTools должен быть WS `/ws/room-1`).
4. Если сообщения не появляются, проверьте:
   - `docker compose logs web --tail 100`;
   - `docker compose logs worker --tail 100`;
   - `docker compose exec -T rabbitmq rabbitmqctl list_queues name messages consumers`.

## Шаг 5: база
Проверка:
- в таблице `messages` появляются записи после отправки;
- после refresh сообщения не исчезают (если реализуете подгрузку истории).

---

## 7. Минимальный тест

`tests/test_health.py`:

```python
from fastapi.testclient import TestClient
from app.main import app


def test_health():
    client = TestClient(app)
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
```

Запуск:
```bash
docker compose run --rm web pytest -q
```

---

## 8. Что сдавать

- репозиторий с кодом;
- скрин работы чата в двух вкладках;
- скрин RabbitMQ/worker логов;
- `README` с запуском;
- короткое описание потока события:
  `HTTP -> MQ created -> Worker -> MQ persisted -> WS`.
