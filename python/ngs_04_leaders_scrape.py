"""Stage 04 -- leaders. leaders/* expectation + tracking leaderboards per (type, week) + season scopes.

Thin shim over the tested package (``ngs_raw.leaders``); this file exists so
the stage sequence is readable from a directory listing. Numbers are intended
build order (schedules first: every other stage enumerates from them).

Equivalent to::

    python -m ngs_raw.leaders  (not exposed) -- run this file with -s/-e/-r
"""

from __future__ import annotations

from ngs_raw import TRACKING_FLOOR
from ngs_raw.cli import main_for
from ngs_raw.leaders import scrape_season

if __name__ == "__main__":
    main_for("ngs_04_leaders_scrape", TRACKING_FLOOR, scrape_season, rescrape_default=True)
