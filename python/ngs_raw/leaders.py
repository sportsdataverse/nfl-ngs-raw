"""Stage 04 -- leaders/* (the NGS-distinctive tracking + expectation leaderboards).

Files: ``ngs/leaders/{family}/{season}/{TYPE}_{week}.json``. Families and
scopes are declared in ``ngs_raw.LEADER_FAMILIES``: the three expectation
leaderboards (completion / ERY / YAC) carry a season scope (``all``) and a
week scope; the four tracking leaderboards (distance, speed, time-to-sack)
are week-only -- measured live, a request without ``week`` silently answers
week 1, so ``week`` is always sent.

Every leaderboard is a top-20 list of ``{play, leader}`` records; a play-level
join key (``play.gameId``/``play.playId``) rides along on each row.
"""

from __future__ import annotations

import time
from pathlib import Path

import polars as pl

from ngs_raw import LEADER_FAMILIES
from ngs_raw.fetch import FetchError, get_json
from ngs_raw.schedule import (
    load_season,
    season_type_complete,
    season_type_started,
    week_plan,
)
from ngs_raw.store import capture, tree_root


def out_path(family: str, season: int, season_type: str, week: int | str, root=None) -> Path:
    return tree_root(root) / "leaders" / family / str(season) / f"{season_type}_{week}.json"


def _fetch(route: str, scope: str, season: int, season_type: str, week: int | None):
    params = {"season": season, "seasonType": season_type}
    if week is not None:
        params["week"] = week
    return get_json(f"/leaders/{route}/{scope}" if scope else f"/leaders/{route}", params)


def scrape_season(
    season: int,
    *,
    root: str | Path | None = None,
    rescrape: bool = True,
    families: tuple[str, ...] = tuple(LEADER_FAMILIES),
    schedule: pl.DataFrame | None = None,
) -> dict:
    t0 = time.monotonic()
    df = schedule if schedule is not None else load_season(season, root=root)
    counts = {"wrote": 0, "skipped": 0, "empty": 0, "absent": 0, "failed": 0}
    if df is None or df.height == 0:
        return {"season": season, "status": "no-schedule", **counts, "elapsed": 0.0}
    plan = week_plan(df)
    for fam in families:
        route, key, has_season_scope = LEADER_FAMILIES[fam]
        if has_season_scope:
            for st in ("PRE", "REG", "POST"):
                if not season_type_started(df, st):
                    continue
                trust = (not rescrape) and season_type_complete(df, st)
                p = out_path(fam, season, st, "all", root)
                try:
                    counts[
                        capture(
                            p,
                            lambda r=route, t=st: _fetch(r, "season", season, t, None),
                            key,
                            rescrape=not trust,
                        )
                    ] += 1
                except FetchError as exc:
                    counts["failed"] += 1
                    print(f"  leaders/{fam} {season} {st} all FAILED: {exc}", flush=True)
        scope = "week" if has_season_scope else ""
        for st, wk, complete in plan:
            trust = (not rescrape) and complete
            p = out_path(fam, season, st, wk, root)
            try:
                counts[
                    capture(
                        p,
                        lambda r=route, s=scope, t=st, w=wk: _fetch(r, s, season, t, w),
                        key,
                        rescrape=not trust,
                    )
                ] += 1
            except FetchError as exc:
                counts["failed"] += 1
                print(f"  leaders/{fam} {season} {st} wk{wk} FAILED: {exc}", flush=True)
    return {
        "season": season,
        "status": "ok",
        **counts,
        "elapsed": time.monotonic() - t0,
    }
