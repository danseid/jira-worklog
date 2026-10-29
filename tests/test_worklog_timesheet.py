from __future__ import annotations

import csv
import io
import unittest
from datetime import date, datetime
from zoneinfo import ZoneInfo

from jira_worklog.timesheet import (
    GroupTotal,
    Worklog,
    added_message,
    bar_percent,
    build_month,
    csv_bytes,
    day_after_text,
    day_groups,
    format_hm,
    format_hmm,
    group_of,
    new_worklog_start,
    parse_duration,
)

H = 3600


def wl(day: int, key: str, hours: float, comment: str = "", month: int = 10) -> Worklog:
    return Worklog(
        id=f"{key}-{day}-{hours}",
        key=key,
        summary=f"Summary of {key}",
        day=date(2026, month, day),
        seconds=int(hours * H),
        comment=comment,
    )


class TestParseDuration(unittest.TestCase):
    def test_accepts_all_formats(self) -> None:
        cases = {
            "1:30": 90 * 60,
            "0:45": 45 * 60,
            "1.5": 90 * 60,
            "1,5": 90 * 60,
            "2": 2 * H,
            "1h 30m": 90 * 60,
            "1h30m": 90 * 60,
            "1h": H,
            "1.5h": 90 * 60,
            "90m": 90 * 60,
            " 15m ": 15 * 60,
            "1H 30M": 90 * 60,
        }
        for text, seconds in cases.items():
            with self.subTest(text=text):
                self.assertEqual(parse_duration(text), seconds)

    def test_invalid_text_gives_none(self) -> None:
        for text in ["abc", "1:75", "1:3:0", "h", "m", "-1", "1.5.5", "1 30"]:
            with self.subTest(text=text):
                self.assertIsNone(parse_duration(text))

    def test_zero_and_empty_give_zero(self) -> None:
        self.assertEqual(parse_duration("0"), 0)
        self.assertEqual(parse_duration("0:00"), 0)
        self.assertEqual(parse_duration(""), 0)
        self.assertEqual(parse_duration("   "), 0)


class TestFormat(unittest.TestCase):
    def test_format_hmm(self) -> None:
        self.assertEqual(format_hmm(0), "0:00")
        self.assertEqual(format_hmm(90 * 60), "1:30")
        self.assertEqual(format_hmm(8 * H + 5 * 60), "8:05")
        self.assertEqual(format_hmm(25 * H), "25:00")
        self.assertEqual(format_hmm(89), "0:01")

    def test_format_hm(self) -> None:
        self.assertEqual(format_hm(90 * 60), "1h 30m")
        self.assertEqual(format_hm(2 * H), "2h")
        self.assertEqual(format_hm(15 * 60), "15m")
        self.assertEqual(format_hm(0), "0m")


class TestBuildMonth(unittest.TestCase):
    # October 2026: 1 Oct is a Thursday. 3/4 Oct are a weekend. 22 workdays.
    def setUp(self) -> None:
        self.logs = [
            wl(1, "DEMO-67", 0.75, "Daily"),
            wl(1, "DEMO-923", 4),
            wl(1, "DEMO-923", 3.25),  # 1 Oct: 8:00 in total
            wl(2, "DEMO-923", 5),  # 2 Oct: 5:00, 3:00 missing
            wl(3, "ABC-1534", 2),  # Saturday
        ]

    def test_rows_sorted_by_key_with_totals(self) -> None:
        month = build_month(self.logs, 2026, 10, today=date(2026, 10, 5))
        self.assertEqual([r.key for r in month.rows], ["ABC-1534", "DEMO-67", "DEMO-923"])
        row = month.rows[2]
        self.assertEqual(row.summary, "Summary of DEMO-923")
        self.assertEqual(row.total, int(12.25 * H))
        self.assertEqual(row.seconds_on(date(2026, 10, 1)), int(7.25 * H))
        self.assertEqual(row.seconds_on(date(2026, 10, 9)), 0)
        self.assertEqual(len(row.worklogs_on(date(2026, 10, 1))), 2)

    def test_days_and_day_totals(self) -> None:
        month = build_month(self.logs, 2026, 10, today=date(2026, 10, 5))
        self.assertEqual(len(month.days), 31)
        self.assertEqual(month.days[0], date(2026, 10, 1))
        self.assertEqual(month.day_total(date(2026, 10, 1)), 8 * H)
        self.assertEqual(month.day_total(date(2026, 10, 2)), 5 * H)
        self.assertEqual(month.day_total(date(2026, 10, 3)), 2 * H)
        self.assertEqual(month.day_total(date(2026, 10, 9)), 0)
        self.assertEqual(month.logged, 15 * H)

    def test_target_and_missing_until_today(self) -> None:
        # Workdays up to Mon 5 Oct: 1, 2, 5 -> 24 h. Missing: 2 Oct 3 h + 5 Oct 8 h.
        month = build_month(self.logs, 2026, 10, today=date(2026, 10, 5))
        self.assertEqual(month.target, 24 * H)
        self.assertEqual(month.missing, 11 * H)
        self.assertEqual(month.missing_on(date(2026, 10, 2)), 3 * H)
        self.assertEqual(month.missing_on(date(2026, 10, 1)), 0)

    def test_weekend_and_future_days_are_not_missing(self) -> None:
        month = build_month(self.logs, 2026, 10, today=date(2026, 10, 5))
        self.assertEqual(month.missing_on(date(2026, 10, 4)), 0)  # Sunday
        self.assertEqual(month.missing_on(date(2026, 10, 6)), 0)  # future workday

    def test_past_month_counts_all_workdays(self) -> None:
        month = build_month([], 2026, 10, today=date(2026, 11, 3))
        self.assertEqual(month.target, 22 * 8 * H)
        self.assertEqual(month.missing, 22 * 8 * H)

    def test_future_month_has_no_target(self) -> None:
        month = build_month([], 2026, 12, today=date(2026, 10, 27))
        self.assertEqual(month.target, 0)
        self.assertEqual(month.missing, 0)

    def test_ignores_worklogs_of_other_months(self) -> None:
        month = build_month([wl(30, "X-1", 2, month=9)], 2026, 10, today=date(2026, 10, 5))
        self.assertEqual(month.rows, [])
        self.assertEqual(month.logged, 0)


