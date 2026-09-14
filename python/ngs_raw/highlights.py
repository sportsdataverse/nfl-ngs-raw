"""Stage 06 -- highlight plays: the weekly list, then tracking + participation per play.

Files::

    ngs/highlights/list/{season}/{TYPE}_{week}.json                      plays/highlights (all pages merged)
    ngs/highlights/tracking/{season}/{gameId}_{playId}.json.gz           highlights/tracking/game/play/withBall/min
    ngs/highlights/participation/{season}/{gameId}_{playId}.json.gz      highlights/participation/game/play

WHY HIGHLIGHTS ONLY: the tracking route serves player coordinates for plays NGS
tagged as highlights and 403s every other play; a per-game highlight lookup
(``?gameId=``) is denied too. The weekly list (``season``/``seasonType``/``week``)
is the one anonymous enumeration, so it drives this stage. Measured 2026-09-14
(ClaudeCowork/notes/2026-09-14-ngs-highlight-tracking-and-denied-routes.md):
floor 2018, ~140-160 highlights per REG week, tracking 334 KB raw / 43 KB
gzipped per play, participation ~4 KB gzipped. Both per-play payloads are
gzipped because they are ~95% of this stage's bytes.

Rules, each pinned by a test:

- **The list is enumerated from the data, and must be complete.** Pages are
  merged until ``total`` rows are in hand; a short merge is a FAILURE, never a
  smaller week (a dropped page would otherwise read as fewer highlights).
- **A week's list is refetched until it settles**: while any game in it is not
  FINAL, and for ``NGS_HIGHLIGHT_SETTLE_DAYS`` (default 7) after its last
  kickoff -- highlight tagging lags the game, so a list banked the next morning
  can be missing plays. After that the banked list is trusted.
- **A per-play payload is final once valid** (a finished play does not change):
  valid = parses AND carries player rows. Missing ones are fetched even when
  the week's list is trusted, so a failed night is repaired by the next.
- **A 403 on a LISTED highlight is a failure, not "absent"** -- the list says
  the play is served, so a deny means access changed and must turn the run red.
"""

from __future__ import annotations

import gzip
import json
import os
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import polars as pl

from ngs_raw import HIGHLIGHT_FLOOR
from ngs_raw.fetch import FetchError, get_json
from ngs_raw.schedule import FINAL_PHASES, load_season
from ngs_raw.store import has_rows, read_json, tree_root, write_json_atomic

__all__ = ["HIGHLIGHT_FLOOR", "scrape_season"]

PAGE_LIMIT = 200  # the route honours limit>=200 (measured: one page for a 141-row week)
LIST_KEY = "highlights"
DAY_MS = 86_400_000

#: per-play payload kind -> route
PER_PLAY: dict[str, str] = {
    "tracking": "/highlights/tracking/game/play/withBall/min",
    "participation": "/highlights/participation/game/play",
}


def settle_days() -> float:
    try:
        return max(0.0, float(os.environ.get("NGS_HIGHLIGHT_SETTLE_DAYS", "7")))
    except ValueError:
        return 7.0


# --- paths ---------------------------------------------------------------------


def list_path(season: int, season_type: str, week: int, root=None) -> Path:
    return tree_root(root) / "highlights" / "list" / str(season) / f"{season_type}_{week}.json"


def play_path(kind: str, season: int, game_id: int, play_id: int, root=None) -> Path:
    return tree_root(root) / "highlights" / kind / str(season) / f"{game_id}_{play_id}.json.gz"


# --- gz store --------------------------------------------------------------------


