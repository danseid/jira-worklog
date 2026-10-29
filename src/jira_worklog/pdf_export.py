"""The month grid as a landscape PDF. No Jira and no web code here."""

from __future__ import annotations

import io
from xml.sax.saxutils import escape

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib.styles import ParagraphStyle
from reportlab.pdfbase.pdfmetrics import stringWidth
from reportlab.pdfgen.canvas import Canvas
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

from jira_worklog.timesheet import DAY_SHORT, Month, format_hmm, is_workday

INK = colors.HexColor("#15191E")
MUTED = colors.HexColor("#5A6270")
LINE = colors.HexColor("#D5D9E0")
GRID_LINE = colors.HexColor("#DCE0E6")
WEEK_LINE = colors.HexColor("#AEB5C0")
WEEKEND = colors.HexColor("#F8F9FA")
HEAD = colors.HexColor("#F7F8FA")
TODAY = colors.HexColor("#EFF3FD")

MARGIN = 22.0
TICKET_W = 132.0
TOTAL_W = 30.0
SUMMARY_SIZE = 5.6
SUMMARY_ROOM = TICKET_W - 7

TITLE = ParagraphStyle("title", fontName="Helvetica-Bold", fontSize=13, leading=15, textColor=INK)
SUBTITLE = ParagraphStyle("subtitle", fontName="Helvetica", fontSize=7, leading=10, textColor=MUTED)
TICKET = ParagraphStyle("ticket", fontName="Helvetica", fontSize=5.6, leading=6.6, textColor=MUTED)
CELL = ParagraphStyle("cell", fontName="Helvetica", fontSize=5.2, leading=6.2, textColor=INK, alignment=2)
DAY_TOTAL = ParagraphStyle("day-total", fontName="Helvetica", fontSize=5.2, leading=6.2, textColor=INK, alignment=1)


def _fit(text: str, room: float, size: float) -> str:
    """Cut the text until it fits on one line of the given width."""
    if stringWidth(text, "Helvetica", size) <= room:
        return text
    while text and stringWidth(text + "…", "Helvetica", size) > room:
        text = text[:-1]
    return text.rstrip() + "…"


def _ticket_cell(key: str, summary: str) -> Paragraph:
    title = f'<font name="Helvetica-Bold" size="6.6" color="#1F4FD1">{escape(key)}</font>'
    return Paragraph(f"{title}<br/>{escape(_fit(summary, SUMMARY_ROOM, SUMMARY_SIZE))}", TICKET)


def _cell_text(seconds: int) -> str:
    return format_hmm(seconds) if seconds else ""


def _day_total_cell(total: int, missing: int, counts: bool) -> Paragraph:
    if not total and not missing:
        return Paragraph("", DAY_TOTAL)
    if missing:
        return Paragraph(
            f'{format_hmm(total)}<br/>'
            f'<font size="4.8" color="#8A3D00">−{format_hmm(missing)}</font>',
            DAY_TOTAL,
        )
    if counts:
        return Paragraph(f'<font color="#1E5B3A">{format_hmm(total)}</font>', DAY_TOTAL)
    return Paragraph(format_hmm(total), DAY_TOTAL)


def _summary_line(month: Month, user: str) -> Paragraph:
    missing = f'<font color="#8A3D00">missing {format_hmm(month.missing)}</font>' if month.missing else "missing 0:00"
    return Paragraph(
        f"{escape(user)} · logged <b>{format_hmm(month.logged)}</b> · "
        f"target until today <b>{format_hmm(month.target)}</b> · {missing}",
        SUBTITLE,
    )


