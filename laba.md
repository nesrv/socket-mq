# Лабораторная работа (максимально подробная): Realtime чат на FastAPI + HTMX + RabbitMQ + PostgreSQL

## Вариант 0 (базовый): простой чат без RabbitMQ и PostgreSQL

Этот вариант можно дать студентам первым шагом:
- только `FastAPI + HTMX ws extension`;
- без `RabbitMQ`;
- без `PostgreSQL`;
- сообщения хранятся в памяти процесса (`in-memory`).

### Цель варианта 0
Понять базовый realtime-поток:
1. браузер подключается к `/ws/{room_id}`;
2. форма отправляется через `ws-send`;
3. сервер принимает сообщение и рассылает его в комнату;
4. сообщение отображается во всех открытых вкладках комнаты.

### Ограничения варианта 0
- после перезапуска сервера история пропадает;
- нет гарантированной доставки;
- нет горизонтального масштабирования.

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

### Минимальная проверка варианта 0
1. Открыть 2 вкладки: `http://localhost:8000/rooms/room-1`.
2. Отправить сообщение в первой вкладке.
3. Сообщение должно появиться в обеих вкладках.
4. Перезапустить `web` и убедиться, что история не сохранилась (это ожидаемо).

---

## 0) Что вы построите

Вы реализуете учебный проект, где:
- пользователь открывает страницу комнаты;
- отправляет сообщение через HTMX (HTTP);
- сервер публикует событие в RabbitMQ;
- worker сохраняет сообщение в PostgreSQL;
- WebSocket рассылает обновление всем клиентам комнаты;
- клиенты видят сообщение без перезагрузки.

В конце вы получите:
- локальный запуск через Docker Compose;
- тесты и healthcheck;
- CI (lint + tests + build);
- CD и деплой на VPS за Nginx + HTTPS.

---

## 1) Архитектура и ответственность компонентов

### 1.1 Компоненты
- `web` (FastAPI): HTTP + WebSocket + публикация в MQ.
- `worker` (Python consumer): чтение из MQ и запись в БД.
- `rabbitmq`: транспорт доменных событий.
- `postgres`: хранение сообщений и комнат.
- `nginx` (prod): reverse proxy и TLS.

### 1.2 Основной поток сообщения
1. Форма в браузере отправляется на `POST /rooms/{room_id}/messages`.
2. FastAPI публикует событие `chat.message.created`.
3. Worker принимает событие, валидирует, сохраняет в PostgreSQL.
4. Worker публикует событие `chat.message.persisted`.
5. Web читает `persisted`-событие и рассылает его по WS участникам комнаты.

Такой поток показывает студентам реальную event-driven схему.

---

## 2) Полная структура проекта

```txt
socket-mq/
  app/
    main.py
    config.py
    schemas.py
    db/
      session.py
      models.py
    services/
      mq.py
      ws_manager.py
      persisted_consumer.py
    api/
      pages.py
      messages.py
      websocket.py
    templates/
      base.html
      room.html
      partials/
        message_item.html
        message_list.html
    static/
      app.js
      styles.css
  worker/
    consumer.py
  tests/
    test_health.py
  migrations/
  docker/
    nginx/
      default.conf
  .github/
    workflows/
      ci.yml
      cd.yml
  Dockerfile
  docker-compose.yml
  docker-compose.prod.yml
  requirements.txt
  .env.example
  README.md
```

---

## 3) Установка и запуск (локально)

## 3.1 Файл `.env.example`

```env
APP_ENV=dev
APP_HOST=0.0.0.0
APP_PORT=8000
SECRET_KEY=change-me

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
alembic
pydantic
pydantic-settings
pytest
httpx
ruff
```

## 3.3 `docker-compose.yml` (dev)

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
    command: python -m worker.consumer
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
    volumes:
      - pg_data:/var/lib/postgresql/data

  rabbitmq:
    image: rabbitmq:3-management
    container_name: socket_mq_rabbitmq
    ports:
      - "5672:5672"
      - "15672:15672"
    volumes:
      - rabbit_data:/var/lib/rabbitmq

volumes:
  pg_data:
  rabbit_data:
```

## 3.4 `Dockerfile`

```dockerfile
FROM python:3.13-slim

WORKDIR /code

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .
```

## 3.5 Запуск

```bash
cp .env.example .env
docker compose up -d --build
```

Проверка:
- `http://localhost:8000/health` -> `{"status":"ok"}`;
- RabbitMQ UI: `http://localhost:15672` (`guest/guest`).

---

## 4) Backend: полный листинг Python файлов

## 4.1 `app/config.py`