class TestNewWorklogStart(unittest.TestCase):
    def test_starts_at_nine_berlin_plus_logged_time(self) -> None:
        start = new_worklog_start(date(2026, 10, 22), int(5.75 * H))
        self.assertEqual(start, datetime(2026, 10, 22, 14, 45, tzinfo=ZoneInfo("Europe/Berlin")))

    def test_empty_day_starts_at_nine(self) -> None:
        start = new_worklog_start(date(2026, 10, 26), 0)
        self.assertEqual(start, datetime(2026, 10, 26, 9, 0, tzinfo=ZoneInfo("Europe/Berlin")))
        self.assertEqual(start.utcoffset().total_seconds(), H)  # CET after the switch on 25 Oct


class TestBarPercent(unittest.TestCase):
    def test_share_of_a_workday(self) -> None:
        self.assertEqual(bar_percent(0), 0)
        self.assertEqual(bar_percent(H), 12.5)
        self.assertEqual(bar_percent(4 * H), 50)
        self.assertEqual(bar_percent(8 * H), 100)
        self.assertEqual(bar_percent(10 * H), 100)


class TestDayPanel(unittest.TestCase):
    def test_groups_per_ticket_with_dialog_ticket_first(self) -> None:
        logs = [
            wl(22, "DEMO-67", 0.5, "Daily"),
            wl(22, "DEMO-67", 0.25, "Afterdaily"),
            wl(22, "DEMO-987", 2),
            wl(22, "DEMO-975", 3),
        ]
        groups = day_groups(logs, first_key="DEMO-975")
        self.assertEqual([g.key for g in groups], ["DEMO-975", "DEMO-67", "DEMO-987"])
        self.assertEqual(groups[1].seconds, 45 * 60)
        self.assertEqual(groups[1].comments, ["Daily", "Afterdaily"])
        self.assertEqual(groups[2].comments, [])

    def test_after_text_without_time(self) -> None:
        self.assertEqual(day_after_text(int(5.75 * H), 0), ("2:15 missing on this day", False))
        self.assertEqual(day_after_text(8 * H, 0), ("Day complete", False))

    def test_after_text_with_time(self) -> None:
        self.assertEqual(
            day_after_text(int(5.75 * H), 90 * 60),
            ("With this worklog: 7:15, 0:45 still missing", False),
        )
        self.assertEqual(day_after_text(8 * H, 75 * 60), ("1:15 over 8:00", True))
        self.assertEqual(day_after_text(6 * H, 2 * H), ("With this worklog: 8:00, day complete", False))

    def test_added_message(self) -> None:
        self.assertEqual(
            added_message("DEMO-975", date(2026, 10, 22), 90 * 60, int(4.5 * H)),
            "Added 1:30 to DEMO-975 on Thursday 22 Oct. New total that day: 4:30.",
        )


class TestGroupTotals(unittest.TestCase):
    def test_group_of(self) -> None:
        self.assertEqual(group_of("H2BNG-67"), "H2BNG")
        self.assertEqual(group_of("h2bng-67"), "h2bng")
        self.assertEqual(group_of("DEMO-1"), "DEMO")
        self.assertEqual(group_of("MAINTENANCE"), "MAINTENANCE")
        self.assertEqual(group_of("A-B-1"), "A")

    def test_sums_each_group_biggest_first(self) -> None:
        month = build_month(
            [
                wl(1, "H2BNG-67", 2),
                wl(2, "H2BNG-67", 1.5),
                wl(1, "H2BNG-975", 4),
                wl(3, "GCF-12", 3),
                wl(3, "MAINTENANCE", 0.5),
            ],
            2026,
            10,
            date(2026, 10, 29),
        )
        self.assertEqual(
            month.group_totals,
            [
                GroupTotal("H2BNG", int(7.5 * H)),
                GroupTotal("GCF", 3 * H),
                GroupTotal("MAINTENANCE", 0.5 * H),
            ],
        )

    def test_no_tickets_means_no_groups(self) -> None:
        self.assertEqual(build_month([], 2026, 10, date(2026, 10, 29)).group_totals, [])


class TestCsv(unittest.TestCase):
    def test_same_columns_as_worklog_export(self) -> None:
        data = csv_bytes([wl(2, "B-1", 1.5, "two\nlines"), wl(1, "A-1", 0.25)])
        self.assertTrue(data.startswith("﻿".encode("utf-8")))
        rows = list(csv.reader(io.StringIO(data.decode("utf-8-sig")), delimiter=";"))
        self.assertEqual(rows[0], ["date", "issue", "summary", "hours", "comment"])
        self.assertEqual(rows[1], ["2026-10-01", "A-1", "Summary of A-1", "0.25", ""])
        self.assertEqual(rows[2], ["2026-10-02", "B-1", "Summary of B-1", "1.5", "two lines"])


if __name__ == "__main__":
    unittest.main()
