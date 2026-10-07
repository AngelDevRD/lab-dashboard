import asyncio

from app.collectors import claude_usage


def _session(folder, sid, tokens, cost, last):
    return {
        "projectPath": folder,
        "sessionId": sid,
        "inputTokens": 1,
        "outputTokens": 2,
        "cacheCreationTokens": 3,
        "cacheReadTokens": tokens - 6,
        "totalTokens": tokens,
        "totalCost": cost,
        "firstActivity": f"{last}T00:00:00Z",
        "lastActivity": f"{last}T01:00:00Z",
    }


SESSIONS = {
    "sessions": [
        _session("C--old-nexfit", "a", 100, 1.0, "2026-08-01"),
        _session("C--new-nexfit", "b", 50, 3.0, "2026-09-01"),
        _session("C--Users-x-lab-dashboard", "c", 10, 0.5, "2026-09-02"),
    ]
}


def _set_push(monkeypatch, projects):
    push = {"reports": {"session": SESSIONS}, "received_at": 0}
    if projects is not None:
        push["projects"] = projects
    monkeypatch.setattr(claude_usage, "_get_pushed", lambda: push)


def test_folders_with_same_identity_are_one_project(monkeypatch):
    _set_push(monkeypatch, {
        "C--old-nexfit": {"cwd": "C:\\old\\nexfit", "project": "nexfit"},
        "C--new-nexfit": {"cwd": "C:\\new\\nexfit", "project": "nexfit"},
    })
    projects = asyncio.run(claude_usage.get_projects())["projects"]

    nexfit = projects[0]
    assert nexfit["name"] == "nexfit"
    assert nexfit["folderCount"] == 2
    assert nexfit["sessionCount"] == 2
    assert nexfit["totalTokens"] == 150
    assert nexfit["totalCost"] == 4.0
    assert nexfit["firstActivity"].startswith("2026-08-01")
    assert nexfit["lastActivity"].startswith("2026-09-01")
    assert [s["sessionId"] for s in nexfit["sessions"]] == ["b", "a"]
    assert nexfit["sessions"][0]["cwd"] == "C:\\new\\nexfit"
    # Unmapped folder stays its own project.
    assert projects[1]["project"] == "C--Users-x-lab-dashboard"
    assert projects[1]["folderCount"] == 1


def test_push_without_map_groups_per_folder(monkeypatch):
    _set_push(monkeypatch, None)
    projects = asyncio.run(claude_usage.get_projects())["projects"]
    assert len(projects) == 3
    assert {p["name"] for p in projects} == {"nexfit", "dashboard"}


def test_days_filter_applies_before_grouping(monkeypatch):
    _set_push(monkeypatch, {
        "C--old-nexfit": {"cwd": "C:\\old\\nexfit", "project": "nexfit"},
        "C--new-nexfit": {"cwd": "C:\\new\\nexfit", "project": "nexfit"},
    })
    projects = asyncio.run(claude_usage.get_projects(since="20260815"))["projects"]
    nexfit = next(p for p in projects if p["project"] == "nexfit")
    assert nexfit["folderCount"] == 1
    assert nexfit["totalTokens"] == 50


def test_push_model_accepts_old_payload_without_projects():
    push = claude_usage.ClaudeUsagePush(source="pc", generated_at="x", reports={})
    assert push.projects is None