```python
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    # Читаем переменные окружения из .env.
    # Это позволяет запускать один и тот же код в dev/prod с разными параметрами.
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")

    # Базовые настройки приложения.
    app_env: str = "dev"
    app_host: str = "0.0.0.0"
    app_port: int = 8000
    secret_key: str = "change-me"

    # Строки подключения к БД и RabbitMQ приходят только из окружения.
    database_url: str
    rabbitmq_url: str

    # Имена exchange/очередей/ключей маршрутизации.
    # Вынесены в конфиг, чтобы не хардкодить в коде сервисов.
    mq_exchange: str = "chat.events"
    mq_queue_incoming: str = "chat.messages.incoming"
    mq_queue_persisted: str = "chat.messages.persisted"
    mq_routing_key_created: str = "chat.message.created"
    mq_routing_key_persisted: str = "chat.message.persisted"


# Глобальный объект настроек, который импортируется в модулях.
settings = Settings()
```

## 4.2 `app/schemas.py`

```python
from datetime import datetime
from pydantic import BaseModel, Field


class MessageCreate(BaseModel):
    room_id: str = Field(min_length=1, max_length=100)
    username: str = Field(min_length=1, max_length=64)
    text: str = Field(min_length=1, max_length=4000)


class MessagePersisted(BaseModel):
    id: int
    room_id: str
    username: str
    text: str
    created_at: datetime
```

## 4.3 `app/db/session.py`

```python
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
from app.config import settings


engine = create_async_engine(settings.database_url, future=True, echo=False)
SessionLocal = async_sessionmaker(bind=engine, class_=AsyncSession, expire_on_commit=False)


async def get_session():
    async with SessionLocal() as session:
        yield session
```

## 4.4 `app/db/models.py`

```python
from datetime import datetime
from sqlalchemy import String, Text, DateTime, Integer
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class Message(Base):
    __tablename__ = "messages"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    room_id: Mapped[str] = mapped_column(String(100), index=True)
    username: Mapped[str] = mapped_column(String(64))
    text: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, index=True)
```

## 4.5 `app/services/ws_manager.py`

```python
from collections import defaultdict
from fastapi import WebSocket


class ConnectionManager:
    def __init__(self) -> None:
        self.rooms: dict[str, set[WebSocket]] = defaultdict(set)

    async def connect(self, room_id: str, websocket: WebSocket) -> None:
        await websocket.accept()
        self.rooms[room_id].add(websocket)

    def disconnect(self, room_id: str, websocket: WebSocket) -> None:
        if room_id in self.rooms and websocket in self.rooms[room_id]:
            self.rooms[room_id].remove(websocket)
            if not self.rooms[room_id]:
                del self.rooms[room_id]

    async def broadcast_json(self, room_id: str, payload: dict) -> None:
        dead = []
        for ws in self.rooms.get(room_id, set()):
            try:
                await ws.send_json(payload)
            except Exception:
                dead.append(ws)
        for ws in dead:
            self.disconnect(room_id, ws)


manager = ConnectionManager()
```

## 4.6 `app/services/mq.py`

```python
import json
import aio_pika
from app.config import settings


class MQClient:
    def __init__(self) -> None:
        # robust-объекты автоматически переподключаются при кратковременном сетевом сбое.
        self.connection: aio_pika.RobustConnection | None = None
        self.channel: aio_pika.RobustChannel | None = None
        self.exchange: aio_pika.Exchange | None = None

    async def connect(self) -> None:
        # 1) Подключаемся к брокеру.
        self.connection = await aio_pika.connect_robust(settings.rabbitmq_url)
        self.channel = await self.connection.channel()

        # 2) Объявляем exchange типа TOPIC для гибкой маршрутизации.
        self.exchange = await self.channel.declare_exchange(
            settings.mq_exchange,
            aio_pika.ExchangeType.TOPIC,
            durable=True,
        )

        # 3) Объявляем очереди (durable, чтобы переживали рестарт брокера).
        await self.channel.declare_queue(settings.mq_queue_incoming, durable=True)
        await self.channel.declare_queue(settings.mq_queue_persisted, durable=True)

        q_incoming = await self.channel.get_queue(settings.mq_queue_incoming)
        q_persisted = await self.channel.get_queue(settings.mq_queue_persisted)

        # 4) Привязываем очереди к exchange через routing key.
        await q_incoming.bind(self.exchange, routing_key=settings.mq_routing_key_created)
        await q_persisted.bind(self.exchange, routing_key=settings.mq_routing_key_persisted)

    async def publish(self, routing_key: str, payload: dict) -> None:
        # Защита от публикации до инициализации MQ.
        if not self.exchange:
            raise RuntimeError("MQ exchange is not initialized")

        # Сериализуем событие в JSON.
        body = json.dumps(payload).encode("utf-8")
        # PERSISTENT + durable очередь = сообщение не теряется при рестарте.
        msg = aio_pika.Message(body=body, delivery_mode=aio_pika.DeliveryMode.PERSISTENT)
        await self.exchange.publish(msg, routing_key=routing_key)

    async def close(self) -> None:
        # Корректно закрываем соединение на shutdown приложения.
        if self.connection:
            await self.connection.close()


# Один клиент MQ на приложение.
mq_client = MQClient()
```

