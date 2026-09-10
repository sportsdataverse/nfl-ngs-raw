"""Stage 05 -- gamecenter. gamecenter/overview per FINAL game.

Thin shim over the tested package (``ngs_raw.gamecenter``); this file exists so
the stage sequence is readable from a directory listing. Numbers are intended
build order (schedules first: every other stage enumerates from them).

Equivalent to::

    python -m ngs_raw.gamecenter  (not exposed) -- run this file with -s/-e/-r
"""

from __future__ import annotations

from ngs_raw import GAMECENTER_FLOOR
from ngs_raw.cli import main_for
from ngs_raw.gamecenter import scrape_season

if __name__ == "__main__":
    main_for("ngs_05_gamecenter_scrape", GAMECENTER_FLOOR, scrape_season, rescrape_default=False)