def write_json_gz_atomic(path: Path, payload: Any) -> int:
    """tmp + rename, like :func:`store.write_json_atomic`; returns compressed bytes."""
    path.parent.mkdir(parents=True, exist_ok=True)
    data = gzip.compress(json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8"), 6)
    fd, tmp = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(data)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
    return len(data)


def read_json_gz(path: Path) -> Any | None:
    try:
        return json.loads(gzip.decompress(path.read_bytes()).decode("utf-8"))
    except (OSError, EOFError, json.JSONDecodeError, UnicodeDecodeError):
        return None


def play_has_rows(kind: str, payload: Any) -> bool:
    """Tracking must carry player tracks; participation must carry a roster."""
    if not isinstance(payload, dict):
        return False
    if kind == "tracking":
        sides = ("homeTrackingData", "awayTrackingData")
    else:
        sides = ("home", "away")
    return any(isinstance(payload.get(s), list) and len(payload[s]) > 0 for s in sides)


def play_is_valid(kind: str, path: Path) -> bool:
    return path.exists() and play_has_rows(kind, read_json_gz(path))


# --- list ------------------------------------------------------------------------


def fetch_week_list(season: int, season_type: str, week: int) -> dict | None:
    """All pages of one week merged into a single envelope. ``None`` = absent.

    Raises :class:`FetchError` if the merged rows fall short of ``total`` -- a
    dropped page must not be banked as a smaller week.
    """
    rows: list[dict] = []
    total: int | None = None
    offset = 0
    while True:
        body = get_json(
            "/plays/highlights",
            {"season": season, "seasonType": season_type, "week": week, "limit": PAGE_LIMIT, "offset": offset},
        )
        if not isinstance(body, dict):
            return None if offset == 0 else _short(season, season_type, week, rows, total)
        total = int(body.get("total") or 0)
        page = body.get(LIST_KEY) or []
        rows.extend(page)
        if not page or len(rows) >= total:
            break
        offset += len(page)
    if total and len(rows) < total:
        _short(season, season_type, week, rows, total)
    return {"season": season, "seasonType": season_type, "week": week, "total": total, LIST_KEY: rows}


def _short(season, season_type, week, rows, total):
    raise FetchError(f"plays/highlights {season} {season_type} wk{week}: merged {len(rows)} of total={total}")


def week_windows(df: pl.DataFrame, *, now_ms: int) -> list[tuple[str, int, bool, int]]:
    """``(season_type, week, complete, last_kick_ms)`` for every STARTED week."""
    grouped = (
        df.group_by(["season_type", "week"])
        .agg(
            pl.col("iso_time").min().alias("first_kick"),
            pl.col("iso_time").max().alias("last_kick"),
            pl.col("phase").is_in(list(FINAL_PHASES)).all().alias("complete"),
        )
        .sort(["season_type", "week"])
    )
    order = {"PRE": 0, "REG": 1, "POST": 2}
    out = []
    for st, wk, first, last, complete in grouped.iter_rows():
        if st not in order or wk is None or first is None or first > now_ms:
            continue
        out.append((st, int(wk), bool(complete), int(last)))
    return sorted(out, key=lambda r: (order[r[0]], r[1]))


def list_is_settled(complete: bool, last_kick_ms: int, *, now_ms: int) -> bool:
    return complete and (now_ms - last_kick_ms) >= settle_days() * DAY_MS


# --- stage -----------------------------------------------------------------------


def scrape_season(
    season: int,
    *,
    root: str | Path | None = None,
    rescrape: bool = False,
    limit: int = 0,
    schedule: pl.DataFrame | None = None,
    now_ms: int | None = None,
) -> dict:
    t0 = time.monotonic()
    now_ms = now_ms if now_ms is not None else int(datetime.now(timezone.utc).timestamp() * 1000)
    counts = {
        "weeks": 0,
        "lists_wrote": 0,
        "lists_skipped": 0,
        "lists_empty": 0,
        "highlights": 0,
        "wrote": 0,
        "skipped": 0,
        "failed": 0,
    }
    df = schedule if schedule is not None else load_season(season, root=root)
    if df is None or df.height == 0:
        return {"season": season, "status": "no-schedule", **counts, "elapsed": 0.0}

    plays: list[tuple[int, int]] = []
    for st, wk, complete, last_kick in week_windows(df, now_ms=now_ms):
        counts["weeks"] += 1
        lp = list_path(season, st, wk, root)
        banked = read_json(lp)
        if not rescrape and has_rows(banked, LIST_KEY) and list_is_settled(complete, last_kick, now_ms=now_ms):
            envelope = banked
            counts["lists_skipped"] += 1
        else:
            try:
                envelope = fetch_week_list(season, st, wk)
            except FetchError as exc:
                counts["failed"] += 1
                print(f"  highlights list {season} {st} wk{wk} FAILED: {exc}", flush=True)
                envelope = banked  # still repair per-play gaps from the last good list
            else:
                if has_rows(envelope, LIST_KEY):
                    write_json_atomic(lp, envelope)
                    counts["lists_wrote"] += 1
                else:
                    counts["lists_empty"] += 1  # a week with no highlights is not persisted
        for h in (envelope or {}).get(LIST_KEY) or []:
            gid, pid = h.get("gameId"), h.get("playId")
            if gid is not None and pid is not None:
                plays.append((int(gid), int(pid)))

    plays = list(dict.fromkeys(plays))
    counts["highlights"] = len(plays)
    if limit:
        plays = plays[:limit]
    for i, (gid, pid) in enumerate(plays, 1):
        for kind, route in PER_PLAY.items():
            p = play_path(kind, season, gid, pid, root)
            if play_is_valid(kind, p):  # a finished play does not change: never refetched
                counts["skipped"] += 1
                continue
            try:
                body = get_json(route, {"gameId": gid, "playId": pid})
            except FetchError as exc:
                counts["failed"] += 1
                print(f"  highlight {kind} {season} {gid}/{pid} FAILED: {exc}", flush=True)
                continue
            if not play_has_rows(kind, body):
                # listed as a highlight but served nothing: count it, never bank it
                counts["failed"] += 1
                print(f"  highlight {kind} {season} {gid}/{pid} FAILED: listed but no player rows", flush=True)
                continue
            write_json_gz_atomic(p, body)
            counts["wrote"] += 1
        if i % 250 == 0:
            print(f"  highlights {season}: {i}/{len(plays)} {counts}", flush=True)
    return {"season": season, "status": "ok", **counts, "elapsed": time.monotonic() - t0}
