"""On-disk contract: atomic writes, validity (not presence) checks, tolerant flags.

Presence is not validity: a file existing on disk must still be rejected if it
is empty or unparseable, or a resume skips a hole forever (3,347 empty ``{}``
payloads once blocked a refetch that way). And an empty payload is never
persisted -- "the API answered" is not "there is data".
"""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any, Callable

from ngs_raw import TREE

_TRUE = {"1", "true", "t", "yes", "y", "on"}


def str2bool(value: Any) -> bool:
    """Tolerant boolean flag parser: unknown -> False, never raises.

    ``argparse type=bool`` is a bug (``bool("false") is True``); a cron typo
    must not trigger a full re-scrape.
    """
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in _TRUE


def tree_root(root: str | Path | None = None) -> Path:
    return Path(root) if root else Path(TREE)


def write_json_atomic(path: Path, payload: Any) -> int:
    """Write ``payload`` as JSON via tmp+rename; return bytes written.

    Never leaves a half-written file behind: an interrupted run must not
    produce something that reads as "captured".
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    data = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
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


def read_json(path: Path) -> Any | None:
    """Parsed JSON or ``None`` if the file is missing/unreadable/unparseable."""
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, UnicodeDecodeError):
        return None


def has_rows(payload: Any, list_key: str | None) -> bool:
    """The finality/validity test derived from the DATA, not from a marker.

    ``list_key`` names the envelope's record list (``stats``, ``leaders``,
    ...); ``None`` means the payload itself is the list (schedule, teams) or a
    keyed object that only needs to be non-empty (gamecenter).
    """
    if payload is None:
        return False
    if list_key is None:
        if isinstance(payload, list):
            return len(payload) > 0
        return isinstance(payload, dict) and len(payload) > 0
    if not isinstance(payload, dict):
        return False
    rows = payload.get(list_key)
    return isinstance(rows, list) and len(rows) > 0


def is_valid(path: Path, list_key: str | None) -> bool:
    return path.exists() and has_rows(read_json(path), list_key)


def capture(
    path: Path,
    fetch: Callable[[], Any | None],
    list_key: str | None,
    *,
    rescrape: bool,
) -> str:
    """Resume-aware fetch-and-bank of one payload. Returns one of
    ``skipped`` (valid on disk, not rescraping), ``wrote``, ``empty`` (the API
    answered with no rows -- nothing persisted), ``absent`` (404/empty body).
    """
    if not rescrape and is_valid(path, list_key):
        return "skipped"
    payload = fetch()
    if payload is None:
        return "absent"
    if not has_rows(payload, list_key):
        return "empty"
    write_json_atomic(path, payload)
    return "wrote"
