"""Stage 06 -- highlights. Weekly highlight list, then tracking + participation per highlight play.

Thin shim over the tested package (``ngs_raw.highlights``); this file exists so
the stage sequence is readable from a directory listing. Needs stage 01's
schedule (weeks and finality come from it).
"""

from __future__ import annotations

from ngs_raw import HIGHLIGHT_FLOOR
from ngs_raw.cli import main_for
from ngs_raw.highlights import scrape_season

if __name__ == "__main__":
    main_for("ngs_06_highlights_scrape", HIGHLIGHT_FLOOR, scrape_season, rescrape_default=False)