def _table(month: Month) -> Table:
    days = month.days
    page_width, _ = landscape(A4)
    day_w = (page_width - 2 * MARGIN - TICKET_W - TOTAL_W) / len(days)

    header: list[str] = ["Ticket", "Σ"] + [f"{DAY_SHORT[d.weekday()]}\n{d.day:02d}" for d in days]
    body: list[list[object]] = []
    for row in month.rows:
        cells: list[object] = [format_hmm(row.total) if row.total else ""]
        cells += [_cell_text(row.seconds_on(d)) for d in days]
        body.append([_ticket_cell(row.key, row.summary), *cells])

    footer: list[object] = [
        "Day total",
        format_hmm(month.logged),
        *[_day_total_cell(month.day_total(d), month.missing_on(d), month.counts(d)) for d in days],
    ]
    data: list[list[object]] = [header, *body, footer]

    style: list[tuple] = [
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("FONTSIZE", (0, 0), (-1, 0), 5.4),
        ("LEADING", (0, 0), (-1, 0), 6.4),
        ("TEXTCOLOR", (0, 0), (-1, 0), MUTED),
        ("BACKGROUND", (0, 0), (-1, 0), HEAD),
        ("ALIGN", (0, 0), (0, -1), "LEFT"),
        ("ALIGN", (1, 0), (1, -1), "RIGHT"),
        ("ALIGN", (2, 0), (-1, -1), "CENTER"),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("FONTSIZE", (1, 1), (-1, -2), 5.2),
        ("TEXTCOLOR", (1, 1), (-1, -2), INK),
        ("TOPPADDING", (0, 0), (-1, -1), 2.4),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 2.4),
        ("LEFTPADDING", (0, 0), (-1, -1), 1),
        ("RIGHTPADDING", (0, 0), (-1, -1), 1.5),
        ("INNERGRID", (0, 0), (-1, -1), 0.35, GRID_LINE),
        ("BOX", (0, 0), (-1, -1), 0.6, LINE),
        ("BACKGROUND", (0, -1), (-1, -1), HEAD),
        ("FONTNAME", (0, -1), (1, -1), "Helvetica-Bold"),
        ("FONTSIZE", (0, -1), (1, -1), 6.4),
        ("TEXTCOLOR", (0, -1), (0, -1), MUTED),
        ("LINEABOVE", (0, -1), (-1, -1), 0.6, WEEK_LINE),
    ]
    for index, day in enumerate(days):
        column = index + 2
        if not is_workday(day):
            style.append(("BACKGROUND", (column, 1), (column, -2), WEEKEND))
        if day == month.today:
            style.append(("BACKGROUND", (column, 0), (column, -2), TODAY))
        if day.weekday() == 0 and index:
            style.append(("LINEBEFORE", (column, 0), (column, -1), 0.6, WEEK_LINE))

    table = Table(data, colWidths=[TICKET_W, TOTAL_W, *[day_w] * len(days)], repeatRows=1, style=style)
    if not month.rows:
        table = Table(
            [header, ["No work logged in this month."]],
            colWidths=[TICKET_W, TOTAL_W, *[day_w] * len(days)],
            style=[
                ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
                ("FONTSIZE", (0, 0), (-1, 0), 5.4),
                ("TEXTCOLOR", (0, 0), (-1, 0), MUTED),
                ("BACKGROUND", (0, 0), (-1, 0), HEAD),
                ("SPAN", (0, 1), (-1, 1)),
                ("FONTSIZE", (0, 1), (-1, 1), 7),
                ("TEXTCOLOR", (0, 1), (-1, 1), MUTED),
                ("TOPPADDING", (0, 0), (-1, -1), 3),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
                ("LEFTPADDING", (0, 0), (-1, -1), 3),
                ("INNERGRID", (0, 0), (-1, 0), 0.25, GRID_LINE),
                ("BOX", (0, 0), (-1, -1), 0.6, LINE),
            ],
        )
    return table


def pdf_bytes(month: Month, user: str) -> bytes:
    """The month grid of one user as a landscape A4 PDF."""
    out = io.BytesIO()
    first = month.days[0]

    def footer(canvas: Canvas, doc: object) -> None:
        canvas.saveState()
        canvas.setFont("Helvetica", 5.5)
        canvas.setFillColor(MUTED)
        canvas.drawString(MARGIN, 14, f"jira-worklog · {first:%B %Y} · {user}")
        canvas.drawRightString(landscape(A4)[0] - MARGIN, 14, f"Page {canvas.getPageNumber()}")
        canvas.restoreState()

    doc = SimpleDocTemplate(
        out,
        pagesize=landscape(A4),
        leftMargin=MARGIN,
        rightMargin=MARGIN,
        topMargin=22,
        bottomMargin=26,
        title=f"Worklogs {first:%B %Y}",
        author=user,
        subject="Jira worklogs",
        creator="jira-worklog",
    )
    story = [
        Paragraph(f"Logged work · {first:%B %Y}", TITLE),
        Spacer(1, 2),
        _summary_line(month, user),
        Spacer(1, 7),
        _table(month),
    ]
    doc.build(story, onFirstPage=footer, onLaterPages=footer)
    return out.getvalue()
