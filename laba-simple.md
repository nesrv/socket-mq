# Лабораторная работа: простой чат на FastAPI + HTMX ws extension

## Часть 1: Простой чат на сокетах

Этот вариант используется как быстрый старт:
- только `FastAPI + HTMX ws extension`;
- без `RabbitMQ`;
- без `PostgreSQL`;
- сообщения хранятся в памяти процесса (`in-memory`).

### Цель варианта 0
Понять базовый realtime-поток:
1. браузер подключается к `/ws/{room_id}`;
2. форма отправляется через `ws-send`;
3. сервер принимает сообщение и рассылает его в комнату;
4. сообщение появляется во всех открытых вкладках комнаты.

### Ограничения варианта 0
- после перезапуска сервера история исчезает;
- нет гарантированной доставки;
- нет масштабирования между несколькими инстансами.

### Упрощенная структура (вариант 0)
```txt
socket-mq/
  app/
    main.py
    ws.py
    templates/
      base.html
      room.html
    static/
      styles.css
  requirements.txt
  Dockerfile
  docker-compose.yml
```

## 1. Подготовка окружения

### 1.1 `.env.example`

```env
APP_HOST=0.0.0.0
APP_PORT=8100
```

### 1.2 `requirements.txt`

```txt
fastapi
uvicorn[standard]
jinja2
```

### 1.3 `Dockerfile`

```dockerfile
FROM python:3.13-slim

WORKDIR /code
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .
```

### 1.4 `docker-compose.yml`

```yaml
services:
  web:
    build: .
    container_name: socket_mq_web_v0
    command: uvicorn app.main:app --host 0.0.0.0 --port 8100 --reload
    env_file:
      - .env
    volumes:
      - ./:/code
    ports:
      - "8100:8100"
```




### 1.5 Подготовьте структуру папок

Перед созданием файлов создайте каталоги проекта.

Linux/macOS:
```bash
mkdir -p app/templates app/static
```

Windows PowerShell:
```powershell
mkdir app, app\templates, app\static
```

### 1.6 Пошагово: создаем файлы простого чата (текущий рабочий вариант)

#### Шаг 1. Создайте `app/ws.py` (менеджер WS комнат)
```python
from collections import defaultdict
from fastapi import WebSocket


class WSManager:
    def __init__(self) -> None:
        # room_id -> набор активных WebSocket-подключений.
        self.rooms: dict[str, set[WebSocket]] = defaultdict(set)

    async def connect(self, room_id: str, ws: WebSocket) -> None:
        # При подключении клиент должен быть принят и добавлен в комнату.
        await ws.accept()
        self.rooms[room_id].add(ws)

    def disconnect(self, room_id: str, ws: WebSocket) -> None:
        # Удаляем клиента и чистим пустую комнату.
        if room_id in self.rooms and ws in self.rooms[room_id]:
            self.rooms[room_id].remove(ws)
            if not self.rooms[room_id]:
                del self.rooms[room_id]

    async def broadcast(self, room_id: str, html_fragment: str) -> None:
        # Рассылка HTML-фрагмента всем участникам комнаты.
        dead: list[WebSocket] = []
        for ws in self.rooms.get(room_id, set()):
            try:
                await ws.send_text(html_fragment)
            except Exception:
                dead.append(ws)
        for ws in dead:
            self.disconnect(room_id, ws)


manager = WSManager()
```

#### Шаг 2. Создайте `app/main.py` (HTTP + WS + in-memory история)
```python
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
# История в памяти процесса: после рестарта сервера очищается.
messages_by_room: dict[str, list[ChatMessage]] = {}


def render_message_fragment(msg: ChatMessage) -> str:
    # Формируем HTML для HTMX out-of-band вставки в блок #messages.
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
    # ws-send присылает JSON; превращаем его в валидное сообщение.
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
        # Новому клиенту сразу отправляем накопленную историю комнаты.
        for msg in messages_by_room.get(room_id, []):
            await ws.send_text(render_message_fragment(msg))

        while True:
            raw = await ws.receive_text()
            msg = parse_ws_payload(raw)
            if not msg:
                continue

            # Сохраняем сообщение в in-memory историю.
            messages_by_room.setdefault(room_id, []).append(msg)
            # Рассылаем сообщение всем клиентам комнаты.
            await manager.broadcast(room_id, render_message_fragment(msg))
    finally:
        # Гарантированно удаляем соединение при любом завершении цикла.
        manager.disconnect(room_id, ws)


@app.get("/health")
async def health():
    return {"status": "ok"}
```

