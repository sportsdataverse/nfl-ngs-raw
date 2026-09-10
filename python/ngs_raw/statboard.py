"""Stage 03 -- statboard/{passing,rushing,receiving,leaders} per season-type & week.

Files: ``ngs/statboard/{stat}/{season}/{TYPE}_{week}.json`` where ``week`` is
an int for a weekly leaderboard or ``all`` for the season aggregate. Measured
live: REG and PRE aggregates exist; POST answers ``stats: []`` at season scope
and is week-only, so its aggregate is simply never banked (empty payloads are
never persisted -- see ``store.capture``).

``statboard/leaders`` is a multi-category season-scope object (fastest ball
carriers, fastest sacks, ...) with no ``stats`` list; it is banked at ``all``
scope only, per season type.
"""

from __future__ import annotations

import time
from pathlib import Path

import polars as pl

from ngs_raw import STATBOARD_STATS
from ngs_raw.fetch import FetchError, get_json
from ngs_raw.schedule import (
    load_season,
    season_type_complete,
    season_type_started,
    week_plan,
)
from ngs_raw.store import ANY_LIST, capture, tree_root


def out_path(stat: str, season: int, season_type: str, week: int | str, root=None) -> Path:
    return tree_root(root) / "statboard" / stat / str(season) / f"{season_type}_{week}.json"


def _fetch(stat: str, season: int, season_type: str, week: int | None):
    return get_json(
        f"/statboard/{stat}",
        {"season": season, "seasonType": season_type, "week": week},
    )


def _list_key(stat: str) -> str | None:
    # `leaders` is an object of category lists: valid only if SOME list has rows.
    return ANY_LIST if stat == "leaders" else "stats"


def scrape_season(
    season: int,
    *,
    root: str | Path | None = None,
    rescrape: bool = True,
    stats: tuple[str, ...] = STATBOARD_STATS,
    schedule: pl.DataFrame | None = None,
) -> dict:
    """Bank every started (type, week) leaderboard + the season aggregates.

    A week that is not yet complete (some game not FINAL) is always refetched
    -- ``rescrape=False`` only lets a resume trust weeks the schedule says are
    over. Same for the ``all`` aggregate: trusted once the season type is
    complete.
    """
    t0 = time.monotonic()
    df = schedule if schedule is not None else load_season(season, root=root)
    counts = {"wrote": 0, "skipped": 0, "empty": 0, "absent": 0, "failed": 0}
    if df is None or df.height == 0:
        return {"season": season, "status": "no-schedule", **counts, "elapsed": 0.0}
    plan = week_plan(df)
    for stat in stats:
        key = _list_key(stat)
        # Season aggregates, per season type that has started.
        for st in ("PRE", "REG", "POST"):
            if not season_type_started(df, st):
                continue
            trust = (not rescrape) and season_type_complete(df, st)
            p = out_path(stat, season, st, "all", root)
            try:
                counts[
                    capture(
                        p,
                        lambda s=stat, t=st: _fetch(s, season, t, None),
                        key,
                        rescrape=not trust,
                    )
                ] += 1
            except FetchError as exc:
                counts["failed"] += 1
                print(f"  statboard/{stat} {season} {st} all FAILED: {exc}", flush=True)
        if stat == "leaders":
            continue  # no weekly scope
        for st, wk, complete in plan:
            trust = (not rescrape) and complete
            p = out_path(stat, season, st, wk, root)
            try:
                counts[
                    capture(
                        p,
                        lambda s=stat, t=st, w=wk: _fetch(s, season, t, w),
                        key,
                        rescrape=not trust,
                    )
                ] += 1
            except FetchError as exc:
                counts["failed"] += 1
                print(f"  statboard/{stat} {season} {st} wk{wk} FAILED: {exc}", flush=True)
    return {
        "season": season,
        "status": "ok",
        **counts,
        "elapsed": time.monotonic() - t0,
    }
