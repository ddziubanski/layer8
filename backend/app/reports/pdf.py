"""A report as a PDF: title, period, the table (first measure drawn as a bar), totals."""

from datetime import date, datetime
from pathlib import Path

from fpdf import FPDF

from app.reports.engine import ReportResult, ReportSpec

FONTS = Path(__file__).parent / "fonts"
BRAND = (15, 107, 92)  # the app's --brand
INK, MUTED, LINE, HEAD_BG = (27, 36, 33), (70, 82, 78), (223, 228, 224), (236, 238, 235)


def period_text(spec: ReportSpec, date_label: str) -> str:
    def d(x: date) -> str:
        return f"{x.day} {x:%b %Y}"

    if spec.date_from and spec.date_to:
        return f"{date_label} {d(spec.date_from)} – {d(spec.date_to)}"
    if spec.date_from:
        return f"{date_label} since {d(spec.date_from)}"
    if spec.date_to:
        return f"{date_label} up to {d(spec.date_to)}"
    return "All time"


def _value(v, kind: str) -> str:
    if v is None:
        return "–"
    if kind == "percent":
        return f"{v}%"
    if kind == "money":
        return f"{float(v):,.2f}"
    if kind == "hours":
        return f"{v} h"
    if kind == "time":
        return datetime.fromisoformat(str(v)).strftime("%d %b %Y").lstrip("0")
    if isinstance(v, float) and v.is_integer() and kind == "count":
        return str(int(v))
    return str(v)


class _Doc(FPDF):
    footer_text = ""

    def footer(self):
        self.set_y(-12)
        self.set_font("Noto", size=8)
        self.set_text_color(*MUTED)
        self.cell(0, 6, self.footer_text, align="L")
        self.set_x(self.l_margin)
        self.cell(0, 6, f"Page {self.page_no()} of {{nb}}", align="R")


def render(result: ReportResult, *, title: str, subtitle: str, period: str, generated_for: str, now: datetime) -> bytes:
    groups = [c for c in result.columns if c.kind in ("text", "time")]
    measures = [c for c in result.columns if c.kind not in ("text", "time")]
    landscape = len(result.columns) > 4
    doc = _Doc(orientation="L" if landscape else "P", unit="mm", format="A4")
    doc.add_font("Noto", "", str(FONTS / "NotoSans-Regular.ttf"))
    doc.add_font("Noto", "B", str(FONTS / "NotoSans-Bold.ttf"))
    doc.set_margins(14, 14, 14)
    doc.set_auto_page_break(True, 18)
    doc.footer_text = f"Service Desk · {generated_for} · {now:%d %b %Y %H:%M}"
    doc.alias_nb_pages()
    doc.add_page()

    doc.set_font("Noto", "B", 16)
    doc.set_text_color(*INK)
    doc.multi_cell(0, 8, title, new_x="LMARGIN", new_y="NEXT")
    doc.set_font("Noto", size=9.5)
    doc.set_text_color(*MUTED)
    if subtitle:
        doc.multi_cell(0, 5, subtitle, new_x="LMARGIN", new_y="NEXT")
    doc.multi_cell(0, 5, period, new_x="LMARGIN", new_y="NEXT")
    doc.ln(4)

    width = doc.w - doc.l_margin - doc.r_margin
    # Measure columns fit their heading; the first one also holds the bar, so it gets more room.
    doc.set_font("Noto", "B", 8)
    measure_w = [max(22, doc.get_string_width(c.label.upper()) + 6) for c in measures]
    if measures:
        measure_w[0] += 0.22 * width
    group_w = (width - sum(measure_w)) / max(1, len(groups))
    widths = [group_w] * len(groups) + measure_w
    first = measures[0] if measures else None
    max_value = max((float(r[len(groups)] or 0) for r in result.rows), default=0) if first else 0
    row_h = 7

    def header():
        doc.set_font("Noto", "B", 8)
        doc.set_fill_color(*HEAD_BG)
        doc.set_text_color(*MUTED)
        for c, w in zip(result.columns, widths, strict=True):
            doc.cell(w, row_h, c.label.upper(), border=0, fill=True, align="R" if c in measures else "L")
        doc.ln(row_h)

    header()
    doc.set_font("Noto", size=9)
    for row in result.rows:
        if doc.get_y() + row_h > doc.h - doc.b_margin:
            doc.add_page()
            header()
            doc.set_font("Noto", size=9)
        y = doc.get_y()
        doc.set_text_color(*INK)
        for i, (c, w) in enumerate(zip(result.columns, widths, strict=True)):
            text = _value(row[i], c.kind)
            if c is first and max_value > 0:
                x = doc.get_x()
                bar_w = (w - 20) * (float(row[i] or 0) / max_value)
                if bar_w > 0:
                    doc.set_fill_color(*BRAND)
                    # Square at the baseline, rounded only at its data end.
                    doc.rect(x + 2, y + 2.25, max(bar_w, 0.6), 2.5, style="F", round_corners=("TOP_RIGHT", "BOTTOM_RIGHT"), corner_radius=1)
                doc.set_xy(x, y)
                doc.cell(w, row_h, text, align="R")
            else:
                doc.cell(w, row_h, text[:80], align="R" if c in measures else "L")
        doc.ln(row_h)
        doc.set_draw_color(*LINE)
        doc.line(doc.l_margin, doc.get_y(), doc.l_margin + width, doc.get_y())

    if not result.rows:
        doc.set_text_color(*MUTED)
        doc.cell(0, row_h, "Nothing in this period.")
        doc.ln(row_h)
    elif groups and result.totals:
        doc.set_font("Noto", "B", 9)
        doc.set_text_color(*INK)
        doc.cell(sum(widths[: len(groups)]), row_h, "Total")
        for c, w in zip(measures, widths[len(groups) :], strict=True):
            doc.cell(w, row_h, _value(result.totals.get(c.key), c.kind) if c.key in result.totals else "", align="R")
        doc.ln(row_h)
    if result.truncated:
        doc.ln(2)
        doc.set_font("Noto", size=8)
        doc.set_text_color(*MUTED)
        doc.multi_cell(0, 5, f"Showing the first {len(result.rows)} rows.", new_x="LMARGIN", new_y="NEXT")
    return bytes(doc.output())
