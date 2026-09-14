"""Stage 06 (highlights) offline tests -- every fetch is monkeypatched.

Pins the rules in ngs_raw/highlights.py: complete-list merging, settle-window
refetch, per-play finality, and that a listed highlight which fails is a
failure rather than an absence.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from ngs_raw import highlights as hl
from ngs_raw import schedule
from ngs_raw.fetch import FetchError

DAY = hl.DAY_MS
NOW = 100 * DAY
FINAL = {"phase": "FINAL"}
PRE = {"phase": "PREGAME"}


def _game(gid, wk, iso, score):
    return {
        "season": 2025,
        "seasonType": "REG",
        "week": wk,
        "gameId": gid,
        "gameKey": gid,
        "isoTime": iso,
        "homeTeamId": "0810",
        "visitorTeamId": "1800",
        "score": score,
        "site": {},
    }


def _sched(*games):
    return schedule.tidy(list(games))


def _item(gid, pid):
    return {"gameId": gid, "playId": pid, "season": 2025, "seasonType": "REG", "week": 1}


TRACK = {"homeTrackingData": [{"esbId": "A", "playerTrackingData": [{"x": 1, "y": 2}]}], "awayTrackingData": []}
PART = {"home": [{"gsisId": "00-1"}], "away": []}


class FakeApi:
    """Serves plays/highlights pages and per-play payloads; records every call."""

    def __init__(self, weeks: dict, *, page_size=200, deny=(), empty=()):
        self.weeks, self.page_size, self.deny, self.empty = weeks, page_size, set(deny), set(empty)
        self.calls: list[tuple[str, dict]] = []

    def __call__(self, path, params=None):
        params = dict(params or {})
        self.calls.append((path, params))
        if path == "/plays/highlights":
            rows = self.weeks.get((params["seasonType"], params["week"]), [])
            off, lim = params.get("offset", 0), min(params.get("limit", 20), self.page_size)
            return {"total": len(rows), "highlights": rows[off : off + lim]}
        key = (params["gameId"], params["playId"])
        if key in self.deny:
            raise FetchError(f"{path}: HTTP 403")
        if key in self.empty:
            return {"homeTrackingData": [], "awayTrackingData": []} if "tracking" in path else {"home": [], "away": []}
        return TRACK if "tracking" in path else PART

    def count(self, path):
        return sum(1 for p, _ in self.calls if p == path)


@pytest.fixture()
def api(monkeypatch):
    def install(fake):
        monkeypatch.setattr(hl, "get_json", fake)
        return fake

    return install


def test_week_list_merges_every_page(tmp_path: Path, api):
    rows = [_item(1, p) for p in range(1, 451)]
    fake = api(FakeApi({("REG", 1): rows}, page_size=200))
    env = hl.fetch_week_list(2025, "REG", 1)
    assert env["total"] == 450 and len(env["highlights"]) == 450
    assert fake.count("/plays/highlights") == 3


def test_short_merge_is_a_failure_not_a_smaller_week(api):
    class Dropping(FakeApi):
        def __call__(self, path, params=None):
            body = super().__call__(path, params)
            if path == "/plays/highlights" and params.get("offset"):
                body["highlights"] = []  # a page came back empty mid-walk
            return body

    api(Dropping({("REG", 1): [_item(1, p) for p in range(1, 301)]}, page_size=200))
    with pytest.raises(FetchError, match="merged 200 of total=300"):
        hl.fetch_week_list(2025, "REG", 1)


def test_future_week_is_never_requested(tmp_path: Path, api):
    fake = api(FakeApi({("REG", 1): [_item(1, 10)]}))
    df = _sched(_game(1, 1, NOW - 10 * DAY, FINAL), _game(2, 2, NOW + DAY, PRE))
    hl.scrape_season(2025, root=tmp_path, schedule=df, now_ms=NOW)
    weeks = {p["week"] for path, p in fake.calls if path == "/plays/highlights"}
    assert weeks == {1}


def test_list_refetched_until_settled_then_trusted(tmp_path: Path, api, monkeypatch):
    monkeypatch.setenv("NGS_HIGHLIGHT_SETTLE_DAYS", "7")
    df = _sched(_game(1, 1, NOW - 3 * DAY, FINAL))  # final, but only 3 days ago
    fake = api(FakeApi({("REG", 1): [_item(1, 10)]}))
    hl.scrape_season(2025, root=tmp_path, schedule=df, now_ms=NOW)
    # tagging lagged: a second highlight appears the next day
    fake.weeks[("REG", 1)] = [_item(1, 10), _item(1, 20)]
    out = hl.scrape_season(2025, root=tmp_path, schedule=df, now_ms=NOW + DAY)
    assert out["lists_wrote"] == 1 and out["highlights"] == 2
    assert hl.play_is_valid("tracking", hl.play_path("tracking", 2025, 1, 20, tmp_path))

    # past the settle window the banked list is trusted: no list request at all
    n_list = fake.count("/plays/highlights")
    out = hl.scrape_season(2025, root=tmp_path, schedule=df, now_ms=NOW + 10 * DAY)
    assert out["lists_skipped"] == 1 and fake.count("/plays/highlights") == n_list


def test_incomplete_week_list_is_never_trusted(tmp_path: Path, api):
    df = _sched(_game(1, 1, NOW - 30 * DAY, FINAL), _game(2, 1, NOW - 30 * DAY, PRE))
    fake = api(FakeApi({("REG", 1): [_item(1, 10)]}))
    hl.scrape_season(2025, root=tmp_path, schedule=df, now_ms=NOW)
    hl.scrape_season(2025, root=tmp_path, schedule=df, now_ms=NOW)
    assert fake.count("/plays/highlights") == 2


def test_per_play_payloads_are_final_once_valid(tmp_path: Path, api):
    df = _sched(_game(1, 1, NOW - 30 * DAY, FINAL))
    fake = api(FakeApi({("REG", 1): [_item(1, 10), _item(1, 11)]}))
    out = hl.scrape_season(2025, root=tmp_path, schedule=df, now_ms=NOW)
    assert out["wrote"] == 4 and out["failed"] == 0
    assert hl.read_json_gz(hl.play_path("participation", 2025, 1, 11, tmp_path)) == PART
    per_play = len(fake.calls) - fake.count("/plays/highlights")
    out = hl.scrape_season(2025, root=tmp_path, schedule=df, now_ms=NOW)
    assert out["skipped"] == 4 and len(fake.calls) - fake.count("/plays/highlights") == per_play


def test_listed_highlight_denied_or_empty_is_failure_and_not_banked(tmp_path: Path, api):
    df = _sched(_game(1, 1, NOW - 30 * DAY, FINAL))
    api(FakeApi({("REG", 1): [_item(1, 10), _item(1, 11), _item(1, 12)]}, deny={(1, 11)}, empty={(1, 12)}))
    out = hl.scrape_season(2025, root=tmp_path, schedule=df, now_ms=NOW)
    assert out["wrote"] == 2 and out["failed"] == 4  # 2 kinds x 2 bad plays
    for pid in (11, 12):
        assert not hl.play_path("tracking", 2025, 1, pid, tmp_path).exists()


def test_corrupt_gz_on_disk_is_refetched(tmp_path: Path, api):
    df = _sched(_game(1, 1, NOW - 30 * DAY, FINAL))
    api(FakeApi({("REG", 1): [_item(1, 10)]}))
    p = hl.play_path("tracking", 2025, 1, 10, tmp_path)
    p.parent.mkdir(parents=True)
    p.write_bytes(b"\x1f\x8b truncated")
    assert not hl.play_is_valid("tracking", p)
    hl.scrape_season(2025, root=tmp_path, schedule=df, now_ms=NOW)
    assert hl.play_is_valid("tracking", p)


def test_week_with_no_highlights_is_not_persisted(tmp_path: Path, api):
    df = _sched(_game(1, 1, NOW - 30 * DAY, FINAL))
    api(FakeApi({}))
    out = hl.scrape_season(2025, root=tmp_path, schedule=df, now_ms=NOW)
    assert out["lists_empty"] == 1 and not hl.list_path(2025, "REG", 1, tmp_path).exists()
