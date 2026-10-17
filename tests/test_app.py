import time

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from app.main import create_app

KEY = "secret-key"


@pytest.fixture
def client():
    # Entering the client keeps one event loop alive, which the shared hub state needs.
    with TestClient(create_app(host_key=KEY, public_url="")) as c:
        yield c


def receive_until(ws, predicate, limit=20):
    for _ in range(limit):
        msg = ws.receive_json()
        if predicate(msg):
            return msg
    raise AssertionError("expected message never arrived")


def join(ws, name="Sam", token=None):
    payload = {"type": "join", "name": name}
    if token:
        payload["token"] = token
    ws.send_json(payload)
    return ws.receive_json()


def is_state(msg):
    return msg["type"] == "state"


# -- HTTP ------------------------------------------------------------------
def test_health(client):
    assert client.get("/health").json() == {"ok": True}


def test_root_redirects_to_the_phone_page(client):
    res = client.get("/", follow_redirects=False)
    assert res.status_code in (302, 307)
    assert res.headers["location"] == "/play"


def test_phone_page_and_assets_are_served(client):
    assert "Halloween Office Trivia" in client.get("/play").text
    assert client.get("/static/style.css").status_code == 200
    assert client.get("/static/play.js").status_code == 200


def test_host_page_needs_the_key(client):
    assert client.get("/host").status_code == 403
    assert client.get("/host?key=wrong").status_code == 403
    assert client.get(f"/host?key={KEY}").status_code == 200


def test_security_headers_are_set(client):
    headers = client.get("/play").headers
    assert headers["x-content-type-options"] == "nosniff"
    assert headers["referrer-policy"] == "no-referrer"
    assert "default-src 'self'" in headers["content-security-policy"]


def test_api_docs_are_not_exposed(client):
    assert client.get("/docs").status_code == 404
    assert client.get("/openapi.json").status_code == 404


def test_join_url_follows_the_request_host(client):
    assert client.get("/api/join-url").json() == {"url": "http://testserver/play"}


def test_join_url_prefers_the_configured_public_url():
    app = create_app(host_key=KEY, public_url="https://party.example.com/")
    with TestClient(app) as c:
        assert c.get("/api/join-url").json() == {"url": "https://party.example.com/play"}


def test_qr_code_is_an_svg(client):
    res = client.get("/qr.svg")
    assert res.headers["content-type"] == "image/svg+xml"
    assert "<svg" in res.text


# -- WebSockets ------------------------------------------------------------
def test_host_sees_players_arrive_and_leave(client):
    with client.websocket_connect(f"/ws/host?key={KEY}") as host:
        assert host.receive_json()["players"] == []
        with client.websocket_connect("/ws/play") as phone:
            welcome = join(phone)
            assert welcome["type"] == "welcome"
            assert welcome["name"] == "Sam"
            state = receive_until(host, lambda m: is_state(m) and m["players"])
            assert state["players"][0]["connected"] is True
            assert "token" not in state["players"][0]
        receive_until(
            host,
            lambda m: is_state(m) and m["players"] and not m["players"][0]["connected"],
        )


def test_host_socket_rejects_a_wrong_key(client):
    with pytest.raises(WebSocketDisconnect):
        with client.websocket_connect("/ws/host?key=nope"):
            pass


def test_first_message_must_be_a_join(client):
    with client.websocket_connect("/ws/play") as phone:
        phone.send_json({"type": "ping", "t": 1})
        with pytest.raises(WebSocketDisconnect):
            phone.receive_json()


def test_names_are_cleaned_and_made_unique(client):
    with client.websocket_connect("/ws/play") as a, client.websocket_connect("/ws/play") as b:
        assert join(a, "  Sam\n​ ")["name"] == "Sam"
        assert join(b, "sam")["name"] == "sam 2"


def test_ping_is_echoed_for_latency_measurement(client):
    with client.websocket_connect("/ws/play") as phone:
        join(phone)
        phone.send_json({"type": "ping", "t": 123})
        assert receive_until(phone, lambda m: m["type"] == "pong")["t"] == 123


def test_reported_latency_reaches_the_host(client):
    with client.websocket_connect(f"/ws/host?key={KEY}") as host:
        host.receive_json()
        with client.websocket_connect("/ws/play") as phone:
            join(phone)
            receive_until(host, lambda m: is_state(m) and m["players"])
            time.sleep(1.1)  # latency updates are throttled to one push per second
            phone.send_json({"type": "rtt", "ms": 42})
            receive_until(host, lambda m: is_state(m) and m["players"][0]["rtt_ms"] == 42)


def test_test_buzz_round_trip(client):
    with client.websocket_connect(f"/ws/host?key={KEY}") as host:
        host.receive_json()
        with client.websocket_connect("/ws/play") as phone:
            join(phone)
            receive_until(host, lambda m: is_state(m) and m["players"])
            host.send_json({"type": "buzz"})
            buzz = receive_until(phone, lambda m: m["type"] == "buzz")
            phone.send_json({"type": "buzz_ack", "id": buzz["id"]})
            receive_until(
                host,
                lambda m: is_state(m)
                and any(p["buzz_ack_ms"] is not None for p in m["players"]),
            )


def test_locked_game_blocks_newcomers_but_not_returning_phones(client):
    with client.websocket_connect(f"/ws/host?key={KEY}") as host:
        host.receive_json()
        with client.websocket_connect("/ws/play") as phone:
            token = join(phone)["token"]
        host.send_json({"type": "lock", "locked": True})
        receive_until(host, lambda m: is_state(m) and m["locked"])
        with client.websocket_connect("/ws/play") as newcomer:
            assert join(newcomer, "Late") == {"type": "error", "code": "locked"}
        with client.websocket_connect("/ws/play") as returning:
            assert join(returning, "Sam", token)["type"] == "welcome"


def test_host_can_remove_a_player(client):
    with client.websocket_connect(f"/ws/host?key={KEY}") as host:
        host.receive_json()
        with client.websocket_connect("/ws/play") as phone:
            player_id = join(phone)["id"]
            receive_until(host, lambda m: is_state(m) and m["players"])
            host.send_json({"type": "kick", "id": player_id})
            assert receive_until(phone, lambda m: m["type"] == "kicked")
            receive_until(host, lambda m: is_state(m) and not m["players"])


def test_leaving_frees_the_seat(client):
    with client.websocket_connect(f"/ws/host?key={KEY}") as host:
        host.receive_json()
        with client.websocket_connect("/ws/play") as phone:
            join(phone)
            receive_until(host, lambda m: is_state(m) and m["players"])
            phone.send_json({"type": "leave"})
            receive_until(host, lambda m: is_state(m) and not m["players"])


def test_same_seat_on_a_second_screen_replaces_the_first(client):
    with client.websocket_connect("/ws/play") as first:
        token = join(first)["token"]
        with client.websocket_connect("/ws/play") as second:
            assert join(second, "Sam", token)["type"] == "welcome"
            assert receive_until(first, lambda m: m["type"] == "replaced")


def test_garbage_messages_are_ignored(client):
    with client.websocket_connect("/ws/play") as phone:
        join(phone)
        phone.send_text("not json")
        phone.send_text("[1, 2, 3]")
        phone.send_text("[" * 1500)
        phone.send_json({"type": "nope"})
        phone.send_json({"type": "ping", "t": 7})
        assert receive_until(phone, lambda m: m["type"] == "pong")["t"] == 7
