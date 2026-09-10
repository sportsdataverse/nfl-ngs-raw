"""Stage 01 -- league/schedule per season: raw json, tidy parquet, and the master.

The schedule is the enumeration surface for every other stage (which
``(seasonType, week)`` pairs have started, which games are FINAL), and the
consumer's too: ``nfl-ngs-data`` reads ``ngs/schedules/parquet/
ngs_schedule_{season}.parquet`` over HTTPS to build game-id lists without
listing a directory it cannot clone (data-pipeline skill, step 9a).

Finality is derived from the DATA: ``score.phase`` in {FINAL, FINAL OVERTIME}.
A week whose games have not all reached that phase is re-fetched on every run
regardless of what is on disk -- a scraper-written marker only records that a
fetch happened, not that the thing it fetched was finished.
"""

from __future__ import annotations

import time
from datetime import datetime, timezone
from pathlib import Path

import polars as pl

from ngs_raw import SEASON_TYPES
from ngs_raw.fetch import get_json
from ngs_raw.store import capture, read_json, tree_root, write_json_atomic

FINAL_PHASES = {"FINAL", "FINAL OVERTIME"}

_COLS = {
    "season": pl.Int64,
    "season_type": pl.Utf8,
    "week": pl.Int64,
    "week_name_abbr": pl.Utf8,
    "game_id": pl.Int64,
    "game_key": pl.Int64,
    "smart_id": pl.Utf8,
    "game_date": pl.Utf8,
    "game_time": pl.Utf8,
    "game_time_eastern": pl.Utf8,
    "iso_time": pl.Int64,
    "home_team_id": pl.Utf8,
    "home_team_abbr": pl.Utf8,
    "home_display_name": pl.Utf8,
    "home_nickname": pl.Utf8,
    "visitor_team_id": pl.Utf8,
    "visitor_team_abbr": pl.Utf8,
    "visitor_display_name": pl.Utf8,
    "visitor_nickname": pl.Utf8,
    "phase": pl.Utf8,
    "home_score": pl.Int64,
    "visitor_score": pl.Int64,
    "site_id": pl.Int64,
    "site_full_name": pl.Utf8,
    "site_city": pl.Utf8,
    "site_state": pl.Utf8,
    "roof_type": pl.Utf8,
    "network_channel": pl.Utf8,
    "ngs_game": pl.Boolean,
    "validated": pl.Boolean,
}


def _row(g: dict) -> dict:
    score = g.get("score") or {}
    site = g.get("site") or {}

    def pts(side: str):
        return ((score.get(side) or {}).get("pointTotal")) if score else None

    return {
        "season": g.get("season"),
        "season_type": g.get("seasonType"),
        "week": g.get("week"),
        "week_name_abbr": g.get("weekNameAbbr"),
        "game_id": g.get("gameId"),
        "game_key": g.get("gameKey"),
        "smart_id": g.get("smartId"),
        "game_date": g.get("gameDate"),
        "game_time": g.get("gameTime"),
        "game_time_eastern": g.get("gameTimeEastern"),
        "iso_time": g.get("isoTime"),
        "home_team_id": g.get("homeTeamId"),
        "home_team_abbr": g.get("homeTeamAbbr"),
        "home_display_name": g.get("homeDisplayName"),
        "home_nickname": g.get("homeNickname"),
        "visitor_team_id": g.get("visitorTeamId"),
        "visitor_team_abbr": g.get("visitorTeamAbbr"),
        "visitor_display_name": g.get("visitorDisplayName"),
        "visitor_nickname": g.get("visitorNickname"),
        "phase": score.get("phase") if score else None,
        "home_score": pts("homeTeamScore"),
        "visitor_score": pts("visitorTeamScore"),
        "site_id": site.get("siteId"),
        "site_full_name": site.get("siteFullName"),
        "site_city": site.get("siteCity"),
        "site_state": site.get("siteState"),
        "roof_type": site.get("roofType"),
        "network_channel": g.get("networkChannel"),
        "ngs_game": g.get("ngsGame"),
        "validated": g.get("validated"),
    }


def tidy(payload: list[dict]) -> pl.DataFrame:
    """Raw schedule list -> tidy frame with the documented, fixed schema."""
    rows = [_row(g) for g in payload if isinstance(g, dict) and g.get("gameId") is not None]
    df = pl.DataFrame(rows, schema=_COLS)
    return df.sort(["season", "season_type", "week", "game_id"])


def json_path(season: int, root: str | Path | None = None) -> Path:
    return tree_root(root) / "schedules" / "json" / f"{season}.json"


def parquet_path(season: int, root: str | Path | None = None) -> Path:
    return tree_root(root) / "schedules" / "parquet" / f"ngs_schedule_{season}.parquet"


def master_path(root: str | Path | None = None) -> Path:
    return tree_root(root) / "ngs_schedule_master.parquet"


def fetch_season(season: int) -> list[dict] | None:
    body = get_json("/league/schedule", {"season": season})
    return body if isinstance(body, list) else None


