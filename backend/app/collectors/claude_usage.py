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

from pydantic import BaseModel

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


class ClaudeUsagePush(BaseModel):
    """Payload a remote machine POSTs to /api/claude-usage/report.

    `reports` mirrors ccusage's own --json output per period (same shape
    _run_ccusage returns), so the existing _filter_report/_recompute_totals
    logic below works unchanged on pushed data.
    """

    source: str
    generated_at: str
    reports: dict[str, dict]
    # ~/.claude/projects folder name -> {"cwd": real path, "project": identity}.
    # Optional so pushes from reporters predating it are still accepted.
    projects: dict[str, dict] | None = None


# In-memory copy of the last received push, so repeated GETs don't hit disk.
# None means "nothing pushed yet, or not loaded from disk this process".
_pushed: dict | None = None
_pushed_loaded_from_disk = False


def _load_pushed_from_disk() -> dict | None:
    path = config.CLAUDE_USAGE_REPORT_FILE
    if not path.exists():
        return None
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        logger.exception("Failed to read pushed claude-usage report from %s", path)
        return None


def save_push(push: ClaudeUsagePush) -> None:
    global _pushed, _pushed_loaded_from_disk
    path = config.CLAUDE_USAGE_REPORT_FILE
    path.parent.mkdir(parents=True, exist_ok=True)
    data = {**push.model_dump(), "received_at": time.time()}
    tmp = path.with_suffix(".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False)
    tmp.replace(path)
    _pushed = data
    _pushed_loaded_from_disk = True


def _get_pushed() -> dict | None:
    global _pushed, _pushed_loaded_from_disk
    if not _pushed_loaded_from_disk:
        _pushed = _load_pushed_from_disk()
        _pushed_loaded_from_disk = True
    return _pushed


def push_status() -> dict | None:
    """Metadata about the last received push, for the frontend's staleness badge."""
    data = _get_pushed()
    if data is None:
        return None
    age = time.time() - data["received_at"]
    return {
        "source": data.get("source"),
        "generated_at": data.get("generated_at"),
        "received_at": data.get("received_at"),
        "stale": age > config.CLAUDE_USAGE_STALE_SEC,
    }


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
    """Report for `period` (daily/monthly/session), optionally restricted to
    entries on/after `since` (YYYYMMDD). Prefers the last report pushed by a
    remote machine (see ClaudeUsagePush) over running ccusage locally — ccusage
    only sees Claude Code sessions that ran on *this* host, which is wrong for
    a server that just monitors other machines. Falls back to a local ccusage
    subprocess when nothing has ever been pushed, so running the backend
    directly on a dev machine still works with no extra setup."""
    if period not in _PERIODS:
        raise ValueError(f"unsupported period: {period!r}, expected one of {_PERIODS}")

    pushed = _get_pushed()
    if pushed is not None:
        data = pushed.get("reports", {}).get(period, {})
    else:
        data = await _get_full_report(period)
    if not since:
        return data
    since_date = datetime.strptime(since, "%Y%m%d").date()
    return _filter_report(data, since_date)


async def get_projects(since: str | None = None) -> dict:
    """Sessions grouped by project identity, so one project that lived in
    several folders (each its own ~/.claude/projects entry) shows as one row.
    Folders the reporter couldn't map (or local ccusage fallback, which has no
    map) stay as their own project keyed by folder name."""
    sessions = (await get_report("session", since=since)).get("sessions", [])
    pushed = _get_pushed()
    folder_map = (pushed or {}).get("projects") or {}

    groups: dict[str, dict] = {}
    for s in sessions:
        folder = s.get("projectPath") or s.get("sessionId", "")
        info = folder_map.get(folder) or {}
        key = info.get("project") or folder
        # Unmapped folder names are encoded paths; their last "-" segment is
        # the best readable guess (mapped names are used as-is).
        name = info.get("project") or folder.split("-")[-1] or folder
        g = groups.setdefault(key, {"project": key, "name": name, "folders": set(), "sessions": []})
        g["folders"].add(folder)
        g["sessions"].append({**s, "cwd": info.get("cwd")})

    projects = []
    for g in groups.values():
        entries = sorted(g["sessions"], key=lambda e: e.get("totalCost", 0), reverse=True)
        projects.append({
            "project": g["project"],
            "name": g["name"],
            **_recompute_totals(entries),
            "sessionCount": len(entries),
            "folderCount": len(g["folders"]),
            "firstActivity": min(e.get("firstActivity", "") for e in entries),
            "lastActivity": max(e.get("lastActivity", "") for e in entries),
            "sessions": entries,
        })
    projects.sort(key=lambda p: p["totalCost"], reverse=True)
    return {"projects": projects}
