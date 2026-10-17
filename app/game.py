"""Lobby state for the party game.

Pure Python with no web-framework imports, so every rule here is easy to unit test.
The server (main.py) owns one Room and is the single source of truth: phones only
send intentions (join, acknowledge a buzz) and never decide anything themselves.
"""
from __future__ import annotations

import secrets
import time
from dataclasses import dataclass, field

MAX_NAME_LEN = 20
MAX_PLAYERS = 30
MAX_RTT_MS = 60_000


class JoinError(Exception):
    """Raised when a phone can't take a seat. `code` is one of: name, locked, full."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def clean_name(raw: object) -> str:
    """Collapse whitespace, drop control/invisible characters, and cap the length."""
    text = " ".join(str(raw).split())
    text = "".join(ch for ch in text if ch.isprintable())
    return text[:MAX_NAME_LEN].strip()


@dataclass
class Player:
    id: str  # public handle: safe to show on the host screen
    token: str  # private secret that lets a phone reclaim its seat after a drop
    name: str
    connected: bool = False
    rtt_ms: int | None = None
    buzz_ack_ms: int | None = None
    joined_at: float = field(default_factory=time.time)


class Room:
    def __init__(self, max_players: int = MAX_PLAYERS) -> None:
        self.max_players = max_players
        self.locked = False
        self.players: dict[str, Player] = {}
        self._by_token: dict[str, str] = {}
        self.buzz_id = 0
        self._buzz_started: float | None = None

    # -- seats -----------------------------------------------------------
    def join(self, name: object, token: object = None) -> Player:
        """Seat a new player, or hand a returning player their existing seat."""
        if isinstance(token, str) and token in self._by_token:
            return self.players[self._by_token[token]]  # reconnects work even when locked
        cleaned = clean_name(name)
        if not cleaned:
            raise JoinError("name")
        if self.locked:
            raise JoinError("locked")
        if len(self.players) >= self.max_players:
            raise JoinError("full")
        player = Player(
            id=secrets.token_hex(4),
            token=secrets.token_urlsafe(16),
            name=self._unique(cleaned),
        )
        self.players[player.id] = player
        self._by_token[player.token] = player.id
        return player

    def _unique(self, name: str) -> str:
        taken = {p.name.casefold() for p in self.players.values()}
        if name.casefold() not in taken:
            return name
        n = 2
        while True:
            suffix = f" {n}"
            candidate = name[: MAX_NAME_LEN - len(suffix)] + suffix
            if candidate.casefold() not in taken:
                return candidate
            n += 1

    def kick(self, player_id: str) -> Player | None:
        player = self.players.pop(player_id, None)
        if player is not None:
            self._by_token.pop(player.token, None)
        return player

    def set_connected(self, player_id: str, connected: bool) -> None:
        player = self.players.get(player_id)
        if player is not None:
            player.connected = connected

    # -- connection test -------------------------------------------------
    def record_rtt(self, player_id: str, ms: object) -> None:
        player = self.players.get(player_id)
        if player is None:
            return
        try:
            value = int(ms)  # type: ignore[arg-type]
        except (TypeError, ValueError, OverflowError):
            return
        player.rtt_ms = max(0, min(MAX_RTT_MS, value))

    def start_buzz(self) -> int:
        self.buzz_id += 1
        self._buzz_started = time.monotonic()
        for player in self.players.values():
            player.buzz_ack_ms = None
        return self.buzz_id

    def ack_buzz(self, player_id: str, buzz_id: object) -> bool:
        player = self.players.get(player_id)
        if (
            player is None
            or self._buzz_started is None
            or buzz_id != self.buzz_id
            or player.buzz_ack_ms is not None
        ):
            return False
        player.buzz_ack_ms = int((time.monotonic() - self._buzz_started) * 1000)
        return True

    # -- what the host screen sees ---------------------------------------
    def snapshot(self) -> dict:
        """Host-facing view. Never includes tokens."""
        ordered = sorted(self.players.values(), key=lambda p: p.joined_at)
        return {
            "locked": self.locked,
            "max_players": self.max_players,
            "buzz_id": self.buzz_id,
            "players": [
                {
                    "id": p.id,
                    "name": p.name,
                    "connected": p.connected,
                    "rtt_ms": p.rtt_ms,
                    "buzz_ack_ms": p.buzz_ack_ms,
                }
                for p in ordered
            ],
        }
