"""Stage 01 -- schedules. league/schedule per season -> json + tidy parquet + master.

Thin shim over the tested package (``ngs_raw.schedule``); this file exists so
the stage sequence is readable from a directory listing. Numbers are intended
build order (schedules first: every other stage enumerates from them).

Equivalent to::

    python -m ngs_raw.schedule  (not exposed) -- run this file with -s/-e/-r
"""

from __future__ import annotations

from ngs_raw import SCHEDULE_FLOOR
from ngs_raw.cli import main_for
from ngs_raw.schedule import scrape_season

if __name__ == "__main__":
    main_for("ngs_01_schedules_scrape", SCHEDULE_FLOOR, scrape_season, rescrape_default=True)
