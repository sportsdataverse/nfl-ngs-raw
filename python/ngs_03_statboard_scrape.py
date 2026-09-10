"""Stage 03 -- statboard. statboard/{passing,rushing,receiving,leaders} per (type, week) + season aggregates.

Thin shim over the tested package (``ngs_raw.statboard``); this file exists so
the stage sequence is readable from a directory listing. Numbers are intended
build order (schedules first: every other stage enumerates from them).

Equivalent to::

    python -m ngs_raw.statboard  (not exposed) -- run this file with -s/-e/-r
"""

from __future__ import annotations

from ngs_raw import TRACKING_FLOOR
from ngs_raw.cli import main_for
from ngs_raw.statboard import scrape_season

if __name__ == "__main__":
    main_for("ngs_03_statboard_scrape", TRACKING_FLOOR, scrape_season, rescrape_default=True)
