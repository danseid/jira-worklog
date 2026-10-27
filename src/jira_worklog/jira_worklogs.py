"""The only module of the worklog app that talks to Jira.

It uses the REST API v2 of Jira Data Center. A move to Jira Cloud changes only this module.
"""

from __future__ import annotations

import time
from datetime import date, datetime
from typing import Any, Callable

import requests
from jira import JIRA
from jira.exceptions import JIRAError

from jira_worklog.settings import JiraSettings
from jira_worklog.timesheet import Worklog

CACHE_SECONDS = 60
PAGE_SIZE = 100
MAX_TICKETS = 8


class JiraWriteError(Exception):
    """Jira refused a new worklog. The message is Jira's own text."""


class JiraWorklogs:
    def __init__(self, client: JIRA, clock: Callable[[], float] = time.monotonic):
        self._client = client
        self._clock = clock
        self._cache: dict[tuple[str, date, date], tuple[float, list[Worklog]]] = {}

    def current_user(self) -> str:
        return self._client._get_json("myself")["name"]

    def issue_url(self, key: str) -> str:
        return f"{self._client.server_url}/browse/{key}"

    def month_worklogs(self, user: str, first_day: date, last_day: date) -> list[Worklog]:
        cache_key = (user.lower(), first_day, last_day)
        hit = self._cache.get(cache_key)
        if hit and self._clock() - hit[0] < CACHE_SECONDS:
            return hit[1]

        jql = (
            f'worklogAuthor = "{user}" AND worklogDate >= "{first_day}" '
            f'AND worklogDate <= "{last_day}" ORDER BY key'
        )
        result = []
        for issue in self._pages("search", "issues", jql=jql, fields="summary"):
            for raw in self._pages(f"issue/{issue['key']}/worklog", "worklogs"):
                day = date.fromisoformat(raw["started"][:10])
                if raw["author"]["name"].lower() == user.lower() and first_day <= day <= last_day:
                    result.append(
                        Worklog(
                            id=str(raw["id"]),
                            key=issue["key"],
                            summary=issue["fields"]["summary"],
                            day=day,
                            seconds=int(raw["timeSpentSeconds"]),
                            comment=raw.get("comment") or "",
                        )
                    )
        self._cache[cache_key] = (self._clock(), result)
        return result

    def add_worklog(self, key: str, started: datetime, seconds: int, comment: str) -> None:
        try:
            self._client.add_worklog(
                key,
                timeSpentSeconds=str(seconds),
                comment=comment.strip() or None,
                started=started,
            )
        except JIRAError as exc:
            raise JiraWriteError(f"Jira refused the worklog: {exc.text or exc.status_code}") from None
        day = started.date()
        for cache_key in [k for k in self._cache if k[1] <= day <= k[2]]:
            del self._cache[cache_key]

    def search_tickets(self, query: str) -> list[tuple[str, str]]:
        if not query.strip():
            return []
        data = self._client._get_json(
            "issue/picker", params={"query": query.strip(), "showSubTasks": "true"}
        )
        found: dict[str, str] = {}
        for section in data.get("sections", []):
            for item in section.get("issues", []):
                found.setdefault(item["key"], item.get("summaryText", ""))
        return list(found.items())[:MAX_TICKETS]

    def _pages(self, path: str, items: str, **params: Any):
        start = 0
        while True:
            page = self._client._get_json(path, params={**params, "startAt": start, "maxResults": PAGE_SIZE})
            yield from page[items]
            start += len(page[items])
            if not page[items] or start >= page["total"]:
                return


def build_client(settings: JiraSettings, **kwargs: Any) -> JIRA:
    options = {"server": settings.url, "verify": settings.verify_ssl}
    if settings.personal_token:
        return JIRA(options=options, token_auth=settings.personal_token, **kwargs)
    return JIRA(options=options, basic_auth=(settings.username, settings.api_token), **kwargs)


def check_login(settings: JiraSettings, make_client: Callable[..., Any] = build_client) -> str | None:
    """None when Jira accepts the URL and the token, else a short error text."""
    try:
        make_client(settings, max_retries=0, timeout=15)._get_json("myself")
    except JIRAError as exc:
        return " ".join(str(part) for part in (f"HTTP {exc.status_code}", exc.text) if part)
    except requests.RequestException as exc:
        return str(exc)
    return None


def connect(settings: JiraSettings) -> JiraWorklogs:
    """Build the module with the given credentials. The token stays on the server."""
    return JiraWorklogs(build_client(settings))
