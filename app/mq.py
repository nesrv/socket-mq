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
        # RabbitMQ при старте compose может подняться позже web — несколько попыток
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
        # created: web → worker; persisted: worker → web (рассылка в сокеты)
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
