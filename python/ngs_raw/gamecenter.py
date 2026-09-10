"""Stage 05 -- gamecenter/overview per FINAL game.

Files: ``ngs/gamecenter/{season}/{gameId}.json``. Only games the schedule
reports FINAL are requested: a pre-kickoff overview is a well-formed payload
with nothing in it, and banking one would make a presence-based resume skip
the real one forever. Once a FINAL game's overview is valid on disk it is
trusted (a finished game does not change) unless ``rescrape`` is set.

Reaches back to at least 2012 (verified); older seasons simply come back
absent and are counted, never persisted.
"""

from __future__ import annotations

import time
from pathlib import Path

import polars as pl

from ngs_raw.fetch import FetchError, get_json
from ngs_raw.schedule import final_game_ids, load_season
from ngs_raw.store import capture, tree_root


def out_path(season: int, game_id: int, root=None) -> Path:
    return tree_root(root) / "gamecenter" / str(season) / f"{game_id}.json"


def _fetch(game_id: int):
    body = get_json("/gamecenter/overview", {"gameId": game_id})
    # An overview with no `schedule` block is the API's empty answer.
    if isinstance(body, dict) and not body.get("schedule"):
        return None
    return body


def scrape_season(
    season: int,
    *,
    root: str | Path | None = None,
    rescrape: bool = False,
    limit: int = 0,
    schedule: pl.DataFrame | None = None,
) -> dict:
    t0 = time.monotonic()
    df = schedule if schedule is not None else load_season(season, root=root)
    counts = {"wrote": 0, "skipped": 0, "empty": 0, "absent": 0, "failed": 0}
    if df is None or df.height == 0:
        return {"season": season, "status": "no-schedule", **counts, "elapsed": 0.0}
    ids = final_game_ids(df)
    if limit:
        ids = ids[:limit]
    for i, gid in enumerate(ids, 1):
        p = out_path(season, gid, root)
        try:
            counts[capture(p, lambda g=gid: _fetch(g), None, rescrape=rescrape)] += 1
        except FetchError as exc:
            counts["failed"] += 1
            print(f"  gamecenter {season} {gid} FAILED: {exc}", flush=True)
        if i % 100 == 0:
            print(f"  gamecenter {season}: {i}/{len(ids)} {counts}", flush=True)
    return {
        "season": season,
        "status": "ok",
        "games": len(ids),
        **counts,
        "elapsed": time.monotonic() - t0,
    }
