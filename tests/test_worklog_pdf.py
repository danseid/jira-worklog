from __future__ import annotations

import unittest
from datetime import date

from jira_worklog.pdf_export import pdf_bytes
from jira_worklog.timesheet import Month, Worklog, build_month

H = 3600


def wl(day: int, key: str, hours: float, summary: str = "Ticket") -> Worklog:
    return Worklog(f"{key}-{day}-{hours}", key, summary, date(2026, 10, day), int(hours * H), "")


def month(worklogs: list[Worklog]) -> Month:
    return build_month(worklogs, 2026, 10, date(2026, 10, 29))


def pages(data: bytes) -> int:
    return data.count(b"/Type /Page\n")


class TestPdfExport(unittest.TestCase):
    def test_landscape_a4_page(self) -> None:
        data = pdf_bytes(month([wl(22, "DEMO-67", 8)]), "jdoe")
        self.assertTrue(data.startswith(b"%PDF"))
        self.assertTrue(data.rstrip().endswith(b"%%EOF"))
        self.assertIn(b"/MediaBox [ 0 0 841.8898 595.2756 ]", data)  # A4, turned on its side

    def test_one_page_for_a_normal_month(self) -> None:
        logs = [wl(day, "DEMO-67", 8) for day in (1, 2, 5, 6, 7)]
        logs += [wl(22, "DEMO-975", 3, "Credit check status >300"), wl(23, "DEMO-975", 4)]
        self.assertEqual(pages(pdf_bytes(month(logs), "jdoe")), 1)

    def test_many_tickets_continue_on_a_second_page(self) -> None:
        logs = [wl(day % 28 + 1, f"DEMO-{100 + day}", 1.5) for day in range(40)]
        self.assertEqual(pages(pdf_bytes(month(logs), "jdoe")), 2)

    def test_empty_month_still_renders(self) -> None:
        data = pdf_bytes(month([]), "jdoe")
        self.assertEqual(pages(data), 1)

    def test_long_summary_is_shortened(self) -> None:
        summary = "A very long summary that does not fit into the ticket column of the sheet"
        data = pdf_bytes(month([wl(22, "DEMO-67", 8, summary)]), "jdoe")
        self.assertNotIn(summary.encode("ascii"), data)


if __name__ == "__main__":
    unittest.main()
