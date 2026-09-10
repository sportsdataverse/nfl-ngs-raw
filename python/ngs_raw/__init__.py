"""nfl-ngs-raw -- scrape nextgenstats.nfl.com/api into a committed raw JSON library.

Scraping ONLY (the SDV ``-raw`` contract): no reshaping, no modeling. The sibling
``nfl-ngs-data`` reads this tree over ``raw.githubusercontent.com`` and owns
every released dataset.

Committed tree (every path is a stable contract a consumer builds URLs from)::

    ngs/schedules/json/{season}.json                  league/schedule?season=
    ngs/schedules/parquet/ngs_schedule_{season}.parquet   tidy per-season schedule
    ngs/ngs_schedule_master.parquet                   all seasons, upserted by game_id
    ngs/teams/json/{season}.json                      league/teams?season=
    ngs/statboard/{stat}/{season}/{TYPE}_{week}.json   statboard/{stat}; week 'all' = season aggregate
    ngs/leaders/{family}/{season}/{TYPE}_{week}.json   leaders/* ; week 'all' = season scope
    ngs/gamecenter/{season}/{gameId}.json              gamecenter/overview?gameId=

Season floors, measured live 2026-09-09 (see sdv-internal-refs/nfl/nextgenstats):
``statboard/`` and ``leaders/`` serve nothing before 2016 (the tracking era);
``league/schedule`` reaches 2009; ``gamecenter/overview`` at least 2012. The
scrapers take those floors as defaults but never refuse an earlier season --
an empty payload is simply not persisted.
"""

from __future__ import annotations

BASE = "https://nextgenstats.nfl.com"
API = BASE + "/api"

TREE = "ngs"

STATBOARD_STATS = ("passing", "rushing", "receiving", "leaders")

# leaders/* families -> (route path, envelope list key). Expectation routes have
# both a season scope and a week scope; the tracking leaderboards are week-only
# (a request without `week` silently answers week 1, measured live).
LEADER_FAMILIES: dict[str, tuple[str, str, bool]] = {
    # family: (route suffix, list key, has_season_scope)
    "completion": ("expectation/completion", "completionLeaders", True),
    "ery": ("expectation/ery", "eryLeaders", True),
    "yac": ("expectation/yac", "yacLeaders", True),
    "distance_ballCarrier": ("distance/ballCarrier", "leaders", False),
    "distance_tackle": ("distance/tackle", "leaders", False),
    "speed_ballCarrier": ("speed/ballCarrier", "leaders", False),
    "time_sack": ("time/sack", "leaders", False),
}

SEASON_TYPES = ("PRE", "REG", "POST")

TRACKING_FLOOR = 2016  # statboard/ + leaders/
SCHEDULE_FLOOR = 2009  # league/schedule
GAMECENTER_FLOOR = 2012  # gamecenter/overview (2012 verified; earlier untested)

__all__ = [
    "API",
    "BASE",
    "GAMECENTER_FLOOR",
    "LEADER_FAMILIES",
    "SCHEDULE_FLOOR",
    "SEASON_TYPES",
    "STATBOARD_STATS",
    "TRACKING_FLOOR",
    "TREE",
]
