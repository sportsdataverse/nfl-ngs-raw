"""Stage 02 -- teams. league/teams per season.

Thin shim over the tested package (``ngs_raw.teams``); this file exists so
the stage sequence is readable from a directory listing. Numbers are intended
build order (schedules first: every other stage enumerates from them).

Equivalent to::

    python -m ngs_raw.teams  (not exposed) -- run this file with -s/-e/-r
"""

from __future__ import annotations

from ngs_raw import SCHEDULE_FLOOR
from ngs_raw.cli import main_for
from ngs_raw.teams import scrape_season

if __name__ == "__main__":
    main_for("ngs_02_teams_scrape", SCHEDULE_FLOOR, scrape_season, rescrape_default=True)
