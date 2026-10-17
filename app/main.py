"""Halloween Office Trivia: FastAPI server for the realtime party game.

Framework stage: phones join a lobby over a WebSocket, and the host screen shows who
is connected, each phone's round-trip latency, and a "test buzz" that proves realtime
delivery on the venue's network. Game rounds plug into this skeleton later.
"""
from __future__ import annotations

import asyncio
import hmac
import io
import json
import os
import secrets
import socket
import time
from contextlib import asynccontextmanager
from pathlib import Path

import segno
from fastapi import FastAPI, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, PlainTextResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles

from .game import JoinError, Room
from .ratelimit import TokenBucket

STATIC_DIR = Path(__file__).parent / "static"
MAX_MESSAGE_CHARS = 2_000
JOIN_TIMEOUT_S = 10
STATE_PUSH_MIN_GAP_S = 1.0

NO_CACHE = {"Cache-Control": "no-store"}
SECURITY_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "no-referrer",
    # No third-party requests anywhere, so the whole app works on locked-down networks.
    "Content-Security-Policy": (
        "default-src 'self'; connect-src 'self' ws: wss:; img-src 'self' data:; "
        "object-src 'none'; base-uri 'none'; form-action 'self'; frame-ancestors 'none'"
    ),
}


def lan_ip() -> str:
    """Best guess at this machine's LAN address (phones can't reach 'localhost')."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        sock.connect(("10.255.255.255", 1))  # UDP connect sends no packets
        return sock.getsockname()[0]
    except OSError:
        return "127.0.0.1"
    finally:
        sock.close()


def parse(raw: str | None) -> dict:
    """Decode an incoming WebSocket message; anything odd becomes an empty dict."""
    if not raw or len(raw) > MAX_MESSAGE_CHARS:
        return {}
    try:
        data = json.loads(raw)
    except (ValueError, RecursionError):
        return {}
    return data if isinstance(data, dict) else {}


async def recv(ws: WebSocket) -> str | None:
    """Next text frame (None for binary frames); raises WebSocketDisconnect on close."""
    message = await ws.receive()
    if message["type"] == "websocket.disconnect":
        raise WebSocketDisconnect(message.get("code", 1000))
    return message.get("text")


async def send(ws: WebSocket, payload: dict) -> bool:
    try:
        await ws.send_text(json.dumps(payload))
        return True
    except Exception:
        return False


async def close_quietly(ws: WebSocket, code: int) -> None:
    try:
        await ws.close(code=code)
    except Exception:
        pass


def create_app(host_key: str | None = None, public_url: str | None = None) -> FastAPI:
    key = host_key or os.environ.get("HOST_KEY") or secrets.token_urlsafe(8)
    base_url = (public_url if public_url is not None else os.environ.get("PUBLIC_URL", "")).rstrip("/")

    room = Room()
    hosts: set[WebSocket] = set()
    sockets: dict[str, WebSocket] = {}  # player id -> live socket
    last_push = 0.0

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        origin = base_url or f"http://localhost:{os.environ.get('PORT', '8000')}"
        print(f"\n  Host screen (keep this link private): {origin}/host?key={key}\n", flush=True)
        yield

    app = FastAPI(
        title="Halloween Office Trivia",
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
        lifespan=lifespan,
    )
    app.state.host_key = key
    app.state.room = room

    @app.middleware("http")
    async def security_headers(request: Request, call_next):
        response = await call_next(request)
        for name, value in SECURITY_HEADERS.items():
            response.headers.setdefault(name, value)
        return response

    def key_ok(candidate: str | None) -> bool:
        if not candidate:
            return False
        return hmac.compare_digest(candidate.encode("utf-8", "replace"), key.encode("utf-8"))

    def join_url(request: Request) -> str:
        if base_url:
            return f"{base_url}/play"
        url = request.url
        host = url.hostname or "localhost"
        if host in {"localhost", "127.0.0.1", "::1"}:
            host = lan_ip()
        default_port = {"http": 80, "https": 443}.get(url.scheme)
        netloc = host if url.port in (None, default_port) else f"{host}:{url.port}"
        return f"{url.scheme}://{netloc}/play"

    async def push_state(force: bool = True) -> None:
        nonlocal last_push
        now = time.monotonic()
        if not force and now - last_push < STATE_PUSH_MIN_GAP_S:
            return
        last_push = now
        payload = {"type": "state", **room.snapshot()}
        for ws in list(hosts):
            if not await send(ws, payload):
                hosts.discard(ws)

    # -- pages -----------------------------------------------------------
    @app.get("/", include_in_schema=False)
    async def root():
        return RedirectResponse("/play")

    @app.get("/play", include_in_schema=False)
    async def play_page():
        return FileResponse(STATIC_DIR / "play.html", headers=NO_CACHE)

    @app.get("/host", include_in_schema=False)
    async def host_page(key: str | None = None):
        if not key_ok(key):
            return PlainTextResponse(
                "Host key required. Use the link printed in the server log.", status_code=403
            )
        return FileResponse(STATIC_DIR / "host.html", headers=NO_CACHE)

    @app.get("/api/join-url")
    async def api_join_url(request: Request):
        return {"url": join_url(request)}

    @app.get("/qr.svg")
    async def qr(request: Request):
        buf = io.BytesIO()
        segno.make(join_url(request), error="m").save(
            buf, kind="svg", scale=10, border=2, dark="#1a0f2e", light="#ffffff", xmldecl=False
        )
        return Response(buf.getvalue(), media_type="image/svg+xml", headers=NO_CACHE)

    @app.get("/health")
    async def health():
        return {"ok": True}

    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

    # -- phones ----------------------------------------------------------
    @app.websocket("/ws/play")
    async def ws_play(ws: WebSocket) -> None:
        await ws.accept()
        player = None
        try:
            try:
                first = await asyncio.wait_for(recv(ws), JOIN_TIMEOUT_S)
            except asyncio.TimeoutError:
                await close_quietly(ws, 1008)
                return
            msg = parse(first)
            if msg.get("type") != "join":
                await close_quietly(ws, 1008)
                return
            try:
                player = room.join(msg.get("name", ""), msg.get("token"))
            except JoinError as err:
                await send(ws, {"type": "error", "code": err.code})
                await close_quietly(ws, 1008)
                return

            old = sockets.get(player.id)  # same seat opened twice: newest connection wins
            sockets[player.id] = ws
            if old is not None:
                await send(old, {"type": "replaced"})
                await close_quietly(old, 4000)
            room.set_connected(player.id, True)
            await send(
                ws,
                {"type": "welcome", "id": player.id, "token": player.token, "name": player.name},
            )
            await push_state()

            bucket = TokenBucket(rate=5, burst=20)
            while True:
                msg = parse(await recv(ws))
                if not msg or not bucket.allow():
                    continue
                kind = msg.get("type")
                if kind == "ping":
                    t = msg.get("t")
                    await send(ws, {"type": "pong", "t": t if isinstance(t, (int, float)) else 0})
                elif kind == "rtt":
                    room.record_rtt(player.id, msg.get("ms"))
                    await push_state(force=False)
                elif kind == "buzz_ack":
                    if room.ack_buzz(player.id, msg.get("id")):
                        await push_state()
                elif kind == "leave":
                    room.kick(player.id)
                    if sockets.get(player.id) is ws:
                        del sockets[player.id]
                    await push_state()
                    await close_quietly(ws, 1000)
                    return
        except WebSocketDisconnect:
            pass
        finally:
            if player is not None and sockets.get(player.id) is ws:
                del sockets[player.id]
                room.set_connected(player.id, False)
                await push_state()

    # -- host screen -----------------------------------------------------
    @app.websocket("/ws/host")
    async def ws_host(ws: WebSocket) -> None:
        if not key_ok(ws.query_params.get("key")):
            await ws.close(code=1008)  # before accept: the browser sees a 403
            return
        await ws.accept()
        hosts.add(ws)
        try:
            await send(ws, {"type": "state", **room.snapshot()})
            while True:
                msg = parse(await recv(ws))
                kind = msg.get("type")
                if kind == "buzz":
                    payload = {"type": "buzz", "id": room.start_buzz()}
                    await asyncio.gather(*(send(p, payload) for p in list(sockets.values())))
                    await push_state()
                elif kind == "lock":
                    room.locked = bool(msg.get("locked"))
                    await push_state()
                elif kind == "kick":
                    pid = msg.get("id")
                    if isinstance(pid, str) and room.kick(pid) is not None:
                        victim = sockets.pop(pid, None)
                        if victim is not None:
                            await send(victim, {"type": "kicked"})
                            await close_quietly(victim, 4001)
                        await push_state()
        except WebSocketDisconnect:
            pass
        finally:
            hosts.discard(ws)

    return app


app = create_app()