## 4.7 `app/services/persisted_consumer.py`

```python
import json
import aio_pika
from app.config import settings
from app.services.mq import mq_client
from app.services.ws_manager import manager


async def consume_persisted_events() -> None:
    channel = mq_client.channel
    if channel is None:
        raise RuntimeError("MQ channel is not initialized")

    queue = await channel.get_queue(settings.mq_queue_persisted)

    async with queue.iterator() as iterator:
        async for message in iterator:
            async with message.process(requeue=True):
                data = json.loads(message.body.decode("utf-8"))
                await manager.broadcast_json(
                    data["room_id"],
                    {
                        "type": "chat.message",
                        "id": data["id"],
                        "room_id": data["room_id"],
                        "username": data["username"],
                        "text": data["text"],
                        "created_at": data["created_at"],
                    },
                )
```

## 4.8 `app/api/pages.py`

```python
from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates


router = APIRouter()
templates = Jinja2Templates(directory="app/templates")


@router.get("/", response_class=HTMLResponse)
async def home(request: Request):
    return templates.TemplateResponse("room.html", {"request": request, "room_id": "room-1"})


@router.get("/rooms/{room_id}", response_class=HTMLResponse)
async def room_page(request: Request, room_id: str):
    return templates.TemplateResponse("room.html", {"request": request, "room_id": room_id})
```

## 4.9 `app/api/messages.py`

```python
from datetime import datetime
from fastapi import APIRouter, Form, HTTPException
from app.config import settings
from app.services.mq import mq_client


router = APIRouter()


@router.post("/rooms/{room_id}/messages")
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

    await mq_client.publish(settings.mq_routing_key_created, payload)
    return {"status": "queued"}
```

## 4.10 `app/api/websocket.py`

```python
from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from app.services.ws_manager import manager


router = APIRouter()


@router.websocket("/ws/{room_id}")
async def ws_room(websocket: WebSocket, room_id: str):
    await manager.connect(room_id, websocket)
    try:
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        manager.disconnect(room_id, websocket)
    except Exception:
        manager.disconnect(room_id, websocket)
```

## 4.11 `app/main.py`

```python
import asyncio
from contextlib import asynccontextmanager
from fastapi import FastAPI
from app.api.pages import router as pages_router
from app.api.messages import router as messages_router
from app.api.websocket import router as websocket_router
from app.services.mq import mq_client
from app.services.persisted_consumer import consume_persisted_events


consumer_task: asyncio.Task | None = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    global consumer_task
    # Startup: поднимаем MQ и запускаем фоновый consumer persisted-событий.
    await mq_client.connect()
    consumer_task = asyncio.create_task(consume_persisted_events())
    yield
    # Shutdown: останавливаем фоновые задачи и закрываем MQ соединение.
    if consumer_task:
        consumer_task.cancel()
    await mq_client.close()


app = FastAPI(title="Socket MQ Chat", lifespan=lifespan)
# Регистрируем все API-маршруты.
app.include_router(pages_router)
app.include_router(messages_router)
app.include_router(websocket_router)


@app.get("/health")
async def health():
    # Нужен для probe в CI/CD и после деплоя.
    return {"status": "ok"}
```

## 4.12 `worker/consumer.py`

```python
import asyncio
import json
import aio_pika
from sqlalchemy import insert
from app.config import settings
from app.db.session import SessionLocal
from app.db.models import Message


async def run_worker():
    # Worker потребляет только входящие сообщения от web-сервиса.
    connection = await aio_pika.connect_robust(settings.rabbitmq_url)
    channel = await connection.channel()

    # Exchange/queue должны совпадать с web-частью.
    exchange = await channel.declare_exchange(
        settings.mq_exchange,
        aio_pika.ExchangeType.TOPIC,
        durable=True,
    )
    queue = await channel.declare_queue(settings.mq_queue_incoming, durable=True)
    await queue.bind(exchange, routing_key=settings.mq_routing_key_created)

    async with queue.iterator() as iterator:
        async for incoming in iterator:
            # process(requeue=True): если исключение, сообщение вернется в очередь.
            async with incoming.process(requeue=True):
                payload = json.loads(incoming.body.decode("utf-8"))

                # Транзакционно сохраняем сообщение в PostgreSQL.
                async with SessionLocal() as session:
                    stmt = insert(Message).values(
                        room_id=payload["room_id"],
                        username=payload["username"],
                        text=payload["text"],
                    ).returning(Message.id, Message.created_at)
                    row = (await session.execute(stmt)).one()
                    await session.commit()

                # Формируем событие "persisted" для дальнейшей WS-рассылки.
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
                await exchange.publish(msg, routing_key=settings.mq_routing_key_persisted)


if __name__ == "__main__":
    # Точка входа worker-контейнера.
    asyncio.run(run_worker())
```

---

## 5) Frontend: полный листинг HTML/HTMX/JS/CSS

## 5.1 `app/templates/base.html`

