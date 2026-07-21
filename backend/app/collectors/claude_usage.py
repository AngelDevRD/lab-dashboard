"""Claude Code token/cost usage, sourced from ccusage (github.com/ryoppippi/ccusage).

ccusage already parses Claude Code's local session logs and computes cost/token
totals per its own pricing table, so we shell out to it instead of reimplementing
that parser. Each invocation rescans every local log file, which is slow (multi-
second) and gets slower as history grows — so we only ever ask ccusage for the
*full* history, cache that once, and answer every day-filter (1d/7d/30d/...) by
slicing the cached data in Python. Without this, switching filters would mean a
fresh multi-second ccusage subprocess per click, and that cost would be paid
again on every refresh once this runs on a remote host instead of a dev laptop.
"""

import asyncio
import json
import logging
import shutil
import time
from datetime import date, datetime

from .. import config

logger = logging.getLogger("dashboard")

_NPX = shutil.which("npx") or "npx"
_PERIODS = ("daily", "monthly", "session")
_SUM_FIELDS = ("cacheCreationTokens", "cacheReadTokens", "inputTokens", "outputTokens", "totalCost", "totalTokens")

# period -> (fetched_at, full-history parsed json)
_cache: dict[str, tuple[float, dict]] = {}
# period -> in-flight fetch, so concurrent requests share one ccusage call
# instead of racing separate subprocesses (which flakes under load on Windows).
_inflight: dict[str, asyncio.Task] = {}


async def _run_ccusage(period: str) -> dict:
    args = [_NPX, config.CLAUDE_USAGE_PACKAGE, "claude", period, "--json"]
    proc = await asyncio.create_subprocess_exec(
        *args,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        stdout, stderr = await asyncio.wait_for(
            proc.communicate(), timeout=config.CLAUDE_USAGE_TIMEOUT
        )
    except asyncio.TimeoutError:
        proc.kill()
        await proc.wait()
        raise RuntimeError(f"ccusage timed out after {config.CLAUDE_USAGE_TIMEOUT}s")
    if proc.returncode != 0:
        raise RuntimeError(f"ccusage exited {proc.returncode}: {stderr.decode(errors='replace').strip()}")
    return json.loads(stdout)


async def _fetch_full(period: str) -> dict:
    try:
        return await _run_ccusage(period)
    except Exception:
        # Spawning npx can flake transiently on Windows (e.g. concurrent
        # invocations racing on the npm cache), so retry once before giving up.
        return await _run_ccusage(period)


async def _get_full_report(period: str) -> dict:
    """Full (unfiltered) ccusage report for `period`, cached for CLAUDE_USAGE_CACHE_TTL."""
    cached = _cache.get(period)
    now = time.time()
    if cached and now - cached[0] < config.CLAUDE_USAGE_CACHE_TTL:
        return cached[1]

    # If a fetch for this period is already running (e.g. two browser tabs,
    # or a filter click landing mid-refresh), await that instead of starting
    # a second concurrent ccusage process.
    task = _inflight.get(period)
    if task is None:
        task = asyncio.ensure_future(_fetch_full(period))
        _inflight[period] = task
    try:
        data = await task
    except Exception:
        if cached:
            logger.exception("ccusage refresh failed for %s, serving stale cache", period)
            return cached[1]
        raise
    finally:
        if _inflight.get(period) is task:
            del _inflight[period]
    _cache[period] = (now, data)
    return data


def _entry_date(entry: dict) -> str | None:
    if "date" in entry:
        return entry["date"]
    if "month" in entry:
        return f"{entry['month']}-01"
    if "lastActivity" in entry:
        return entry["lastActivity"][:10]
    return None


def _recompute_totals(entries: list[dict]) -> dict:
    totals = {field: 0 for field in _SUM_FIELDS}
    for entry in entries:
        for field in _SUM_FIELDS:
            totals[field] += entry.get(field, 0)
    return totals


def _filter_report(data: dict, since_date: date) -> dict:
    list_key = next((k for k in ("daily", "monthly", "sessions") if k in data), None)
    if list_key is None:
        return data
    since_iso = since_date.isoformat()
    filtered = [e for e in data[list_key] if (_entry_date(e) or "") >= since_iso]
    return {list_key: filtered, "totals": _recompute_totals(filtered)}


async def get_report(period: str, since: str | None = None) -> dict:
    """ccusage's report for `period` (daily/monthly/session), optionally
    restricted to entries on/after `since` (YYYYMMDD). Always fetches the
    full history from ccusage (cached) and filters in-process, so switching
    between day ranges never re-triggers a ccusage subprocess."""
    if period not in _PERIODS:
        raise ValueError(f"unsupported period: {period!r}, expected one of {_PERIODS}")

    data = await _get_full_report(period)
    if not since:
        return data
    since_date = datetime.strptime(since, "%Y%m%d").date()
    return _filter_report(data, since_date)
