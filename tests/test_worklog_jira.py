from __future__ import annotations

import unittest
from datetime import date, datetime
from zoneinfo import ZoneInfo

import requests
from jira.exceptions import JIRAError

from jira_worklog.jira_worklogs import JiraWorklogs, JiraWriteError, check_login
from jira_worklog.settings import JiraSettings
from jira_worklog.timesheet import Worklog


def raw_worklog(wid: int, author: str, started: str, seconds: int, comment: str | None = None) -> dict:
    data = {
        "id": str(wid),
        "author": {"name": author},
        "started": started,
        "timeSpentSeconds": seconds,
    }
    if comment is not None:
        data["comment"] = comment
    return data


class FakeJiraClient:
    """Answers the REST v2 paths that JiraWorklogs reads. Records writes."""

    server_url = "https://jira.example"

    def __init__(self, issues: list[dict], worklogs: dict[str, list[dict]], page_size: int = 2):
        self.issues = issues
        self.worklogs = worklogs
        self.page_size = page_size
        self.calls: list[tuple[str, dict]] = []
        self.added: list[dict] = []
        self.fail_with: JIRAError | None = None
        self.picker_sections: list[dict] = []

    def _get_json(self, path: str, params: dict | None = None):
        params = params or {}
        self.calls.append((path, params))
        if path == "myself":
            return {"name": "jdoe", "displayName": "Jane Doe"}
        if path == "search":
            start = params["startAt"]
            page = self.issues[start : start + self.page_size]
            return {"issues": page, "total": len(self.issues), "startAt": start}
        if path.startswith("issue/") and path.endswith("/worklog"):
            key = path.split("/")[1]
            start = params["startAt"]
            logs = self.worklogs.get(key, [])
            return {"worklogs": logs[start : start + self.page_size], "total": len(logs)}
        if path == "issue/picker":
            return {"sections": self.picker_sections}
        raise AssertionError(f"unexpected path {path}")

    def add_worklog(self, issue, timeSpentSeconds=None, comment=None, started=None, **kwargs):
        if self.fail_with:
            raise self.fail_with
        self.added.append(
            {"issue": issue, "seconds": timeSpentSeconds, "comment": comment, "started": started, **kwargs}
        )


def issue(key: str, summary: str) -> dict:
    return {"key": key, "fields": {"summary": summary}}


class TestMonthWorklogs(unittest.TestCase):
    def setUp(self) -> None:
        self.client = FakeJiraClient(
            issues=[issue("A-1", "First"), issue("B-2", "Second"), issue("C-3", "Third")],
            worklogs={
                "A-1": [
                    raw_worklog(1, "jdoe", "2026-10-01T09:00:00.000+0200", 3600, "Daily"),
                    raw_worklog(2, "other", "2026-10-01T10:00:00.000+0200", 7200),
                    raw_worklog(3, "JDoe", "2026-09-30T09:00:00.000+0200", 1800),
                    raw_worklog(4, "jdoe", "2026-10-31T09:00:00.000+0100", 900, "line\nbreak"),
                    raw_worklog(5, "jdoe", "2026-11-01T09:00:00.000+0100", 900),
                ],
                "B-2": [raw_worklog(6, "JDOE", "2026-10-15T09:00:00.000+0200", 5400)],
                "C-3": [],
            },
        )
        self.jira = JiraWorklogs(self.client)

    def test_current_user(self) -> None:
        self.assertEqual(self.jira.current_user(), "jdoe")

    def test_issue_url(self) -> None:
        self.assertEqual(self.jira.issue_url("A-1"), "https://jira.example/browse/A-1")

    def test_keeps_only_own_worklogs_in_the_month(self) -> None:
        logs = self.jira.month_worklogs("jdoe", date(2026, 10, 1), date(2026, 10, 31))
        self.assertEqual(
            logs,
            [
                Worklog("1", "A-1", "First", date(2026, 10, 1), 3600, "Daily"),
                Worklog("4", "A-1", "First", date(2026, 10, 31), 900, "line\nbreak"),
                Worklog("6", "B-2", "Second", date(2026, 10, 15), 5400, ""),
            ],
        )

    def test_search_uses_worklog_jql(self) -> None:
        self.jira.month_worklogs("jdoe", date(2026, 10, 1), date(2026, 10, 31))
        search = [p for path, p in self.client.calls if path == "search"]
        self.assertEqual(
            search[0]["jql"],
            'worklogAuthor = "jdoe" AND worklogDate >= "2026-10-01" '
            'AND worklogDate <= "2026-10-31" ORDER BY key',
        )

    def test_pages_through_issues_and_worklogs(self) -> None:
        self.jira.month_worklogs("jdoe", date(2026, 10, 1), date(2026, 10, 31))
        search_starts = [p["startAt"] for path, p in self.client.calls if path == "search"]
        a1_starts = [p["startAt"] for path, p in self.client.calls if path == "issue/A-1/worklog"]
        self.assertEqual(search_starts, [0, 2])
        self.assertEqual(a1_starts, [0, 2, 4])

    def test_caches_the_month_for_60_seconds(self) -> None:
        now = [1000.0]
        jira = JiraWorklogs(self.client, clock=lambda: now[0])
        jira.month_worklogs("jdoe", date(2026, 10, 1), date(2026, 10, 31))
        calls = len(self.client.calls)
        now[0] += 59
        jira.month_worklogs("jdoe", date(2026, 10, 1), date(2026, 10, 31))
        self.assertEqual(len(self.client.calls), calls)
        now[0] += 2
        jira.month_worklogs("jdoe", date(2026, 10, 1), date(2026, 10, 31))
        self.assertGreater(len(self.client.calls), calls)


