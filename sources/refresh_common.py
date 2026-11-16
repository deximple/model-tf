#!/usr/bin/env python3
"""Shared helpers for the refresh collectors (stdlib only).

- ``fetch_url``: HTTP GET with retry and exponential backoff so a transient
  5xx / 429 / timeout from one documentation site does not kill a whole run.
- ``option_value``: tiny ``--flag VALUE`` reader (the collectors keep their
  historical ``--touch`` style argument handling).
- ``write_status``: per-source health report consumed by
  ``.github/workflows/refresh.yml`` to open or close the refresh alert issue.

Self-test hook: when ``MODEL_TF_SIMULATE_FAIL_HOSTS`` (comma-separated host
names) is set, every fetch to those hosts fails with a retryable error, so the
retry, per-source isolation, carry-forward, and alert paths can be exercised
live by ``.github/workflows/selftest.yml``. Production runs never set it.
"""

import http.client
import json
import os
import re
import ssl
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, Callable

USER_AGENT = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) model-tf-refresh/1.0"

# HTTP status codes worth retrying; any other 4xx is a permanent failure.
RETRYABLE_HTTP = {408, 425, 429, 500, 502, 503, 504}

SIMULATE_FAIL_ENV = "MODEL_TF_SIMULATE_FAIL_HOSTS"


def _simulated_outage(url: str) -> bool:
    hosts = {h.strip().lower() for h in os.environ.get(SIMULATE_FAIL_ENV, "").split(",") if h.strip()}
    return bool(hosts) and (urllib.parse.urlparse(url).hostname or "").lower() in hosts


def _is_retryable(exc: BaseException) -> bool:
    if isinstance(exc, urllib.error.HTTPError):
        return exc.code in RETRYABLE_HTTP
    reason = exc.reason if isinstance(exc, urllib.error.URLError) else exc
    if isinstance(reason, (ssl.SSLCertVerificationError, ssl.CertificateError)):
        return False  # a bad certificate will not fix itself within seconds
    # URLError (DNS, connection reset, TLS), socket timeouts, truncated or
    # malformed responses (http.client.IncompleteRead, RemoteDisconnected).
    return isinstance(exc, (OSError, http.client.HTTPException))


def fetch_url(
    url: str,
    timeout: float = 45,
    attempts: int = 3,
    backoff: float = 3.0,
    sleep: Callable[[float], None] = time.sleep,
    opener: Callable[..., Any] = urllib.request.urlopen,
) -> str:
    """GET ``url`` and return the decoded body, retrying transient failures."""
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    last_exc: BaseException | None = None
    for attempt in range(1, attempts + 1):
        try:
            if _simulated_outage(url):
                raise urllib.error.URLError(f"simulated outage ({SIMULATE_FAIL_ENV})")
            with opener(req, timeout=timeout) as resp:
                return resp.read().decode("utf-8", errors="replace")
        except Exception as exc:  # noqa: BLE001 - re-raised below when permanent
            last_exc = exc
            if attempt >= attempts or not _is_retryable(exc):
                raise
            sleep(backoff * (2 ** (attempt - 1)))
    raise RuntimeError(f"unreachable: {last_exc}")  # pragma: no cover


def option_value(args: list[str], flag: str) -> str | None:
    """Return the value following ``flag`` (``--flag VALUE`` or ``--flag=VALUE``)."""
    for i, arg in enumerate(args):
        if arg == flag and i + 1 < len(args):
            return args[i + 1]
        if arg.startswith(flag + "="):
            return arg.split("=", 1)[1]
    return None


def describe_error(exc: BaseException) -> str:
    """One-line, bounded error text (safe for markdown tables and annotations)."""
    text = re.sub(r"\s+", " ", f"{type(exc).__name__}: {exc}").strip()
    return text if len(text) <= 300 else text[:297] + "..."


def write_status(
    path: str | None,
    collector: str,
    status: dict[str, str],
    failed_urls: set[str] | None = None,
) -> None:
    """Write ``{"collector", "sources": {label: "ok" | "error: ..."}, "failed_urls"}``.

    ``failed_urls`` lists evidence source URLs that were carried forward rather
    than re-verified this run; the tier gate must not count them as confirmation.
    """
    if not path:
        return
    payload = {
        "collector": collector,
        "sources": status,
        "failed_urls": sorted(failed_urls or ()),
    }
    Path(path).write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def read_failed_urls(path: str | None) -> set[str]:
    """Inverse of write_status's failed_urls; a missing/unreadable file means none."""
    if not path:
        return set()
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return set()
    return set(data.get("failed_urls") or [])