```html
<!doctype html>
<html lang="ru">
  <head>
    <meta charset="utf-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1" />
    <title>Socket MQ Chat</title>
    <script src="https://unpkg.com/htmx.org@1.9.12"></script>
    <link rel="stylesheet" href="/static/styles.css" />
  </head>
  <body>
    <main class="container">
      {% block content %}{% endblock %}
    </main>
    <script src="/static/app.js"></script>
  </body>
</html>
```

## 5.2 `app/templates/room.html`

```html
{% extends "base.html" %}
{% block content %}
<h1>Комната: {{ room_id }}</h1>

<!-- Контейнер сообщений, сюда JS будет добавлять новые элементы -->
<section id="messages" class="messages"></section>

<form
  id="message-form"
  class="message-form"
  <!-- HTMX отправляет форму асинхронно без перезагрузки страницы -->
  hx-post="/rooms/{{ room_id }}/messages"
  <!-- Ответ сервера не вставляем в DOM, т.к. сообщение придет через WS -->
  hx-swap="none"
>
  <!-- Упрощенный формат авторизации для учебной работы -->
  <input type="text" name="username" placeholder="Ваш ник" required />
  <input type="text" name="text" placeholder="Сообщение" required />
  <button type="submit">Отправить</button>
</form>

<script>
  // Идентификатор комнаты нужен JS-клиенту для подключения к нужному WS-каналу.
  window.CHAT_ROOM_ID = "{{ room_id }}";
</script>
{% endblock %}
```

## 5.3 `app/templates/partials/message_item.html`

```html
<article class="msg">
  <header>
    <strong>{{ username }}</strong>
    <small>{{ created_at }}</small>
  </header>
  <p>{{ text }}</p>
</article>
```

## 5.4 `app/static/app.js`

