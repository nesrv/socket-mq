import asyncio
import urllib.parse
import urllib.request

import websockets


async def main() -> None:
    url = "ws://127.0.0.1:8000/ws/room-1"
    ws1 = await websockets.connect(url)
    ws2 = await websockets.connect(url)

    data = urllib.parse.urlencode({"username": "bot", "text": "ws-check"}).encode()
    req = urllib.request.Request(
        "http://127.0.0.1:8000/rooms/room-1/messages", data=data, method="POST"
    )
    req.add_header("Content-Type", "application/x-www-form-urlencoded")
    urllib.request.urlopen(req).read()

    m1 = await asyncio.wait_for(ws1.recv(), timeout=5)
    m2 = await asyncio.wait_for(ws2.recv(), timeout=5)
    print(m1)
    print(m2)
    await ws1.close()
    await ws2.close()


if __name__ == "__main__":
    asyncio.run(main())
