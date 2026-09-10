"""Stage 02 -- league/teams per season -> ``ngs/teams/json/{season}.json``.

``league/teams`` takes an optional ``season`` (measured live: ``?season=2016``
answers that season's 34 rows incl. the two Pro Bowl pseudo-teams). Banked per
season so the team-id -> abbreviation crosswalk a consumer joins on is the one
that was true THAT season, not today's.
"""

from __future__ import annotations

import time
from pathlib import Path

from ngs_raw.fetch import FetchError, get_json
from ngs_raw.store import capture, tree_root


def out_path(season: int, root=None) -> Path:
    return tree_root(root) / "teams" / "json" / f"{season}.json"


def scrape_season(season: int, *, root: str | Path | None = None, rescrape: bool = True) -> dict:
    t0 = time.monotonic()
    p = out_path(season, root)
    try:
        status = capture(
            p,
            lambda: get_json("/league/teams", {"season": season}),
            None,
            rescrape=rescrape,
        )
    except FetchError as exc:
        print(f"  teams {season} FAILED: {exc}", flush=True)
        status = "failed"
    return {"season": season, "status": status, "elapsed": time.monotonic() - t0}
