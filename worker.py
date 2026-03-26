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
