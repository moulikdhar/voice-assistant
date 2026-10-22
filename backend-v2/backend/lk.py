"""
The local LiveKit server is only the browser's phone line now. This replaces the `livekit-api` package
with the two things we still need from it: access tokens (HS256 JWT) and "delete room".
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
import time

import httpx

from . import config

log = logging.getLogger("lk")


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def _jwt(claims: dict) -> str:
    head = _b64(json.dumps({"alg": "HS256", "typ": "JWT"}).encode())
    body = _b64(json.dumps(claims, separators=(",", ":")).encode())
    sig = hmac.new(config.LIVEKIT_API_SECRET.encode(), f"{head}.{body}".encode(), hashlib.sha256).digest()
    return f"{head}.{body}.{_b64(sig)}"


def token(identity: str, name: str, room: str, *, agent: bool = False, hidden: bool = False,
          can_publish: bool = True, ttl_s: int = 6 * 3600) -> str:
    """Join token. agent=True makes the browser see this participant as ParticipantKind.AGENT,
    which is what the phone waits for to switch from "ringing" to "connected"."""
    now = int(time.time())
    claims = {
        "iss": config.LIVEKIT_API_KEY, "sub": identity, "name": name, "nbf": now - 10, "exp": now + ttl_s,
        "video": {"roomJoin": True, "room": room, "canPublish": can_publish, "canSubscribe": True,
                  "canPublishData": True, "canUpdateOwnMetadata": True, "hidden": hidden, "agent": agent},
    }
    if agent:
        claims["kind"] = "agent"
    return _jwt(claims)


async def delete_room(room: str) -> None:
    """RoomService.DeleteRoom (Twirp): every participant is disconnected, the browser shows "Call ended"."""
    now = int(time.time())
    admin = _jwt({"iss": config.LIVEKIT_API_KEY, "sub": "backend", "nbf": now - 10, "exp": now + 60,
                  "video": {"roomCreate": True, "roomAdmin": True, "room": room}})
    url = config.LIVEKIT_URL.replace("ws://", "http://").replace("wss://", "https://").rstrip("/")
    try:
        # plain http to localhost: skip loading TLS certificates (slow on Windows)
        async with httpx.AsyncClient(timeout=5, trust_env=False) as c:
            r = await c.post(f"{url}/twirp/livekit.RoomService/DeleteRoom", json={"room": room},
                             headers={"Authorization": f"Bearer {admin}"})
            if r.status_code >= 400:
                log.info("delete_room(%s): %s %s", room, r.status_code, r.text[:200])
    except Exception as e:
        log.info("delete_room(%s): %s", room, e)
