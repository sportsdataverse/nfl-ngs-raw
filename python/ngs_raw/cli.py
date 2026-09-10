"""Shared CLI for the numbered stage shims: ``-s/-e`` season range, tolerant ``-r``.

Seasons are the STARTING calendar year (2025 = the 2025-26 NFL season), the
NGS API's own convention and nflverse's. The default is the current season via
``sportsdataverse.nfl.utils_date.most_recent_nfl_season`` (rolls over the
Thursday after Labor Day, not on 1 January -- Jan/Feb are still last season).
"""

from __future__ import annotations

import argparse
import json
import sys
from typing import Callable

from ngs_raw.store import str2bool


def current_season() -> int:
    try:
        from sportsdataverse.nfl.utils_date import most_recent_nfl_season

        return int(most_recent_nfl_season())
    except Exception:  # noqa: BLE001 -- offline fallback
        from datetime import date

        today = date.today()
        return today.year if today.month >= 9 else today.year - 1


def build_parser(prog: str, floor: int, rescrape_default: bool) -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog=prog)
    ap.add_argument(
        "-s",
        "--start",
        type=int,
        default=None,
        help=f"first season (default: current; floor {floor})",
    )
    ap.add_argument("-e", "--end", type=int, default=None, help="last season (default: --start)")
    ap.add_argument(
        "-r",
        "--rescrape",
        default=str(rescrape_default),
        help="refetch payloads already valid on disk (tolerant true/false; unknown -> false)",
    )
    ap.add_argument("--root", default=None, help="tree root (default ngs/)")
    ap.add_argument("--limit", type=int, default=0, help="cap per-season items (smoke tests)")
    return ap


def run(
    prog: str,
    floor: int,
    stage: Callable[..., dict],
    *,
    rescrape_default: bool = True,
    argv=None,
) -> int:
    """Parse args, loop seasons, print one JSON summary line per season; rc=1 if any failed."""
    a = build_parser(prog, floor, rescrape_default).parse_args(argv)
    start = a.start if a.start is not None else current_season()
    end = a.end if a.end is not None else start
    if end < start:
        raise SystemExit(f"--end ({end}) is before --start ({start})")
    rescrape = str2bool(a.rescrape)
    rc = 0
    for season in range(start, end + 1):
        kwargs = {"root": a.root, "rescrape": rescrape}
        if a.limit:
            kwargs["limit"] = a.limit
        try:
            summary = stage(season, **kwargs)
        except TypeError:
            kwargs.pop("limit", None)
            summary = stage(season, **kwargs)
        print(f"{prog} {json.dumps(summary)}", flush=True)
        if summary.get("failed") or summary.get("status") == "failed":
            rc = 1
    return rc


def main_for(prog: str, floor: int, stage: Callable[..., dict], *, rescrape_default: bool = True) -> None:
    sys.exit(run(prog, floor, stage, rescrape_default=rescrape_default))
