"""FastAPI routes of the worklog dashboard. Start with: jira-worklog"""

from __future__ import annotations

import argparse
import calendar
import json
import threading
import webbrowser
from dataclasses import replace
from html import escape
from datetime import date, datetime
from pathlib import Path
from typing import Any, Callable

from fastapi import FastAPI, Form, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from jira_worklog.jira_worklogs import JiraWriteError
from jira_worklog.timesheet import (
    BERLIN,
    DAY_SHORT,
    WORKDAY_SECONDS,
    Month,
    added_message,
    bar_percent,
    build_month,
    csv_bytes,
    day_after_text,
    day_groups,
    format_hm,
    format_hmm,
    is_workday,
    new_worklog_start,
    parse_duration,
)
from jira_worklog.pdf_export import pdf_bytes

HOST = "127.0.0.1"
PORT = 8765
HERE = Path(__file__).parent
QUICK_TIMES = ["15m", "30m", "1h", "2h", "4h"]
TIME_ERROR = "Type a time like 1:30, 1.5 or 90m."


def berlin_today() -> date:
    return datetime.now(BERLIN).date()


def parse_month(text: str) -> tuple[int, int]:
    try:
        first = datetime.strptime(text, "%Y-%m").date()
    except ValueError:
        raise HTTPException(status_code=404, detail="Unknown month") from None
    return first.year, first.month


def parse_day(text: str) -> date | None:
    try:
        return date.fromisoformat(text)
    except ValueError:
        return None


def shift_month(year: int, month: int, step: int) -> str:
    index = year * 12 + month - 1 + step
    return f"{index // 12:04d}-{index % 12 + 1:02d}"


def cell_id(key: str, day: date) -> str:
    return f"cell-{key}-{day.isoformat()}"


