from __future__ import annotations

import json
import unittest
from datetime import date, datetime
from zoneinfo import ZoneInfo

from fastapi.testclient import TestClient

from jira_worklog.app import create_app
from jira_worklog.jira_worklogs import JiraWriteError
from jira_worklog.timesheet import Worklog

H = 3600


class FakeJiraWorklogs:
    """Stands in for JiraWorklogs. Keeps worklogs in a list and records writes."""

    def __init__(self, worklogs: list[Worklog]):
        self.worklogs = list(worklogs)
        self.added: list[tuple[str, datetime, int, str]] = []
        self.fail_with: str | None = None

    def current_user(self) -> str:
        return "jdoe"

    def issue_url(self, key: str) -> str:
        return f"https://jira.example.com/browse/{key}"

    def month_worklogs(self, user: str, first_day: date, last_day: date) -> list[Worklog]:
        return [w for w in self.worklogs if first_day <= w.day <= last_day]

    def add_worklog(self, key: str, started: datetime, seconds: int, comment: str) -> None:
        if self.fail_with:
            raise JiraWriteError(self.fail_with)
        self.added.append((key, started, seconds, comment))
        summary = next((w.summary for w in self.worklogs if w.key == key), "")
        self.worklogs.append(Worklog(f"new{len(self.added)}", key, summary, started.date(), seconds, comment))

    def search_tickets(self, query: str) -> list[tuple[str, str]]:
        return [("DEMO-1", "Picked ticket")] if query else []


def wl(day: int, key: str, hours: float, comment: str = "") -> Worklog:
    summaries = {"DEMO-975": "Credit check status >300", "DEMO-67": "[TIMEKEEPING] - Agile ceremonies"}
    return Worklog(f"{key}-{day}-{hours}", key, summaries[key], date(2026, 10, day), int(hours * H), comment)