class TestAddWorklog(unittest.TestCase):
    def setUp(self) -> None:
        self.client = FakeJiraClient(issues=[issue("A-1", "First")], worklogs={"A-1": []})
        self.jira = JiraWorklogs(self.client)
        self.started = datetime(2026, 10, 22, 14, 45, tzinfo=ZoneInfo("Europe/Berlin"))

    def test_sends_seconds_started_and_comment(self) -> None:
        self.jira.add_worklog("A-1", self.started, 5400, "Tests")
        self.assertEqual(len(self.client.added), 1)
        added = self.client.added[0]
        self.assertEqual(added["issue"], "A-1")
        self.assertEqual(added["seconds"], "5400")
        self.assertEqual(added["started"], self.started)
        self.assertEqual(added["comment"], "Tests")

    def test_empty_comment_is_not_sent(self) -> None:
        self.jira.add_worklog("A-1", self.started, 900, "  ")
        self.assertIsNone(self.client.added[0]["comment"])

    def test_write_clears_the_cache_of_that_month(self) -> None:
        self.jira.month_worklogs("jdoe", date(2026, 10, 1), date(2026, 10, 31))
        calls = len(self.client.calls)
        self.jira.add_worklog("A-1", self.started, 900, "")
        self.jira.month_worklogs("jdoe", date(2026, 10, 1), date(2026, 10, 31))
        self.assertGreater(len(self.client.calls), calls)

    def test_jira_error_becomes_clear_error(self) -> None:
        self.client.fail_with = JIRAError(
            "Sie können keine Arbeit auf einem geschlossenen Vorgang protokollieren.", status_code=400
        )
        with self.assertRaises(JiraWriteError) as ctx:
            self.jira.add_worklog("A-1", self.started, 900, "")
        self.assertEqual(
            str(ctx.exception),
            "Jira refused the worklog: Sie können keine Arbeit auf einem geschlossenen Vorgang protokollieren.",
        )


class TestSearchTickets(unittest.TestCase):
    def test_uses_issue_picker_without_duplicates_and_max_8(self) -> None:
        client = FakeJiraClient(issues=[], worklogs={})
        client.picker_sections = [
            {"issues": [{"key": "A-1", "summaryText": "First"}]},
            {"issues": [{"key": f"B-{i}", "summaryText": f"Ticket {i}"} for i in range(10)]
             + [{"key": "A-1", "summaryText": "First"}]},
        ]
        found = JiraWorklogs(client).search_tickets("first")
        self.assertEqual(found[0], ("A-1", "First"))
        self.assertEqual(len(found), 8)
        self.assertEqual(len({k for k, _ in found}), 8)
        self.assertEqual(client.calls[-1], ("issue/picker", {"query": "first", "showSubTasks": "true"}))

    def test_blank_query_does_not_call_jira(self) -> None:
        client = FakeJiraClient(issues=[], worklogs={})
        self.assertEqual(JiraWorklogs(client).search_tickets("  "), [])
        self.assertEqual(client.calls, [])


if __name__ == "__main__":
    unittest.main()


class TestCheckLogin(unittest.TestCase):
    settings = JiraSettings(url="https://jira.example", personal_token="abc")

    def test_accepted(self) -> None:
        self.assertIsNone(check_login(self.settings, make_client=lambda s, **kw: FakeJiraClient([], {})))

    def test_refused(self) -> None:
        def refuse(settings, **kwargs):
            raise JIRAError(status_code=401, text="Unauthorized")

        self.assertEqual(check_login(self.settings, make_client=refuse), "HTTP 401 Unauthorized")

    def test_no_connection(self) -> None:
        def offline(settings, **kwargs):
            raise requests.ConnectionError("no route to host")

        self.assertIn("no route to host", check_login(self.settings, make_client=offline))
