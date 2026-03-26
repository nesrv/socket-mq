# Лабораторная работа: простой чат на FastAPI + HTMX ws extension

Сквозная нумерация разделов верхнего уровня: **`## 1` … `## 7`**. В **части 1** — разделы **1** (окружение и файлы простого чата, вариант 0) и **2** (деплой на VPS). В **части 2** — разделы **3** … **7** (серверная часть с PostgreSQL и RabbitMQ, шаблоны, проверка, тесты, сдача). Подразделы вида **`3.1`**, **`5.2`** относятся к родительскому разделу **3** или **5**.

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
APP_PORT=8000
```

`APP_PORT` совпадает с портом, на котором **внутри контейнера** запускается Uvicorn; снаружи в `docker-compose.yml` проброс `8100:8000` даёт доступ с хоста по `http://localhost:8100`.

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
    command: uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
    env_file:
      - .env
    volumes:
      - ./:/code
    ports:
      - "8100:8000"
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

## 2. Быстрый деплой на VPS через git + ssh

Данные сервера:
- SSH: `alekseeva@81.90.182.174`
- Домен: `alekseeva.h1n.ru`

### 2.1 Первый вход
```bash
ssh alekseeva@81.90.182.174
```

### 2.2 Подготовка сервера (один раз)
```bash
sudo systemctl enable --now docker
sudo usermod -aG docker $USER
```
Пояснение:
- `sudo systemctl enable --now docker` - запускает Docker сейчас и включает автозапуск после перезагрузки сервера.
- `sudo usermod -aG docker $USER` - добавляет текущего пользователя в группу `docker`, чтобы запускать команды без `sudo`.

После этого переподключитесь по SSH.

### 2.3 Клонирование проекта
```bash
mkdir -p /opt/socket-mq
cd /opt/socket-mq
git clone <URL_ВАШЕГО_РЕПОЗИТОРИЯ> app
cd app
```

### 2.4 Настройка `.env`
Создайте файл `.env` рядом с `docker-compose.yml`:

```env
APP_HOST=0.0.0.0
APP_PORT=8000
```

Остальные переменные для варианта 0 не требуются. Порт **8000** — внутри контейнера; обращение с хоста — `http://localhost:8100` при пробросе `8100:8000` в `docker-compose.yml`.

### 2.5 Запуск приложения
```bash
docker compose up -d --build
docker compose ps
```

### 2.6 Nginx конфиг для домена
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

Если получили предупреждение `conflicting server name ... ignored`, значит у домена есть дублирующийся конфиг (частая ситуация после ручных правок/старых сайтов).
Проверьте активные конфиги и оставьте только один для `alekseeva.h1n.ru`:

```bash
ls -l /etc/nginx/sites-enabled
sudo grep -R "server_name alekseeva.h1n.ru" /etc/nginx/sites-available /etc/nginx/sites-enabled
sudo rm /etc/nginx/sites-enabled/alekseeva
sudo nginx -t
sudo systemctl reload nginx
```

### 2.7 HTTPS сертификат
```bash
sudo certbot --nginx -d alekseeva.h1n.ru
```

### 2.8 Проверка
- `http://alekseeva.h1n.ru/health`
- `https://alekseeva.h1n.ru/health`
- локально на сервере backend отвечает: `curl -i http://127.0.0.1:8100/health`

### 2.9 Обновление деплоя (каждый раз)
```bash
ssh alekseeva@81.90.182.174
cd /opt/socket-mq/app
git pull
docker compose up -d --build
```


### 2.10 Принудительное обновление (сотрет локальные правки tracked-файлов)
Сначала определите актуальную ветку на сервере:
```bash
git branch -r
```

Затем выполните принудительное выравнивание (пример для `origin/master`):
```bash
git fetch origin
git reset --hard origin/master   # замени на нужную ветку
git clean -fd
```

### 2.11 Имитация нагрузки через кнопки в чате
Чтобы наглядно показать поведение простого варианта чата под нагрузкой (без MQ и БД), можно использовать специальные кнопки на веб-интерфейсе:

- кнопка **«Отправить 100»** — быстро отправляет 100 сообщений в текущую комнату;
- кнопка **«Отправить 1000»** — быстро отправляет 1000 сообщений.

Эти кнопки добавляются в шаблон `app/templates/room.html` рядом с формой отправки сообщений и используют ту же форму с `ws-send`, поэтому все сообщения уходят через WebSocket, как обычные.

