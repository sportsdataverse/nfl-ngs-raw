"""Offline tests: store validity contract, schedule tidy/enumeration, stage resume.

No network -- every fetch is monkeypatched. The finality rules are the thing
under test: a future week is never requested, an incomplete week is always
refetched, a complete week on disk is trusted, and an empty payload is never
persisted.
"""

from __future__ import annotations

import json
from pathlib import Path

import polars as pl
import pytest
from ngs_raw import schedule, statboard, store

FINAL = {"phase": "FINAL", "homeTeamScore": {"pointTotal": 20}, "visitorTeamScore": {"pointTotal": 17}}
PRE = {"phase": "PREGAME", "homeTeamScore": {"pointTotal": 0}, "visitorTeamScore": {"pointTotal": 0}}


def game(gid: int, st: str, wk: int, iso: int, score: dict) -> dict:
    return {
        "season": 2025,
        "seasonType": st,
        "week": wk,
        "gameId": gid,
        "gameKey": gid,
        "isoTime": iso,
        "homeTeamId": "0810",
        "visitorTeamId": "1800",
        "homeTeamAbbr": "CHI",
        "visitorTeamAbbr": "GB",
        "score": score,
        "site": {"siteId": 1, "siteFullName": "Soldier Field"},
        "ngsGame": True,
        "validated": True,
    }


NOW = 1_000_000
SCHED = [
    game(1, "REG", 1, NOW - 10, FINAL),
    game(2, "REG", 1, NOW - 9, FINAL),
    game(3, "REG", 2, NOW - 5, FINAL),
    game(4, "REG", 2, NOW - 4, PRE),  # week 2 in progress
    game(5, "REG", 3, NOW + 100, PRE),  # future week
    game(6, "POST", 19, NOW + 200, PRE),  # future type
]


# --- store -------------------------------------------------------------------


def test_str2bool_is_tolerant():
    assert store.str2bool("TRUE") and store.str2bool("yes") and store.str2bool(True)
    assert not store.str2bool("false") and not store.str2bool("garbage") and not store.str2bool(None)


def test_has_rows_semantics():
    assert store.has_rows({"stats": [1]}, "stats")
    assert not store.has_rows({"stats": []}, "stats")
    assert not store.has_rows({}, "stats")
    assert store.has_rows([1], None) and not store.has_rows([], None)
    assert store.has_rows({"schedule": {}}, None) and not store.has_rows({}, None)
    # ANY_LIST: a full envelope of empty category lists is NOT data (pre-floor statboard/leaders)
    assert not store.has_rows({"season": 2009, "seasonType": "REG", "fastestSacks": [], "x": []}, store.ANY_LIST)
    assert store.has_rows({"season": 2024, "fastestSacks": [{"play": 1}], "x": []}, store.ANY_LIST)


def test_capture_never_persists_empty_and_respects_resume(tmp_path: Path):
    p = tmp_path / "x.json"
    assert store.capture(p, lambda: {"stats": []}, "stats", rescrape=True) == "empty"
    assert not p.exists()
    assert store.capture(p, lambda: None, "stats", rescrape=True) == "absent"
    assert store.capture(p, lambda: {"stats": [1]}, "stats", rescrape=True) == "wrote"
    assert json.loads(p.read_text()) == {"stats": [1]}
    assert store.capture(p, lambda: {"stats": [2]}, "stats", rescrape=False) == "skipped"
    assert store.capture(p, lambda: {"stats": [2]}, "stats", rescrape=True) == "wrote"
    # presence is not validity: a corrupt file is refetched even without rescrape
    p.write_text("{not json")
    assert store.capture(p, lambda: {"stats": [3]}, "stats", rescrape=False) == "wrote"


# --- schedule ----------------------------------------------------------------


def test_tidy_schema_and_master_upsert(tmp_path: Path):
    pq = schedule.write_schedule_json(2025, SCHED, root=tmp_path)
    df = pl.read_parquet(pq)
    assert df.height == 6 and df.schema["game_id"] == pl.Int64 and df.schema["home_team_id"] == pl.Utf8
    assert df.filter(pl.col("game_id") == 1).item(0, "home_score") == 20
    master = pl.read_parquet(schedule.master_path(tmp_path))
    assert master.height == 6
    # upsert, not clobber: re-writing one game changes nothing else
    schedule.upsert_master(schedule.tidy([game(1, "REG", 1, NOW - 10, FINAL)]), root=tmp_path)
    assert pl.read_parquet(schedule.master_path(tmp_path)).height == 6


def test_week_plan_only_started_weeks_and_completeness():
    df = schedule.tidy(SCHED)
    plan = schedule.week_plan(df, now_ms=NOW)
    assert plan == [("REG", 1, True), ("REG", 2, False)]
    assert schedule.season_type_started(df, "REG", now_ms=NOW)
    assert not schedule.season_type_started(df, "POST", now_ms=NOW)
    assert not schedule.season_type_complete(df, "REG")
    assert schedule.final_game_ids(df) == [1, 2, 3]


# --- statboard stage resume rules -------------------------------------------


def test_statboard_refetches_incomplete_trusts_complete(tmp_path: Path, monkeypatch):
    schedule.write_schedule_json(2025, SCHED, root=tmp_path)
    calls: list[tuple] = []

    def fake_get(path, params=None):
        calls.append((path, params.get("seasonType"), params.get("week")))
        return {"stats": [{"x": 1}]}

    monkeypatch.setattr(statboard, "get_json", fake_get)
    monkeypatch.setattr(statboard, "week_plan", lambda df: _plan(df))
    monkeypatch.setattr(
        statboard, "season_type_started", lambda df, st: schedule.season_type_started(df, st, now_ms=NOW)
    )

    out = statboard.scrape_season(2025, root=tmp_path, rescrape=False, stats=("passing",))
    # REG aggregate + week 1 + week 2 (future week 3 and POST never requested)
    assert sorted(c[2] for c in calls if c[2] is not None) == [1, 2] and sum(c[2] is None for c in calls) == 1
    assert out["wrote"] == 3
    calls.clear()
    out = statboard.scrape_season(2025, root=tmp_path, rescrape=False, stats=("passing",))
    # complete week 1 trusted on disk; incomplete week 2 + the still-open REG aggregate refetched
    assert [c[2] for c in calls if c[2] is not None] == [2] and sum(c[2] is None for c in calls) == 1
    assert out["skipped"] == 1 and out["wrote"] == 2


def _plan(df):
    return schedule.week_plan(df, now_ms=NOW)


@pytest.mark.parametrize("stat", ["passing", "leaders"])
def test_statboard_empty_payload_not_persisted(tmp_path: Path, monkeypatch, stat):
    schedule.write_schedule_json(2025, SCHED, root=tmp_path)
    empty_leaders = {"season": 2025, "seasonType": "REG", "fastestSacks": [], "longestCompletions": []}
    monkeypatch.setattr(
        statboard, "get_json", lambda path, params=None: {"stats": []} if stat != "leaders" else empty_leaders
    )
    monkeypatch.setattr(statboard, "week_plan", lambda df: _plan(df))
    monkeypatch.setattr(
        statboard, "season_type_started", lambda df, st: schedule.season_type_started(df, st, now_ms=NOW)
    )
    out = statboard.scrape_season(2025, root=tmp_path, rescrape=True, stats=(stat,))
    assert out["wrote"] == 0 and out["empty"] >= 1
    assert not list((tmp_path / "statboard").rglob("*.json"))