class TestWorklogApp(unittest.TestCase):
    def setUp(self) -> None:
        self.jira = FakeJiraWorklogs(
            [
                wl(22, "DEMO-975", 3),
                wl(22, "DEMO-67", 0.75, "Daily"),
                wl(23, "DEMO-67", 8),
            ]
        )
        self.client = TestClient(create_app(self.jira, today=lambda: date(2026, 10, 27)))

    def test_root_redirects_to_current_month(self) -> None:
        response = self.client.get("/", follow_redirects=False)
        self.assertEqual(response.status_code, 307)
        self.assertEqual(response.headers["location"], "/month/2026-10")

    def test_page_renders_grid(self) -> None:
        response = self.client.get("/month/2026-10")
        self.assertEqual(response.status_code, 200)
        html = response.text
        self.assertIn("Logged work", html)
        self.assertIn("October 2026", html)
        self.assertIn('href="/month/2026-09"', html)
        self.assertIn('href="/month/2026-11"', html)
        self.assertIn('href="https://jira.example.com/browse/DEMO-975"', html)
        self.assertIn("Credit check status &gt;300", html)
        self.assertIn("3:00", html)
        self.assertIn("11:45", html)  # logged in the month
        self.assertIn("152:00", html)  # target: 19 workdays up to 27 Oct
        self.assertIn('id="cell-DEMO-975-2026-10-22"', html)
        self.assertIn("4:15 missing", html)  # day total tooltip on 22 Oct
        self.assertIn('<span class="miss">−4:15</span>', html)  # missing time under the day total
        self.assertIn('<span class="fill" style="width: 37.5%">', html)  # 3:00 = 37.5 % of 8 h
        self.assertIn("/static/htmx.min.js", html)

    def test_invalid_month_is_404(self) -> None:
        self.assertEqual(self.client.get("/month/2026-13").status_code, 404)

    def test_dialog_renders_with_day_panel(self) -> None:
        response = self.client.get("/dialog", params={"key": "DEMO-975", "date": "2026-10-22"})
        self.assertEqual(response.status_code, 200)
        html = response.text
        self.assertIn("Log work", html)
        self.assertIn("Credit check status &gt;300", html)
        self.assertIn('value="2026-10-22"', html)
        self.assertIn("Everything logged on this day", html)
        self.assertIn("Thursday, 22 October", html)
        self.assertIn("3:45 / 8:00", html)
        self.assertIn(" · this ticket", html)
        self.assertIn("Daily", html)
        self.assertIn("Rest of day 4:15", html)
        self.assertIn("4:15 missing on this day", html)
        self.assertIn("Adds a new worklog. Existing worklogs stay.", html)

    def test_dialog_for_a_ticket_without_worklogs_takes_the_summary_param(self) -> None:
        response = self.client.get(
            "/dialog", params={"key": "DEMO-1", "date": "2026-10-27", "summary": "Picked ticket"}
        )
        self.assertIn("Picked ticket", response.text)

    def test_panel_shows_the_new_worklog(self) -> None:
        response = self.client.get(
            "/dialog/panel", params={"key": "DEMO-975", "date": "2026-10-22", "time": "1:30"}
        )
        html = response.text
        self.assertIn("With this worklog: 5:15, 2:45 still missing", html)
        self.assertIn("= 1h 30m", html)
        self.assertIn("Add 1:30", html)
        self.assertIn("new worklog", html)

    def test_panel_shows_an_error_for_bad_time(self) -> None:
        html = self.client.get(
            "/dialog/panel", params={"key": "DEMO-975", "date": "2026-10-22", "time": "abc"}
        ).text
        self.assertIn("Type a time like 1:30, 1.5 or 90m.", html)
        self.assertIn("disabled", html)

    def test_post_adds_exactly_one_worklog_and_returns_the_grid(self) -> None:
        response = self.client.post(
            "/worklog",
            data={"key": "DEMO-975", "date": "2026-10-22", "time": "1:30", "comment": "Tests", "month": "2026-10"},
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(self.jira.added), 1)
        key, started, seconds, comment = self.jira.added[0]
        self.assertEqual(key, "DEMO-975")
        self.assertEqual(seconds, 90 * 60)
        self.assertEqual(comment, "Tests")
        # 09:00 + 3:45 already logged that day
        self.assertEqual(started, datetime(2026, 10, 22, 12, 45, tzinfo=ZoneInfo("Europe/Berlin")))
        self.assertIn('id="grid-area"', response.text)
        self.assertIn("4:30", response.text)
        self.assertIn(
            "Added 1:30 to DEMO-975 on Thursday 22 Oct. New total that day: 4:30.", response.text
        )
        trigger = json.loads(response.headers["HX-Trigger-After-Settle"])
        self.assertEqual(trigger["worklogAdded"]["cell"], "cell-DEMO-975-2026-10-22")

    def test_invalid_time_gives_error_and_no_write(self) -> None:
        for text in ["abc", "0", ""]:
            with self.subTest(text=text):
                response = self.client.post(
                    "/worklog",
                    data={"key": "DEMO-975", "date": "2026-10-22", "time": text, "month": "2026-10"},
                )
                self.assertEqual(self.jira.added, [])
                self.assertEqual(response.headers["HX-Retarget"], "#dlg-error")
                self.assertIn("Type a time like 1:30, 1.5 or 90m.", response.text)

    def test_jira_error_keeps_the_dialog_open(self) -> None:
        self.jira.fail_with = "Jira refused the worklog: Issue is closed."
        response = self.client.post(
            "/worklog",
            data={"key": "DEMO-975", "date": "2026-10-22", "time": "1h", "month": "2026-10"},
        )
        self.assertEqual(response.headers["HX-Retarget"], "#dlg-error")
        self.assertIn("Jira refused the worklog: Issue is closed.", response.text)

    def test_csv_export(self) -> None:
        response = self.client.get("/month/2026-10/export.csv")
        self.assertEqual(response.status_code, 200)
        self.assertIn('filename="worklogs-jdoe-2026-10.csv"', response.headers["content-disposition"])
        self.assertTrue(response.content.startswith("﻿".encode("utf-8")))
        self.assertIn("2026-10-22;DEMO-67;", response.content.decode("utf-8-sig"))

    def test_ticket_search(self) -> None:
        html = self.client.get("/tickets/search", params={"q": "pick"}).text
        self.assertIn("DEMO-1", html)
        self.assertIn("Picked ticket", html)
        self.assertIn("date=2026-10-27", html)


if __name__ == "__main__":
    unittest.main()