**Фрагмент HTML, который нужно добавить в `app/templates/room.html` под формой:**

```html
  <div class="burst-controls">
    <button type="button" id="send-100" onclick="sendBurst(100)">Отправить 100</button>
    <button type="button" id="send-1000" onclick="sendBurst(1000)">Отправить 1000</button>
  </div>

  <script>
    // Отправка серии сообщений через текущую форму (ws-send).
    // Это помогает быстро создать нагрузку и продемонстрировать эффект при отсутствии MQ.
    let burstBusy = false;

    function setFormDefaultsIfEmpty() {
      const form = document.getElementById('message-form');
      const usernameInput = form.querySelector('input[name="username"]');
      const textInput = form.querySelector('input[name="text"]');

      if (!usernameInput.value.trim()) usernameInput.value = 'load-test';
      if (!textInput.value.trim()) textInput.value = 'Нагрузочное сообщение';
    }

    async function sendBurst(count) {
      if (burstBusy) return;
      burstBusy = true;

      const form = document.getElementById('message-form');
      const usernameInput = form.querySelector('input[name="username"]');
      const textInput = form.querySelector('input[name="text"]');

      setFormDefaultsIfEmpty();
      const baseText = textInput.value.trim();
      usernameInput.focus();

      const btn100 = document.getElementById('send-100');
      const btn1000 = document.getElementById('send-1000');
      if (btn100) btn100.disabled = true;
      if (btn1000) btn1000.disabled = true;

      // Чтобы не зависнуть в одном длинном цикле, отправляем пачками.
      let i = 1;
      const batchSize = 20;

      while (i <= count) {
        const end = Math.min(count, i + batchSize - 1);
        for (; i <= end; i++) {
          textInput.value = `${baseText} #${i}`;
          // requestSubmit вызовет submit-ивент, а htmx-ext-ws перешлет данные в WS.
          form.requestSubmit();
        }
        await new Promise((r) => setTimeout(r, 0));
      }

      burstBusy = false;
      if (btn100) btn100.disabled = false;
      if (btn1000) btn1000.disabled = false;
    }
  </script>
```

**И фрагмент CSS, который нужно добавить в `app/static/styles.css`:**

```css
.burst-controls {
  display: flex;
  gap: 8px;
  margin-bottom: 12px;
}
```

Сценарий для студентов:

1. Открыть одну или несколько вкладок с комнатой `room-1`:
   - локально: `http://localhost:8100/rooms/room-1`;
   - на VPS: `http://alekseeva.h1n.ru/rooms/room-1`.
2. Ввести ник (если не введён, код подставит `load-test` автоматически).
3. Нажать «Отправить 100» или «Отправить 1000» и наблюдать:
   - как быстро появляются сообщения;
   - как ведёт себя браузер и сервер при серии из сотен сообщений;
   - как это отличается при одном экземпляре сервера и при нескольких экземплярах (в варианте с брокером сообщений и базой данных, часть 2).


## Часть 2: Чат с базой данных и брокером сообщений

В этом варианте в браузере по-прежнему используются **HTMX** и расширение **WebSocket** (`htmx-ext-ws`): страница комнаты подключается к серверу по протоколу WebSocket и отправляет сообщения через форму с атрибутом `ws-send`. На стороне сервера цепочка обработки отличается от варианта 0: каждое новое сообщение сначала попадает в **брокер сообщений RabbitMQ**, затем отдельный процесс **worker** записывает его в **PostgreSQL**, после чего факт сохранения снова оформляется как событие в RabbitMQ, и процесс **web** рассылает готовый фрагмент HTML всем клиентам, открывшим ту же комнату.

Ниже приведены полные листинги файлов, которые нужно иметь в проекте этого варианта (каталог `app/`, файл `worker.py` в корне рядом с `docker-compose.yml`, файлы шаблонов и стилей). Вы можете развивать проект из **части 1**, добавляя и заменяя файлы, либо собрать новый каталог и перенести туда фрагменты из методички.

**Поток данных по шагам:**

