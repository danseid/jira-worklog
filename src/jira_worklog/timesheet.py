"""Pure functions for the month grid. No Jira and no web code here."""

from __future__ import annotations

import calendar
import csv
import io
import re
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

WORKDAY_SECONDS = 8 * 3600
BERLIN = ZoneInfo("Europe/Berlin")
DAY_START = time(9, 0)
DAY_SHORT = ["Mo", "Tu", "We", "Th", "Fr", "Sa", "Su"]

_HMM = re.compile(r"^(\d+):([0-5]\d)$")
_DECIMAL = re.compile(r"^\d+(?:\.\d+)?$")
_UNITS = re.compile(r"^(?:(\d+(?:\.\d+)?)\s*h)?\s*(?:(\d+)\s*m)?$")


@dataclass(frozen=True)
class Worklog:
    id: str
    key: str
    summary: str
    day: date
    seconds: int
    comment: str


@dataclass
class TicketRow:
    key: str
    summary: str
    cells: dict[date, list[Worklog]] = field(default_factory=dict)

    @property
    def total(self) -> int:
        return sum(w.seconds for logs in self.cells.values() for w in logs)

    def worklogs_on(self, day: date) -> list[Worklog]:
        return self.cells.get(day, [])

    def seconds_on(self, day: date) -> int:
        return sum(w.seconds for w in self.worklogs_on(day))


@dataclass
class Month:
    year: int
    month: int
    today: date
    days: list[date]
    rows: list[TicketRow]
    worklogs: list[Worklog]

    def day_total(self, day: date) -> int:
        return sum(w.seconds for w in self.worklogs if w.day == day)

    def worklogs_on(self, day: date) -> list[Worklog]:
        return [w for w in self.worklogs if w.day == day]

    def counts(self, day: date) -> bool:
        """True for a workday up to today: it has a target of 8 h."""
        return is_workday(day) and day <= self.today

    def missing_on(self, day: date) -> int:
        if not self.counts(day):
            return 0
        return max(0, WORKDAY_SECONDS - self.day_total(day))

    @property
    def logged(self) -> int:
        return sum(w.seconds for w in self.worklogs)

    @property
    def target(self) -> int:
        return WORKDAY_SECONDS * sum(1 for d in self.days if self.counts(d))

    @property
    def missing(self) -> int:
        return sum(self.missing_on(d) for d in self.days)


@dataclass(frozen=True)
class DayGroup:
    key: str
    summary: str
    seconds: int
    comments: list[str]


def parse_duration(text: str) -> int | None:
    """Read 1:30, 1.5, 1,5, 1h 30m or 90m. Return seconds, 0 for empty text, None when not a time."""
    s = text.strip().lower().replace(",", ".")
    if not s:
        return 0
    if m := _HMM.match(s):
        minutes = int(m[1]) * 60 + int(m[2])
    elif _DECIMAL.match(s):
        minutes = round(float(s) * 60)
    elif (m := _UNITS.match(s)) and (m[1] or m[2]):
        minutes = round(float(m[1] or 0) * 60) + int(m[2] or 0)
    else:
        return None
    return minutes * 60


def format_hmm(seconds: int) -> str:
    minutes = round(seconds / 60)
    return f"{minutes // 60}:{minutes % 60:02d}"


def format_hm(seconds: int) -> str:
    hours, minutes = divmod(round(seconds / 60), 60)
    parts = ([f"{hours}h"] if hours else []) + ([f"{minutes}m"] if minutes else [])
    return " ".join(parts) or "0m"


def is_workday(day: date) -> bool:
    return day.weekday() < 5


def month_days(year: int, month: int) -> list[date]:
    last = calendar.monthrange(year, month)[1]
    return [date(year, month, d) for d in range(1, last + 1)]


def build_month(worklogs: list[Worklog], year: int, month: int, today: date) -> Month:
    days = month_days(year, month)
    inside = [w for w in worklogs if days[0] <= w.day <= days[-1]]
    rows: dict[str, TicketRow] = {}
    for w in inside:
        row = rows.setdefault(w.key, TicketRow(w.key, w.summary))
        row.cells.setdefault(w.day, []).append(w)
    return Month(
        year=year,
        month=month,
        today=today,
        days=days,
        rows=[rows[k] for k in sorted(rows)],
        worklogs=inside,
    )


def new_worklog_start(day: date, logged_seconds: int) -> datetime:
    """09:00 Europe/Berlin plus the time already logged on that day, so worklogs do not overlap."""
    return datetime.combine(day, DAY_START, tzinfo=BERLIN) + timedelta(seconds=logged_seconds)


def bar_percent(seconds: int) -> float:
    """Width of the bar in a grid cell: the share of an 8 h workday, at most 100."""
    return min(100, max(0, seconds) * 100 / WORKDAY_SECONDS)


def day_groups(worklogs: list[Worklog], first_key: str) -> list[DayGroup]:
    """One group per ticket for one day. The ticket first_key comes first, then by key."""
    by_key: dict[str, list[Worklog]] = defaultdict(list)
    for w in worklogs:
        by_key[w.key].append(w)
    keys = sorted(by_key, key=lambda k: (k != first_key, k))
    return [
        DayGroup(
            key=k,
            summary=by_key[k][0].summary,
            seconds=sum(w.seconds for w in by_key[k]),
            comments=[w.comment for w in by_key[k] if w.comment],
        )
        for k in keys
    ]


def day_after_text(day_total: int, new_seconds: int) -> tuple[str, bool]:
    """The line under the day bar in the dialog. The flag is True when the day goes over 8 h."""
    if new_seconds <= 0:
        if day_total < WORKDAY_SECONDS:
            return f"{format_hmm(WORKDAY_SECONDS - day_total)} missing on this day", False
        return "Day complete", False
    after = day_total + new_seconds
    if after > WORKDAY_SECONDS:
        return f"{format_hmm(after - WORKDAY_SECONDS)} over 8:00", True
    if after == WORKDAY_SECONDS:
        return f"With this worklog: {format_hmm(after)}, day complete", False
    return (
        f"With this worklog: {format_hmm(after)}, "
        f"{format_hmm(WORKDAY_SECONDS - after)} still missing",
        False,
    )


def added_message(key: str, day: date, seconds: int, new_cell_total: int) -> str:
    return (
        f"Added {format_hmm(seconds)} to {key} on {day:%A} {day.day} {day:%b}. "
        f"New total that day: {format_hmm(new_cell_total)}."
    )


def csv_bytes(worklogs: list[Worklog]) -> bytes:
    """The same columns as worklog_export.py: date;issue;summary;hours;comment, UTF-8 with BOM."""
    out = io.StringIO()
    writer = csv.writer(out, delimiter=";", lineterminator="\r\n")
    writer.writerow(["date", "issue", "summary", "hours", "comment"])
    for w in sorted(worklogs, key=lambda w: (w.day, w.key)):
        writer.writerow(
            [
                w.day.isoformat(),
                w.key,
                w.summary,
                round(w.seconds / 3600, 2),
                w.comment.replace("\n", " "),
            ]
        )
    return out.getvalue().encode("utf-8-sig")
