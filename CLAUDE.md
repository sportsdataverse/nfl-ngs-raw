# CLAUDE.md — nfl-ngs-raw

Python scraper for the Next Gen Stats web API (`nextgenstats.nfl.com/api`) that
commits raw JSON to this repo. **Scraping only** (SDV `-raw` contract): no
reshaping, no modeling deps, ever. The sibling `nfl-ngs-data` reads this tree
over `raw.githubusercontent.com` and owns every released dataset.

## Commands (verified)

```sh
uv sync --dev
uv run pytest -q                      # offline; every fetch is injected
uv run ruff check python tests
bash -n scripts/*.sh

bash scripts/daily_ngs_scraper.sh [-s YYYY] [-e YYYY] [-r true|false]   # the entry point
PYTHONPATH=python .venv/bin/python python/ngs_03_statboard_scrape.py -s 2024 -e 2024 -r false
```

Never `uv run` inside a long sweep (it re-syncs the venv mid-run); drivers
source `scripts/_venv.sh` and call `$SDV_PY` by path.

## Conventions

- Seasons are the **starting** year (2025 = 2025-26); default = current via
  `most_recent_nfl_season()` (rolls over after Labor Day, not 1 Jan).
- Commit subject `NGS Raw Update (Start: YYYY End: YYYY)` is load-bearing —
  `nfl_ngs_data_trigger.yml` forwards it and the data repo parses the years.
- Committing raw per-game JSON to git is the pattern, at scale. Do not warn
  about repo size or propose external storage.
- Never add AI tools as commit co-authors.
- Pacing is env-only (`NGS_RAW_SLEEP` / `NGS_RAW_TIMEOUT` / `NGS_RAW_RETRIES`).

## Invariants the tests pin (read before touching a stage)

- **Presence is not validity.** A file on disk is trusted only if it parses AND
  has rows (`store.is_valid`). An empty payload is never written.
- **Finality comes from the schedule**, not from a marker: a week is
  refetched until every game in it is `FINAL`; only then can `-r false` skip
  it. `gamecenter` is requested only for `FINAL` games.
- **Future weeks are never requested** (`schedule.week_plan` filters on first
  kickoff ≤ now) — the API answers `stats: []` for them, and banking that
  would make a presence-based resume skip the real data forever.
- Writes are atomic (tmp + rename); the master schedule is upserted by
  `game_id`, never clobbered.
- `-r` goes through `store.str2bool` (unknown → False). Never `argparse type=bool`.

## Gotchas

- Access is **egress-dependent**: 19/36 routes answer from the project droplet;
  a GitHub macOS runner was denied outright. The droplet cron is the scheduler;
  `scrape_ngs_raw.yml` is dispatch-only on purpose. Don't add a cron to it.
- `leaders/{distance,speed,time}/*` silently answer **week 1** when `week` is
  omitted — always send it (the stage does).
- `statboard/*` POST has no season aggregate (answers `stats: []`); it is
  week-only. `statboard/leaders` has no weekly scope; it is `all`-only.
- One live probe saw a transient **503** on `statboard/leaders`; 503 is in the
  retry set. A persistent non-404 failure raises `FetchError`, is counted as
  `failed`, and turns the stage rc red — never swallowed.
- `download()` raises `NoDataError` on 404; `fetch.get_json` maps that to
  `None` (absent), which is normal for pre-floor seasons.

## Structure

```
python/ngs_raw/{__init__,fetch,store,schedule,statboard,leaders,gamecenter,teams,cli}.py
python/ngs_0{1..5}_*_scrape.py     numbered shims, intended build order
scripts/daily_ngs_scraper.sh       driver (commit + push per season) ; scripts/_venv.sh
ngs/{schedules,teams,statboard,leaders,gamecenter}/   committed raw tree
.github/workflows/{tests,orphan_scripts,scrape_ngs_raw,nfl_ngs_data_trigger}.yml
```

Reference: `sdv-internal-refs/nfl/nextgenstats/` (route surface, access
findings, sample bodies).
