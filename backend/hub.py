
from __future__ import annotations

import json

from fastapi import WebSocket

clients: set[WebSocket] = set()


async def broadcast(event: dict) -> None:
    data = json.dumps(event, ensure_ascii=False, default=str)
    dead = []
    for ws in list(clients):
        try:
            await ws.send_text(data)
        except Exception:
            dead.append(ws)
    for ws in dead:
        clients.discard(ws)
