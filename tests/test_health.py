import asyncio

import pytest
from fastapi.testclient import TestClient

import app.main as main


def test_health(monkeypatch: pytest.MonkeyPatch) -> None:
    async def noop() -> None:
        return None

    async def fake_consume() -> None:
        await asyncio.Event().wait()

    monkeypatch.setattr(main, "init_models", noop)
    monkeypatch.setattr(main.mq, "connect", noop)
    monkeypatch.setattr(main, "consume_persisted_events", fake_consume)

    with TestClient(main.app) as client:
        response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