1. Пользователь отправляет сообщение из формы: расширение WebSocket для HTMX передаёт на сервер JSON с полями имени пользователя и текста по маршруту WebSocket `/ws/{room_id}`. Процесс контейнера **web** (приложение Uvicorn + FastAPI) не пишет сразу в базу и не рассылает сообщение всем: он публикует в RabbitMQ событие с ключом маршрутизации `chat.message.created` (сообщение «создано, ожидает сохранения»).
2. Процесс **worker** подписан на очередь входящих сообщений, читает событие, выполняет вставку строки в таблицу `messages` в PostgreSQL, затем публикует в RabbitMQ второе событие с ключом `chat.message.persisted` (сообщение «успешно сохранено», в теле — идентификатор записи и время).
3. В процессе **web** в фоне запущена асинхронная задача `consume_persisted_events`: она читает очередь событий «persisted», собирает HTML-фрагмент с разметкой сообщения и вызывает `manager.broadcast`, чтобы отправить этот фрагмент по WebSocket всем подключениям данной комнаты.

**Подготовка:** в корне проекта должны лежать `docker-compose.yml` с сервисами `web`, `worker`, `postgres`, `rabbitmq` и файл переменных окружения `.env`, заполненный по образцу из подраздела **3.0**.

## 3. Backend (приложение и фоновые процессы)

### 3.0 Файл переменных окружения `.env` (образец для копирования в свой `.env`)

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

В `docker-compose.yml` для сервиса **web** задан проброс **`8100:8000`**: с хоста (браузер, `curl`) доступен порт **8100**, внутри контейнера Uvicorn слушает порт **8000**. Проверки ниже используют адрес `http://127.0.0.1:8100` — это тот же принцип, что и в **части 1**.

### 3.1 `app/db.py`

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

### 3.2 `app/ws.py`

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

### 3.3 `app/mq.py`

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

### 3.4 `app/main.py`

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

### 3.5 `worker.py`

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

## 4. Шаблоны и стили

Ниже — полные тексты файлов для варианта с PostgreSQL и RabbitMQ. Структура страницы совпадает с **частью 1**: подключаются библиотека HTMX, расширение WebSocket для HTMX, таблица стилей; на странице комнаты задаётся `hx-ext="ws"`, адрес WebSocket `ws-connect="/ws/{{ room_id }}"`, форма с атрибутом `ws-send` отправляет поля как JSON по уже открытому соединению. Заголовок документа в базовом шаблоне — **Socket MQ Chat** (в простом чате из части 1 часто используют **Socket MQ Chat V0**). Кнопки массовой отправки из раздела **2.11** сюда не включены: при необходимости перенесите HTML-блок с кнопками и встроенный скрипт из шаблона комнаты части 1 и вставьте в `room.html` сразу после полей формы, перед закрывающим тегом секции.

### 4.1 `app/templates/base.html`

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
  <body>
    <main class="container">{% block content %}{% endblock %}</main>
  </body>
</html>
```

### 4.2 `app/templates/room.html`

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

### 4.3 `app/static/styles.css`

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

## 5. Проверка по шагам

Все команды выполняйте в **корневом каталоге проекта**, где лежит `docker-compose.yml` этого варианта (тот же каталог, что и файл `.env`).

### 5.1 Инфраструктура
Команда:
```bash
docker compose up -d postgres rabbitmq
```
Проверка:
- оба контейнера `running`;
- RabbitMQ UI открывается (порт `15672` в `docker-compose.yml`).

### 5.2 Сервис web
Команда:
```bash
docker compose up -d --build web
```
Проверка:
- `http://127.0.0.1:8100/health` возвращает `{"status":"ok"}`;
- `http://127.0.0.1:8100/rooms/room-1` открывается в браузере.

### 5.3 Worker
Команда:
```bash
docker compose up -d --build worker
```
Проверка:
- worker живой, не падает при старте;
- в логах нет ошибки `relation "messages" does not exist`.

### 5.4 Realtime (две вкладки)
Проверка:
1. Откройте две вкладки `http://127.0.0.1:8100/rooms/room-1`.
2. Отправьте сообщение из одной вкладки.
3. Сообщение должно появиться в обеих вкладках.
4. В DevTools → Network → WS должен быть запрос к `/ws/room-1`.
5. Если сообщения не появляются, проверьте:
   - `docker compose logs web --tail 100`;
   - `docker compose logs worker --tail 100`;
   - `docker compose exec -T rabbitmq rabbitmqctl list_queues name messages consumers`.

### 5.5 База данных
Проверка:
- в таблице `messages` появляются записи после отправки (через `psql` или клиент к БД);
- в приведённой ниже реализации приложение при открытии страницы комнаты не подгружает историю из PostgreSQL в шаблон: пользователь видит только новые сообщения, которые приходят по WebSocket после того, как worker сохранил запись и сработала рассылка `persisted`.