def scrape_season(season: int, *, root: str | Path | None = None, rescrape: bool = True) -> dict:
    """Bank the season schedule json + parquet; upsert the master. Returns counters."""
    t0 = time.monotonic()
    jp = json_path(season, root)
    # A season schedule is a living document until every game is FINAL, so
    # it is re-fetched by default; `rescrape=False` only trusts a banked copy
    # whose every game already reads FINAL.
    if not rescrape and jp.exists():
        banked = read_json(jp)
        if (
            isinstance(banked, list)
            and banked
            and all(((g.get("score") or {}).get("phase") in FINAL_PHASES) for g in banked)
        ):
            df = tidy(banked)
            return {
                "season": season,
                "status": "skipped",
                "games": df.height,
                "elapsed": time.monotonic() - t0,
            }
    status = capture(jp, lambda: fetch_season(season), None, rescrape=True)
    if status != "wrote":
        return {
            "season": season,
            "status": status,
            "games": 0,
            "elapsed": time.monotonic() - t0,
        }
    payload = read_json(jp)
    df = tidy(payload)
    pq = parquet_path(season, root)
    pq.parent.mkdir(parents=True, exist_ok=True)
    df.write_parquet(pq)
    upsert_master(df, root=root)
    return {
        "season": season,
        "status": "wrote",
        "games": df.height,
        "elapsed": time.monotonic() - t0,
    }


def upsert_master(df: pl.DataFrame, *, root: str | Path | None = None) -> pl.DataFrame:
    """Upsert by game_id, never wholesale clobber (idempotency contract #3)."""
    mp = master_path(root)
    if mp.exists():
        old = pl.read_parquet(mp)
        old = old.filter(~pl.col("game_id").is_in(df.get_column("game_id").implode()))
        out = pl.concat([old, df], how="diagonal_relaxed")
    else:
        out = df
    out = out.sort(["season", "season_type", "week", "game_id"])
    mp.parent.mkdir(parents=True, exist_ok=True)
    out.write_parquet(mp)
    return out


# --- enumeration for the other stages ----------------------------------------


def load_season(season: int, *, root: str | Path | None = None) -> pl.DataFrame | None:
    pq = parquet_path(season, root)
    return pl.read_parquet(pq) if pq.exists() else None


def week_plan(df: pl.DataFrame, *, now_ms: int | None = None) -> list[tuple[str, int, bool]]:
    """``(season_type, week, complete)`` for every week that has STARTED.

    Started = at least one game's kickoff is in the past, so a future week is
    never requested (the API answers an empty ``stats`` list for it, and that
    must not be banked). ``complete`` = every game in the week is FINAL --
    the only state a resume may trust a banked file for.
    """
    now_ms = now_ms if now_ms is not None else int(datetime.now(timezone.utc).timestamp() * 1000)
    out: list[tuple[str, int, bool]] = []
    grouped = (
        df.group_by(["season_type", "week"])
        .agg(
            pl.col("iso_time").min().alias("first_kick"),
            (pl.col("phase").is_in(list(FINAL_PHASES))).all().alias("complete"),
        )
        .sort(["season_type", "week"])
    )
    for st, wk, first_kick, complete in grouped.iter_rows():
        if st not in SEASON_TYPES or wk is None:
            continue
        if first_kick is None or first_kick > now_ms:
            continue
        out.append((st, int(wk), bool(complete)))
    # Deterministic order: PRE, REG, POST, then week.
    order = {t: i for i, t in enumerate(SEASON_TYPES)}
    return sorted(out, key=lambda r: (order[r[0]], r[1]))


def season_type_complete(df: pl.DataFrame, season_type: str) -> bool:
    sub = df.filter(pl.col("season_type") == season_type)
    return sub.height > 0 and bool(sub.get_column("phase").is_in(list(FINAL_PHASES)).all())


def season_type_started(df: pl.DataFrame, season_type: str, *, now_ms: int | None = None) -> bool:
    now_ms = now_ms if now_ms is not None else int(datetime.now(timezone.utc).timestamp() * 1000)
    sub = df.filter(pl.col("season_type") == season_type)
    if sub.height == 0:
        return False
    first = sub.get_column("iso_time").min()
    return first is not None and first <= now_ms


def final_game_ids(df: pl.DataFrame) -> list[int]:
    return df.filter(pl.col("phase").is_in(list(FINAL_PHASES))).get_column("game_id").cast(pl.Int64).to_list()


def write_schedule_json(season: int, payload: list[dict], *, root: str | Path | None = None) -> Path:
    """Test/offline helper: bank a schedule payload without fetching."""
    jp = json_path(season, root)
    write_json_atomic(jp, payload)
    df = tidy(payload)
    pq = parquet_path(season, root)
    pq.parent.mkdir(parents=True, exist_ok=True)
    df.write_parquet(pq)
    upsert_master(df, root=root)
    return pq
