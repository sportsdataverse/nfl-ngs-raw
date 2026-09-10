"""HTTP contract for nextgenstats.nfl.com/api -- one gateway, env-tuned pacing.

The API takes NO auth (measured: a real browser sends no Authorization header).
The entire contract is a ``Referer`` on the nextgenstats.nfl.com origin plus a
browser User-Agent. Access is egress-dependent: 19 of 36 routes answer from
this project's droplet, ``live/*`` and the tracking-coordinate routes do not
(sdv-internal-refs/nfl/nextgenstats/ENDPOINTS.md). None of the blocked routes
are scraped here.

Pacing is env-only, never hardcoded (SDV scrape convention):
    NGS_RAW_SLEEP    seconds slept after EVERY attempt, success or not (default 0.25)
    NGS_RAW_TIMEOUT  per-request timeout in seconds (default 30)
    NGS_RAW_RETRIES  retries on transient statuses (default 4); statboard/leaders
                     threw a one-off 503 on a live probe, so 503 is retryable.
"""

from __future__ import annotations

import json
import os
import time
from typing import Any

from sportsdataverse.dl_utils import download
from sportsdataverse.errors import NoDataError

from ngs_raw import API, BASE

_UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/152.0.0.0 Safari/537.36"
_RETRYABLE = {408, 429, 500, 502, 503, 504}


class FetchError(RuntimeError):
    """A non-404 failure after retries -- the caller counts it, never swallows it."""


def headers(referer_path: str = "/stats/passing/2025/REG/all") -> dict[str, str]:
    return {
        "Referer": BASE + referer_path,
        "User-Agent": _UA,
        "Accept": "application/json, text/plain, */*",
    }


def sleep_s() -> float:
    try:
        return max(0.0, float(os.environ.get("NGS_RAW_SLEEP", "0.25")))
    except ValueError:
        return 0.25


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, default))
    except ValueError:
        return default


def get_json(path: str, params: dict[str, Any] | None = None) -> Any | None:
    """GET ``{API}{path}`` -> parsed JSON; ``None`` when the resource is absent.

    Absent means: a 404, or an HTTP 200 whose body is empty. Both are how this
    API says "nothing here" (it sends no error envelope), and neither is an
    error. Anything else that survives the retries raises :class:`FetchError`.

    Every attempt is paced with ``NGS_RAW_SLEEP`` in a ``finally`` -- pacing
    only after success would hammer the host hardest exactly when it is
    already failing.
    """
    clean = {k: v for k, v in (params or {}).items() if v is not None}
    try:
        try:
            resp = download(
                url=API + path,
                params=clean,
                headers=headers(),
                timeout=_env_int("NGS_RAW_TIMEOUT", 30),
                num_retries=_env_int("NGS_RAW_RETRIES", 4),
                retry_statuses=_RETRYABLE,
            )
        except NoDataError:
            return None
        status = getattr(resp, "status_code", None)
        if status == 404:
            return None
        if status != 200:
            raise FetchError(f"{path} {clean}: HTTP {status}")
        text = (getattr(resp, "text", "") or "").strip()
        if not text:
            return None
        try:
            return json.loads(text)
        except json.JSONDecodeError as exc:
            raise FetchError(f"{path} {clean}: non-JSON 200 body ({exc})") from exc
    finally:
        s = sleep_s()
        if s:
            time.sleep(s)