def create_app(jira: Any, today: Callable[[], date] = berlin_today) -> FastAPI:
    """jira is a JiraWorklogs or a fake with the same methods."""
    app = FastAPI(title="Worklog dashboard", docs_url=None, redoc_url=None, openapi_url=None)
    app.mount("/static", StaticFiles(directory=HERE / "static"), name="static")
    templates = Jinja2Templates(directory=HERE / "templates")
    templates.env.filters["hmm"] = format_hmm
    templates.env.globals.update(
        bar_percent=bar_percent, is_workday=is_workday, cell_id=cell_id, day_short=DAY_SHORT
    )
    user: list[str] = []

    def current_user() -> str:
        if not user:
            user.append(jira.current_user())
        return user[0]

    def load_month(year: int, month: int) -> Month:
        last = calendar.monthrange(year, month)[1]
        logs = jira.month_worklogs(current_user(), date(year, month, 1), date(year, month, last))
        return build_month(logs, year, month, today())

    def grid_context(year: int, month: int, flash: str = "") -> dict[str, Any]:
        return {
            "m": load_month(year, month),
            "ym": f"{year:04d}-{month:02d}",
            "today": today(),
            "issue_url": jira.issue_url,
            "flash": flash,
        }

    def panel_context(key: str, summary: str, day_text: str, time_text: str) -> dict[str, Any]:
        day = parse_day(day_text)
        seconds = parse_duration(time_text)
        valid = seconds is not None and seconds > 0
        month = load_month(day.year, day.month) if day else None
        logs = month.worklogs_on(day) if month else []
        if not summary and month:
            summary = next((r.summary for r in month.rows if r.key == key), "")
        day_total = sum(w.seconds for w in logs)
        new = seconds if valid else 0
        after, over = day_after_text(day_total, new)
        old_pct = min(100.0, day_total / WORKDAY_SECONDS * 100)
        quick = [(q, format_hmm(parse_duration(q))) for q in QUICK_TIMES]
        if day and day_total < WORKDAY_SECONDS:
            rest = format_hmm(WORKDAY_SECONDS - day_total)
            quick.append((f"Rest of day {rest}", rest))
        if not time_text.strip():
            hint, hint_error = "1:30, 1.5 or 90m", False
        elif valid:
            hint, hint_error = f"= {format_hm(seconds)}", False
        else:
            hint, hint_error = TIME_ERROR, True
        return {
            "key": key,
            "summary": summary,
            "day": day,
            "day_text": day_text,
            "time_text": time_text,
            "new": new,
            "can_add": valid and day is not None,
            "hint": hint,
            "hint_error": hint_error,
            "quick": quick,
            "day_total": day_total,
            "after": after,
            "over": over,
            "old_pct": old_pct,
            "new_pct": max(0.0, min(100.0, (day_total + new) / WORKDAY_SECONDS * 100) - old_pct),
            "groups": day_groups(logs, first_key=key),
        }

    @app.get("/")
    def root() -> RedirectResponse:
        return RedirectResponse(f"/month/{today():%Y-%m}")

    @app.get("/month/{ym}", response_class=HTMLResponse)
    def page(request: Request, ym: str) -> HTMLResponse:
        year, month = parse_month(ym)
        context = grid_context(year, month)
        context.update(prev=shift_month(year, month, -1), next=shift_month(year, month, 1))
        return templates.TemplateResponse(request, "page.html", context)

    @app.get("/month/{ym}/export.csv")
    def export_csv(ym: str) -> Response:
        year, month = parse_month(ym)
        filename = f"worklogs-{current_user().split('@')[0]}-{ym}.csv"
        return Response(
            csv_bytes(load_month(year, month).worklogs),
            media_type="text/csv; charset=utf-8",
            headers={"Content-Disposition": f'attachment; filename="{filename}"'},
        )

    @app.get("/month/{ym}/export.pdf")
    def export_pdf(ym: str) -> Response:
        year, month = parse_month(ym)
        user_name = current_user()
        filename = f"worklogs-{user_name.split('@')[0]}-{ym}.pdf"
        return Response(
            pdf_bytes(load_month(year, month), user_name),
            media_type="application/pdf",
            headers={"Content-Disposition": f'attachment; filename="{filename}"'},
        )

    @app.get("/dialog", response_class=HTMLResponse)
    def dialog(
        request: Request, key: str, day: str = Query(alias="date"), summary: str = "", month: str = ""
    ) -> HTMLResponse:
        context = panel_context(key, summary, day, "")
        context["month"] = month or day[:7]
        return templates.TemplateResponse(request, "dialog.html", context)

    @app.get("/dialog/panel", response_class=HTMLResponse)
    def panel(
        request: Request,
        key: str,
        day: str = Query("", alias="date"),
        time: str = "",
        summary: str = "",
    ) -> HTMLResponse:
        context = panel_context(key, summary, day, time)
        context["oob"] = True
        return templates.TemplateResponse(request, "day_panel.html", context)

    @app.post("/worklog", response_class=HTMLResponse)
    def add_worklog(
        request: Request,
        key: str = Form(...),
        day_text: str = Form("", alias="date"),
        time: str = Form(""),
        comment: str = Form(""),
        month: str = Form(...),
    ) -> HTMLResponse:
        day = parse_day(day_text)
        seconds = parse_duration(time)
        if day is None or not seconds:
            message = TIME_ERROR if day else "Pick a date."
            return error_response(message)
        logged = load_month(day.year, day.month)
        try:
            jira.add_worklog(key, new_worklog_start(day, logged.day_total(day)), seconds, comment)
        except JiraWriteError as exc:
            return error_response(str(exc))

        cell_total = sum(w.seconds for w in load_month(day.year, day.month).worklogs_on(day) if w.key == key)
        year, mon = parse_month(month)
        context = grid_context(year, mon, flash=cell_id(key, day))
        context["status"] = added_message(key, day, seconds, cell_total)
        response = templates.TemplateResponse(request, "saved.html", context)
        response.headers["HX-Trigger-After-Settle"] = json.dumps(
            {"worklogAdded": {"cell": cell_id(key, day)}}
        )
        return response

    def error_response(message: str) -> HTMLResponse:
        response = HTMLResponse(f'<p class="error" role="alert">{escape(message)}</p>')
        response.headers["HX-Retarget"] = "#dlg-error"
        response.headers["HX-Reswap"] = "innerHTML"
        return response

    @app.get("/tickets/search", response_class=HTMLResponse)
    def search(request: Request, q: str = "", month: str = "") -> HTMLResponse:
        context = {"tickets": jira.search_tickets(q), "q": q, "today": today(), "month": month}
        return templates.TemplateResponse(request, "search_results.html", context)

    return app


def main(argv: list[str] | None = None) -> None:
    import uvicorn

    from jira_worklog.jira_worklogs import check_login, connect
    from jira_worklog.settings import CONFIG_FILE, ask_settings, load_settings, save_settings

    parser = argparse.ArgumentParser(
        prog="jira-worklog", description="Show your Jira worklogs for one month and add new worklogs."
    )
    parser.add_argument("--jira-url", help="Jira base URL, for example https://jira.example.com")
    parser.add_argument("--setup", action="store_true", help="Ask for the Jira URL and the token again and save them.")
    args = parser.parse_args(argv)

    settings = load_settings()
    if args.jira_url:
        settings = replace(settings, url=args.jira_url.strip().rstrip("/"))
    if args.setup or not settings.complete:
        try:
            asked = ask_settings(settings.url, check_login, confirm_url=args.setup and not args.jira_url)
        except (KeyboardInterrupt, EOFError):
            raise SystemExit("\nStopped. Nothing was saved.") from None
        settings = replace(asked, verify_ssl=settings.verify_ssl)
        save_settings(settings)
        print(f"Saved the Jira URL and the token in {CONFIG_FILE}. Only you can read this file.")

    url = f"http://{HOST}:{PORT}/"
    threading.Timer(1.0, webbrowser.open, args=(url,)).start()
    print(f"Worklog dashboard: {url}  (Ctrl+C to stop)")
    uvicorn.run(create_app(connect(settings)), host=HOST, port=PORT, log_level="warning")


if __name__ == "__main__":
    main()