### 5.6 Деплой на VPS: что меняется по сравнению с разделом **2** (простой чат)

Раздел **«2. Быстрый деплой на VPS через git + ssh»** описывает выкладку **варианта 0**: один контейнер **web**, в `.env` только хост и порт приложения, в Nginx обратный прокси на **порт 8100**. Для **части 2** (PostgreSQL + RabbitMQ + worker) на сервере нужно учесть следующее.

**Файл `.env` на сервере**  
Вместо двух строк из раздела **2.4** используйте полный набор переменных, как в подразделе **3.0** этой методички: строка подключения к **PostgreSQL**, URL **RabbitMQ**, имена **очереди и обменника**, ключи **маршрутизации**. Пароли к базе и к RabbitMQ должны совпадать с теми, что заданы в `docker-compose.yml` для сервисов `postgres` и `rabbitmq` (или задайте свои значения согласованно в `.env` и в секции `environment` контейнеров).

**Команда запуска**  
После `git pull` выполняйте `docker compose up -d --build` в каталоге с **текущим** `docker-compose.yml` части 2: поднимутся не только **web**, но и **worker**, **postgres**, **rabbitmq**. Проверка списка: `docker compose ps` — должны быть запущены все нужные сервисы.

**Порт приложения и Nginx**  
На хосте VPS Nginx по разделу **2.6** проксирует на **`127.0.0.1:8100`** — это внешний порт, который Docker перенаправляет на **8000** внутри контейнера **web**. Дополнительно менять `proxy_pass` при деплое части 2 не нужно, если сохранён проброс `8100:8000`. После правок: `sudo nginx -t` и `sudo systemctl reload nginx`.

**Ресурсы и безопасность**  
На VPS одновременно работают четыре сервиса вместо одного; может понадобиться больше оперативной памяти. Порты **5432** (PostgreSQL) и **5672** / **15672** (RabbitMQ) в учебном `docker-compose.yml` проброшены на хост — на боевом сервере имеет смысл не открывать их в файрволе для интернета и оставить доступ только из Docker-сети; веб-пользователям по-прежнему достаточно **443** / **80** на Nginx.

**Обновление кода**  
После каждого `git pull` выполняйте `docker compose up -d --build`, чтобы пересобрать образы и перезапустить **web** и **worker**. Если менялись только переменные окружения, иногда достаточно `docker compose up -d` без `--build` — ориентируйтесь на вывод `docker compose`.

**Проверка после выкладки**  
Адреса вида `https://ваш-домен/health` и страница комнаты должны отвечать так же, как при локальной проверке в **5.2**–**5.4**, но с вашим доменом вместо `127.0.0.1`.

---

## 6. Проверка health и опциональный тест

Быстрая проверка без pytest:
```bash
curl -s http://127.0.0.1:8100/health
```

Минимальный автотест с использованием **pytest**: создайте в проекте каталог `tests` и файл `tests/test_health.py` со следующим содержимым:

```python
from fastapi.testclient import TestClient
from app.main import app


def test_health():
    client = TestClient(app)
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
```

Запуск теста внутри контейнера **web** из корня проекта:
```bash
docker compose run --rm web pytest -q
```

Дополнительно можно добавить сценарий ручной проверки (два клиента WebSocket и отправка через HTTP), поместив скрипт, например, в `tests/ws_check.py`; для такого скрипта на машине разработчика понадобятся запущенные контейнеры и установленный пакет **websockets** в том окружении, из которого вы запускаете Python.

---

## 7. Что сдавать

- ссылка на репозиторий Git с полным кодом варианта с PostgreSQL и RabbitMQ;
- скриншот: чат открыт в двух вкладках браузера, видно появление одного и того же сообщения;
- скриншот или текст логов контейнера **worker** либо снимок очередей в интерфейсе управления RabbitMQ;
- файл **README** с пошаговым запуском через Docker Compose (какие сервисы поднимаются и в каком порядке);
- краткое текстовое описание цепочки без стрелок-аббревиатур: клиент отправляет сообщение по WebSocket; приложение **web** публикует событие в **RabbitMQ**; процесс **worker** записывает сообщение в **PostgreSQL** и снова публикует событие в **RabbitMQ**; приложение **web** получает подтверждение и рассылает HTML по WebSocket подключениям комнаты.

---

