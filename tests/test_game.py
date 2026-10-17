import pytest

from app.game import MAX_NAME_LEN, MAX_RTT_MS, JoinError, Room, clean_name
from app.ratelimit import TokenBucket


def test_clean_name_collapses_whitespace_and_strips_invisible_characters():
    assert clean_name("  Sam\n\t  the   Man ​") == "Sam the Man"


def test_clean_name_caps_length():
    assert len(clean_name("a" * 50)) == MAX_NAME_LEN


def test_join_requires_a_name():
    with pytest.raises(JoinError) as err:
        Room().join("   \n ")
    assert err.value.code == "name"


def test_duplicate_names_get_a_number():
    room = Room()
    first = room.join("Sam")
    second = room.join("sam")
    third = room.join("SAM")
    assert [first.name, second.name, third.name] == ["Sam", "sam 2", "SAM 3"]


def test_duplicate_suffix_never_exceeds_max_length():
    room = Room()
    long_name = "x" * MAX_NAME_LEN
    room.join(long_name)
    assert len(room.join(long_name).name) == MAX_NAME_LEN


def test_locked_room_turns_away_new_players_but_not_returning_ones():
    room = Room()
    sam = room.join("Sam")
    room.locked = True
    with pytest.raises(JoinError) as err:
        room.join("Late")
    assert err.value.code == "locked"
    assert room.join("ignored", sam.token) is sam


def test_full_room_turns_players_away():
    room = Room(max_players=2)
    room.join("A")
    room.join("B")
    with pytest.raises(JoinError) as err:
        room.join("C")
    assert err.value.code == "full"


def test_unknown_or_malformed_token_is_treated_as_new_player():
    room = Room()
    sam = room.join("Sam", {"not": "a string"})
    other = room.join("Other", "made-up-token")
    assert sam.id != other.id


def test_kick_invalidates_the_token():
    room = Room()
    sam = room.join("Sam")
    assert room.kick(sam.id) is sam
    assert room.join("Sam", sam.token).id != sam.id


def test_rtt_is_clamped_and_garbage_is_ignored():
    room = Room()
    sam = room.join("Sam")
    room.record_rtt(sam.id, 42)
    assert sam.rtt_ms == 42
    room.record_rtt(sam.id, -5)
    assert sam.rtt_ms == 0
    room.record_rtt(sam.id, 10**9)
    assert sam.rtt_ms == MAX_RTT_MS
    for junk in ("abc", None, float("nan"), float("inf"), [1]):
        room.record_rtt(sam.id, junk)
    assert sam.rtt_ms == MAX_RTT_MS


def test_buzz_ack_counts_once_and_only_for_the_current_buzz():
    room = Room()
    sam = room.join("Sam")
    assert room.ack_buzz(sam.id, 1) is False  # nothing sent yet
    first = room.start_buzz()
    assert room.ack_buzz(sam.id, first + 1) is False
    assert room.ack_buzz(sam.id, first) is True
    assert sam.buzz_ack_ms is not None
    assert room.ack_buzz(sam.id, first) is False
    second = room.start_buzz()
    assert sam.buzz_ack_ms is None
    assert room.ack_buzz(sam.id, second) is True


def test_snapshot_never_exposes_tokens():
    room = Room()
    sam = room.join("Sam")
    snapshot = room.snapshot()
    assert sam.token not in str(snapshot)
    assert snapshot["players"][0]["id"] == sam.id


def test_token_bucket_limits_bursts():
    bucket = TokenBucket(rate=0, burst=3)
    assert [bucket.allow() for _ in range(5)] == [True, True, True, False, False]