```javascript
(() => {
  // roomId прокидывается из шаблона room.html
  const roomId = window.CHAT_ROOM_ID;
  if (!roomId) return;

  const messagesEl = document.getElementById("messages");
  const formEl = document.getElementById("message-form");
  const textInput = formEl.querySelector('input[name="text"]');

  const protocol = location.protocol === "https:" ? "wss" : "ws";
  let ws;
  let reconnectTimer = null;

  // Защита от XSS: никогда не вставляем пользовательский текст как raw HTML.
  function escapeHtml(value) {
    const div = document.createElement("div");
    div.textContent = value;
    return div.innerHTML;
  }

  function appendMessage(msg) {
    const item = document.createElement("article");
    item.className = "msg";
    item.innerHTML = `
      <header>
        <strong>${escapeHtml(msg.username)}</strong>
        <small>${escapeHtml(msg.created_at)}</small>
      </header>
      <p>${escapeHtml(msg.text)}</p>
    `;
    messagesEl.appendChild(item);
    // Автопрокрутка к последнему сообщению.
    messagesEl.scrollTop = messagesEl.scrollHeight;
  }

  function connect() {
    // Подключаемся к комнате по WS.
    ws = new WebSocket(`${protocol}://${location.host}/ws/${roomId}`);

    ws.onmessage = (event) => {
      const data = JSON.parse(event.data);
      if (data.type === "chat.message") {
        appendMessage(data);
      }
    };

    ws.onclose = () => {
      // Простейшая стратегия автопереподключения.
      reconnectTimer = setTimeout(connect, 1500);
    };
  }

  connect();

  formEl.addEventListener("htmx:afterRequest", () => {
    // После успешной отправки чистим input.
    textInput.value = "";
    textInput.focus();
  });

  window.addEventListener("beforeunload", () => {
    // Аккуратно освобождаем ресурсы при закрытии вкладки.
    if (reconnectTimer) clearTimeout(reconnectTimer);
    if (ws) ws.close();
  });
})();
```

## 5.5 `app/static/styles.css`

```css
body {
  margin: 0;
  font-family: Arial, sans-serif;
  background: #f7f7f9;
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

## 6) Миграции (минимально)

`alembic` инициализируйте стандартно, затем создайте таблицу `messages`.

Команды:
```bash
docker compose run --rm web alembic init migrations
docker compose run --rm web alembic revision -m "create messages table" --autogenerate
docker compose run --rm web alembic upgrade head
```

Проверка:
- таблица `messages` существует;
- после отправки сообщения появляется новая запись.

---

## 7) Тесты

## 7.1 `tests/test_health.py`

```python
from fastapi.testclient import TestClient
from app.main import app


def test_health():
    client = TestClient(app)
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"
```

Запуск:
```bash
docker compose run --rm web pytest -q
```

---

## 8) CI/CD (полный листинг workflow файлов)

## 8.1 `.github/workflows/ci.yml`

```yaml
name: CI

on:
  push:
    branches: ["**"]
  pull_request:

jobs:
  test:
    # Один job для простого учебного проекта.
    runs-on: ubuntu-latest
    steps:
      # 1) Забираем код репозитория.
      - uses: actions/checkout@v4

      # 2) Ставим Python версии, с которой работает проект.
      - uses: actions/setup-python@v5
        with:
          python-version: "3.13"

      # 3) Устанавливаем зависимости.
      - name: Install deps
        run: |
          python -m pip install --upgrade pip
          pip install -r requirements.txt

      # 4) Проверяем стиль/качество кода.
      - name: Lint
        run: ruff check .

      # 5) Гоняем тесты.
      - name: Test
        run: pytest -q

      # 6) Проверяем, что Docker-образ собирается без ошибок.
      - name: Docker build
        run: docker build -t socket-mq:ci .
```

## 8.2 `.github/workflows/cd.yml`

```yaml
name: CD

on:
  push:
    branches: ["main"]

jobs:
  deploy:
    runs-on: ubuntu-latest
    permissions:
      contents: read
      packages: write
    steps:
      - uses: actions/checkout@v4

      - name: Log in to GHCR
        uses: docker/login-action@v3
        with:
          registry: ghcr.io
          username: ${{ github.actor }}
          password: ${{ secrets.GITHUB_TOKEN }}

      - name: Build and push web image
        run: |
          IMAGE=ghcr.io/${{ github.repository }}/socket-mq-web:latest
          docker build -t $IMAGE .
          docker push $IMAGE

      - name: Deploy via SSH
        uses: appleboy/ssh-action@v1.0.3
        with:
          host: ${{ secrets.VPS_HOST }}
          username: ${{ secrets.VPS_USER }}
          key: ${{ secrets.VPS_SSH_KEY }}
          script: |
            cd /opt/socket-mq
            docker compose -f docker-compose.prod.yml pull
            docker compose -f docker-compose.prod.yml up -d
            docker compose -f docker-compose.prod.yml exec -T web alembic upgrade head
            curl -f https://your-domain/health
```

---

## 9) Production deploy на VPS

## 9.1 `docker-compose.prod.yml`

```yaml
services:
  web:
    image: ghcr.io/OWNER/REPO/socket-mq-web:latest
    container_name: socket_mq_web
    env_file:
      - .env
    expose:
      - "8000"
    command: uvicorn app.main:app --host 0.0.0.0 --port 8000
    depends_on:
      - postgres
      - rabbitmq

  worker:
    image: ghcr.io/OWNER/REPO/socket-mq-web:latest
    container_name: socket_mq_worker
    env_file:
      - .env
    command: python -m worker.consumer
    depends_on:
      - postgres
      - rabbitmq

  postgres:
    image: postgres:16
    container_name: socket_mq_postgres
    environment:
      POSTGRES_DB: chat
      POSTGRES_USER: chat
      POSTGRES_PASSWORD: ${POSTGRES_PASSWORD}
    volumes:
      - pg_data:/var/lib/postgresql/data

  rabbitmq:
    image: rabbitmq:3-management
    container_name: socket_mq_rabbitmq
    volumes:
      - rabbit_data:/var/lib/rabbitmq

  nginx:
    image: nginx:1.27-alpine
    container_name: socket_mq_nginx
    ports:
      - "80:80"
      - "443:443"
    volumes:
      - ./docker/nginx/default.conf:/etc/nginx/conf.d/default.conf:ro
      - ./certbot/conf:/etc/letsencrypt
      - ./certbot/www:/var/www/certbot
    depends_on:
      - web

volumes:
  pg_data:
  rabbit_data:
```

## 9.2 `docker/nginx/default.conf`

```nginx
server {
    listen 80;
    server_name your-domain;
    location /.well-known/acme-challenge/ {
        root /var/www/certbot;
    }
    location / {
        return 301 https://$host$request_uri;
    }
}

server {
    listen 443 ssl;
    server_name your-domain;

    ssl_certificate /etc/letsencrypt/live/your-domain/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/your-domain/privkey.pem;

    location / {
        proxy_pass http://web:8000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }

    location /ws/ {
        proxy_pass http://web:8000/ws/;
        proxy_http_version 1.1;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection "upgrade";
        proxy_set_header Host $host;
    }
}
```

## 9.3 Деплой команды (VPS)

```bash
cd /opt/socket-mq
docker compose -f docker-compose.prod.yml pull
docker compose -f docker-compose.prod.yml up -d
docker compose -f docker-compose.prod.yml exec -T web alembic upgrade head
curl -f https://your-domain/health
```

---

## 10) Проверка каждого шага (чек-лист преподавателя)

## Шаг A. Инфраструктура
- `docker compose ps` -> `postgres`, `rabbitmq` running.
- RabbitMQ UI доступен.

## Шаг B. Web и шаблоны
- `GET /rooms/room-1` открывает страницу.
- форма отправляется без перезагрузки.

## Шаг C. WebSocket
- 2 вкладки в одной комнате;
- сообщение видят обе вкладки.

## Шаг D. MQ
- событие `chat.message.created` появляется в broker;
- worker подтверждает обработку.

## Шаг E. DB
- запись появляется в `messages`;
- после refresh история не теряется.

## Шаг F. CI
- lint и tests зелёные.

## Шаг G. CD + VPS
- при push в `main` происходит деплой;
- `/health` доступен по HTTPS;
- WS работает по WSS.

---

## 11) Частые ошибки и как исправлять

- **Пустые сообщения проходят:** добавьте `.strip()` и проверку длины.
- **WS не работает за Nginx:** проверьте `Upgrade`/`Connection` headers.
- **Worker пишет дубликаты:** добавьте `event_id` и idempotency-проверку.
- **Сервисы стартуют, но не коннектятся:** проверьте `DATABASE_URL` и `RABBITMQ_URL`.
- **CI зеленый, но prod не обновляется:** проверьте registry login и SSH secrets.

---

## 12) Что сдавать

- репозиторий со всем кодом;
- `README` с запуском локально и на VPS;
- файлы CI/CD workflows;
- скриншоты:
  - работающий чат (2 клиента),
  - RabbitMQ/worker logs,
  - `/health` на домене,
  - успешный pipeline.

---

## 13) Доп. задания (по желанию)

- typing indicator;
- online presence;
- отдельный сервис `notification`;
- WebRTC signaling (`offer/answer/ice`);
- нагрузочный тест WS (100+ соединений).
# Лабораторная работа: realtime чат на FastAPI, HTMX, RabbitMQ, PostgreSQL

## 1. Тема и цель

В этой работе вы реализуете учебный чат с комнатами, где:
- HTTP и HTMX отвечают за UI и отправку форм;
- WebSocket отвечает за realtime-доставку;
- RabbitMQ отвечает за асинхронную обработку событий;
- PostgreSQL отвечает за хранение истории.

К концу работы у вас будет:
- работающее приложение локально в Docker;
- базовые автотесты;
- CI/CD pipeline;
- деплой на VPS с Nginx и HTTPS.

---

## 2. Что должно получиться

### Обязательный функционал
- вход в комнату;
- отправка/получение текстовых сообщений в реальном времени;
- сохранение сообщений в PostgreSQL;
- обработка через RabbitMQ (не прямой обход);
- healthcheck endpoint.

### Дополнительный функционал (bonus)
- typing indicator;
- online/offline в комнате;
- signaling для WebRTC (`offer/answer/ice`);
- пагинация истории.

---

## 3. Архитектура и поток данных

### Компоненты
1. **Frontend (HTMX + Jinja2 templates)**  
   Рисует страницу, отправляет форму сообщения, обновляет DOM.

2. **FastAPI (web)**  
   HTTP routes, WebSocket endpoint, публикация событий в RabbitMQ.

3. **RabbitMQ (broker)**  
   Обмен событиями между web и worker.

4. **Worker**  
   Читает события из очереди, валидирует, пишет в БД.

5. **PostgreSQL**  
   Таблицы `rooms`, `users`, `messages`.

6. **Nginx (prod)**  
   Reverse proxy + TLS + проксирование WebSocket.

### Поток текстового сообщения
1. Пользователь отправляет форму на `POST /rooms/{room_id}/messages`.
2. FastAPI публикует событие `chat.message.created` в RabbitMQ.
3. Worker читает событие и сохраняет сообщение в PostgreSQL.
4. Web-сервис отправляет событие по WebSocket подписчикам комнаты.
5. Браузер добавляет сообщение в ленту.

---

## 4. Рекомендуемая структура проекта

```txt
socket-mq/
  app/
    main.py
    api/
      http_chat.py
      ws_chat.py
    services/
      mq.py
      broadcaster.py
    db/
      models.py
      session.py
    templates/
      base.html
      chat.html
      partials/
        message_item.html
    static/
      chat.js
      chat.css
  worker/
    consumer.py
  tests/
    test_health.py
    test_message_flow.py
  migrations/
  docker-compose.yml
  docker-compose.prod.yml
  Dockerfile
  requirements.txt
  README.md
```

---

## 5. Подготовка окружения (шаг 0)

### 5.1 Требования
- Docker Desktop (или Docker Engine + Compose plugin);
- Git;
- Python 3.13+ (если запускаете часть шагов без Docker).

### 5.2 Переменные окружения (`.env`)
Минимальный набор:
- `APP_ENV=dev`
- `DATABASE_URL=postgresql+asyncpg://chat:chat@postgres:5432/chat`
- `RABBITMQ_URL=amqp://guest:guest@rabbitmq:5672/`
- `SECRET_KEY=change-me`

### 5.3 Локальный запуск инфраструктуры
Команда:
```bash
docker compose up -d postgres rabbitmq
```

Проверка:
- `docker compose ps` показывает `postgres` и `rabbitmq` в `running`;
- RabbitMQ UI доступен на `http://localhost:15672` (если включен management).

Критерий готовности шага:
- брокер и БД подняты и доступны.

Типичные ошибки:
- порт занят;
- неверный URL подключения;
- забыли создать `.env`.

---

## 6. Этап 1: базовый FastAPI + HTMX UI

### Что реализовать
- `GET /` - страница выбора комнаты;
- `GET /rooms/{room_id}` - страница чата;
- шаблоны `base.html`, `chat.html`, `partials/message_item.html`;
- форма отправки сообщения через HTMX.

### Как запускать
```bash
docker compose up -d --build web
```

Или локально:
```bash
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

### Как проверить
1. Откройте `http://localhost:8000`.
2. Перейдите в комнату.
3. Отправьте сообщение через форму.
4. Убедитесь, что HTMX обновляет блок сообщений (даже если пока без realtime).

Критерий готовности шага:
- UI работает, форма отправляется, сервер отвечает без ошибок.

---

## 7. Этап 2: WebSocket realtime

### Что реализовать
- endpoint: `/ws/{room_id}`;
- менеджер подключений комнат (join/leave);
- broadcast события `chat.message` в текущую комнату.

### Как запускать
```bash
docker compose up -d --build web
```

### Как проверить
1. Откройте две вкладки браузера в одной комнате.
2. Отправьте сообщение в первой вкладке.
3. Сообщение должно появиться во второй почти мгновенно.
4. Отключите вкладку и убедитесь, что сервер корректно снимает подписку.

Критерий готовности шага:
- сообщения доходят всем активным участникам комнаты < 1 сек.

Типичные ошибки:
- забыли `await websocket.accept()`;
- широковещание по всем комнатам вместо текущей;
- падение на отключенном сокете.

---

## 8. Этап 3: RabbitMQ и worker

### Что реализовать
- в web-сервисе: публикация события в exchange;
- в worker: consume очереди, parse payload, ack/nack;
- retry или DLQ (минимум базовый retry).

Пример routing key:
- `chat.message.created`

Пример payload:
```json
{
  "event_id": "uuid",
  "room_id": "room-1",
  "user": "alice",
  "text": "Hello",
  "created_at": "2026-03-26T10:00:00Z"
}
```

### Как запускать
```bash
docker compose up -d --build web worker rabbitmq
```

### Как проверить
1. Отправьте сообщение из UI.
2. Проверьте, что событие прошло через RabbitMQ (через логи/management UI).
3. Убедитесь, что worker принял событие и подтвердил (`ack`).

Критерий готовности шага:
- путь сообщения проходит через MQ, worker стабильно обрабатывает события.

Типичные ошибки:
- durable очередь без persistent сообщений (или наоборот);
- consumer не делает ack;
- неправильный exchange/queue binding.

---

## 9. Этап 4: PostgreSQL и миграции

### Что реализовать
- модели:
  - `Room(id, name, created_at)`,
  - `User(id, username, created_at)`,
  - `Message(id, room_id, user_id/username, text, created_at)`;
- миграции Alembic;
- чтение истории комнаты.

### Как запускать
```bash
docker compose run --rm web alembic upgrade head
docker compose up -d web worker postgres
```

### Как проверить
1. Отправьте 3-5 сообщений.
2. Перезагрузите страницу комнаты.
3. Убедитесь, что история подтянулась из БД.
4. Проверьте таблицу `messages` через psql/GUI.

Критерий готовности шага:
- сообщения сохраняются и читаются из PostgreSQL.

---

## 10. Этап 5: стабильность и UX

### Что реализовать
- reconnection WebSocket на клиенте;
- таймауты и обработка ошибок при недоступности MQ/DB;
- логирование (уровни INFO/ERROR).

### Как проверить
1. Остановите `rabbitmq`: `docker compose stop rabbitmq`.
2. Отправьте сообщение и проверьте корректную ошибку пользователю.
3. Запустите `rabbitmq` обратно и проверьте восстановление.
4. Закройте вкладку/интернет и убедитесь, что reconnect отрабатывает.

Критерий готовности шага:
- система восстанавливается после кратковременных сбоев.

---

## 11. Этап 6 (bonus): signaling для видео

### Что реализовать
- WS события:
  - `webrtc.offer`
  - `webrtc.answer`
  - `webrtc.ice`
- форвардинг сигналинга только участникам одной комнаты.

### Как проверить
1. Откройте две вкладки.
2. Инициируйте звонок.
3. Проверьте в DevTools, что события `offer/answer/ice` проходят.
4. Убедитесь, что медиа соединение устанавливается.

Критерий готовности шага:
- peer connection поднимается между двумя клиентами.

---

## 12. Контракты API и событий

### HTTP
- `GET /`
- `GET /rooms/{room_id}`
- `POST /rooms/{room_id}/messages`
- `GET /rooms/{room_id}/history`
- `GET /health`

### WebSocket
- `ws://host/ws/{room_id}`

### Типы событий
- `chat.message`
- `user.join`
- `user.leave`
- `typing.start`
- `typing.stop`
- `webrtc.offer` (bonus)
- `webrtc.answer` (bonus)
- `webrtc.ice` (bonus)

### MQ routing keys
- `chat.message.created`
- `chat.user.joined`
- `chat.user.left`
- `chat.typing.started`
- `chat.typing.stopped`

---

## 13. Тестирование (обязательно)

### Ручные проверки
- сообщение видно всем в одной комнате;
- изоляция комнат соблюдается;
- история переживает перезапуск web;
- reconnect клиента работает;
- при падении MQ есть корректная ошибка.

### Автотесты (минимум)
- unit:
  - валидация message payload;
  - преобразование event -> DTO;
- integration:
  - `/health` возвращает 200;
  - создание сообщения через HTTP;
  - сохранение в БД;
  - событие в MQ (на тестовом broker).

Запуск:
```bash
docker compose run --rm web pytest -q
```

---

## 14. CI/CD

### 14.1 CI (push и PR)
Шаги:
1. checkout кода;
2. установка зависимостей;
3. `ruff check .`;
4. `pytest -q`;
5. `docker build`.

Критерий:
- pipeline зеленый, ошибки линтера/тестов блокируют merge.

### 14.2 CD (main)
Шаги:
1. собрать образы `web` и `worker`;
2. запушить образы в registry (например, GHCR);
3. по SSH подключиться к VPS;
4. на VPS выполнить:
   - `docker compose -f docker-compose.prod.yml pull`
   - `docker compose -f docker-compose.prod.yml up -d`
   - `docker compose -f docker-compose.prod.yml exec -T web alembic upgrade head`
5. проверить `GET /health`.

Критерий:
- после merge в `main` новая версия доступна на сервере автоматически.

### 14.3 Секреты CI
- `VPS_HOST`
- `VPS_USER`
- `VPS_SSH_KEY`
- `DATABASE_URL`
- `RABBITMQ_URL`
- `SECRET_KEY`
- `REGISTRY_TOKEN`

---

## 15. Деплой на VPS (пошагово)

### 15.1 Подготовка сервера
1. Создать Ubuntu 22.04 VPS.
2. Обновить систему:
   ```bash
   sudo apt update && sudo apt upgrade -y
   ```
3. Установить Docker и Compose plugin.
4. Настроить firewall:
   ```bash
   sudo ufw allow 22
   sudo ufw allow 80
   sudo ufw allow 443
   sudo ufw enable
   ```

Проверка:
- `docker --version` и `docker compose version` работают;
- порты открыты только 22/80/443.

### 15.2 Развертывание приложения
1. Создать каталог деплоя, например `/opt/socket-mq`.
2. Положить `docker-compose.prod.yml` и `.env`.
3. Запустить:
   ```bash
   docker compose -f docker-compose.prod.yml up -d
   ```
4. Применить миграции:
   ```bash
   docker compose -f docker-compose.prod.yml exec -T web alembic upgrade head
   ```

Проверка:
- `docker compose -f docker-compose.prod.yml ps` показывает все сервисы `running`.

### 15.3 Nginx и HTTPS
1. Настроить `server_name`.
2. Проксировать HTTP на `web:8000`.
3. Добавить WS proxy headers (`Upgrade`, `Connection`).
4. Выпустить TLS сертификат через Let's Encrypt.

Проверка:
- `https://your-domain/health` возвращает 200;
- чат работает по WSS.

---

## 16. Чек-лист сдачи

Студент должен продемонстрировать:
1. Локальный запуск через Docker Compose.
2. Обмен сообщениями между двумя вкладками.
3. Сообщения сохраняются в БД.
4. RabbitMQ участвует в обработке (показать логи worker).
5. Зеленый CI pipeline.
6. Деплой на VPS и рабочий `/health`.
7. (bonus) signaling для WebRTC.

---

## 17. Критерии оценки

### Архитектура (20%)
- корректное разделение web/worker/mq/db;
- отсутствие "обхода" MQ в основной цепочке.

### Функционал (30%)
- комнаты, realtime, история.

### Качество кода (20%)
- структура проекта, обработка ошибок, логирование.

### CI/CD и деплой (20%)
- рабочие workflows и автодеплой.

### Документация (10%)
- понятный README и инструкция запуска.

---

## 18. Что сдавать

- ссылка на репозиторий;
- `README.md`:
  - локальный запуск;
  - переменные окружения;
  - запуск тестов;
  - деплой на VPS;
- `docker-compose.yml` и `docker-compose.prod.yml`;
- workflow файлы CI/CD;
- краткий отчет (1-2 страницы) со скриншотами:
  - архитектура;
  - pipeline;
  - работающий чат;
  - healthcheck на VPS.

---

## 19. Примечания преподавателю

- Можно выдать шаблон проекта с готовыми папками и пустыми модулями.
- Для базовой версии достаточно username без полноценной авторизации.
- Если группа отстает, этап 6 оставить как бонусный.
- На защите просить показать именно путь сообщения через MQ.