#### Шаг 3. Создайте шаблон `app/templates/base.html`
```html
<!doctype html>
<html lang="ru">
  <head>
    <meta charset="utf-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1" />
    <title>Socket MQ Chat V0</title>
    <!-- Базовая библиотека HTMX -->
    <script src="https://unpkg.com/htmx.org@1.9.12"></script>
    <!-- Расширение для WebSocket-интеграции (ws-connect / ws-send) -->
    <script src="https://unpkg.com/htmx-ext-ws@2.0.2/ws.js"></script>
    <link rel="stylesheet" href="/static/styles.css" />
  </head>
  <body>
    <!-- Контент каждой страницы подставляется в этот блок -->
    <main class="container">{% block content %}{% endblock %}</main>
  </body>
</html>
```

#### Шаг 4. Создайте шаблон `app/templates/room.html`
```html
{% extends "base.html" %}
{% block content %}
<h1>Комната: {{ room_id }}</h1>

<!-- Подключение к WebSocket комнаты -->
<section id="chat-root" hx-ext="ws" ws-connect="/ws/{{ room_id }}">
  <!-- Сюда сервер добавляет сообщения через hx-swap-oob -->
  <div id="messages" class="messages"></div>
  <!-- Форма отправляется по WS благодаря ws-send -->
  <form id="message-form" class="message-form" ws-send>
    <input type="text" name="username" placeholder="Ваш ник" required />
    <input type="text" name="text" placeholder="Сообщение" required />
    <button type="submit">Отправить</button>
  </form>
</section>
{% endblock %}
```

#### Шаг 5. Создайте `app/static/styles.css`
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


Запуск:
```bash
cp .env.example .env
docker compose up -d --build
```


Проверка:
- `http://localhost:8100/health` возвращает `{"status":"ok"}`;
- страница чата открывается на `http://localhost:8100/rooms/room-1`.

---

## 9. Быстрый деплой на VPS через git + ssh

Данные сервера:
- SSH: `alekseeva@81.90.182.174`
- Домен: `alekseeva.h1n.ru`

### 9.1 Первый вход
```bash
ssh alekseeva@81.90.182.174
```

### 9.2 Подготовка сервера (один раз)
```bash
sudo systemctl enable --now docker
sudo usermod -aG docker $USER
```
Пояснение:
- `sudo systemctl enable --now docker` - запускает Docker сейчас и включает автозапуск после перезагрузки сервера.
- `sudo usermod -aG docker $USER` - добавляет текущего пользователя в группу `docker`, чтобы запускать команды без `sudo`.

После этого переподключитесь по SSH.

### 9.3 Клонирование проекта
```bash
mkdir -p /opt/socket-mq
cd /opt/socket-mq
git clone <URL_ВАШЕГО_РЕПОЗИТОРИЯ> app
cd app
```

### 9.4 Настройка `.env`
Создайте файл `.env` рядом с `docker-compose.yml`:

```env
APP_HOST=0.0.0.0
APP_PORT=8100
```

### 9.5 Запуск приложения
```bash
docker compose up -d --build
docker compose ps
```

### 9.6 Nginx конфиг для домена
Создайте файл `/etc/nginx/sites-available/alekseeva.h1n.ru`:

```nginx
server {
    listen 80;
    server_name alekseeva.h1n.ru;

    location / {
        proxy_pass http://127.0.0.1:8100;
        proxy_http_version 1.1;
        proxy_set_header Host $host;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }

    location /ws/ {
        proxy_pass http://127.0.0.1:8100/ws/;
        proxy_http_version 1.1;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection "upgrade";
        proxy_set_header Host $host;
    }
}
```

Активируйте конфиг:
```bash
sudo ln -s /etc/nginx/sites-available/alekseeva.h1n.ru /etc/nginx/sites-enabled/
sudo nginx -t
sudo systemctl reload nginx
```

### 9.7 HTTPS сертификат
```bash
sudo certbot --nginx -d alekseeva.h1n.ru
```

### 9.8 Проверка
- `http://alekseeva.h1n.ru/health`
- `https://alekseeva.h1n.ru/health`

### 9.9 Обновление деплоя (каждый раз)
```bash
ssh alekseeva@81.90.182.174
cd /opt/socket-mq/app
git pull
docker compose up -d --build
```



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

---

