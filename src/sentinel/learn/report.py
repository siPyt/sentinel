"""Weekly Excel report of what the learning job found (advisory, not a GMP record)."""

from __future__ import annotations

from pathlib import Path

from openpyxl import Workbook
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter

from sentinel.learn.analysis import Analysis

_FONT = "Calibri"
_HEAD = PatternFill("solid", start_color="1F3864", end_color="1F3864")
_SEVERITY = {  # fill, text colour
    "alert": ("FFC7CE", "9C0006"),
    "watch": ("FFE699", "7F6000"),
    "info": ("DDEBF7", "1F4E79"),
}
_THIN = Side(style="thin", color="BFBFBF")

EXPLAIN = (
    ("alert / watch / info", "How much attention a finding deserves: alert = look soon, watch = keep an eye on it, info = good to know."),
    ("Unusual day", "One of the last 7 days did not look like that door's normal (open times, openings, timeouts, chatter, night use). The numbers behind it are in Evidence."),
    ("Open times have drifted", "The door's typical open time moved away from its usual level (EWMA control chart)."),
    ("Open times are trending", "A steady rise or fall over the last 8 weeks (Theil-Sen slope, Kendall significance test). Often the first sign of wear."),
    ("More openings are timing out", "A growing share of openings run past the 30 s door-ajar timer."),
    ("Slower than its sister doors", "The door's typical open time is far above the other doors in the same airlock."),
    ("Unusual activity", "A whole-site hour with far more openings than that weekday and hour normally has."),
    ("Learning stages", "Day 14 baselines; day 30 anomaly detection and drift watch; day 60 trends, sister-door and traffic comparisons; day 180 six-month study."),
    ("Not a GMP record", "Advisory only. It is built from the tamper-evident ledger, never changes the logs, and does not control any alarm. Confirm findings on the door before acting."),
)


def _header(sheet, titles, widths) -> None:
    sheet.append(list(titles))
    for col in range(1, len(titles) + 1):
        cell = sheet.cell(1, col)
        cell.font = Font(name=_FONT, size=12, bold=True, color="FFFFFF")
        cell.fill = _HEAD
        cell.alignment = Alignment(vertical="center", wrap_text=True)
        sheet.column_dimensions[get_column_letter(col)].width = widths[col - 1]
    sheet.row_dimensions[1].height = 28
    sheet.freeze_panes = "A2"


def _style_row(sheet, row: int, columns: int, wrap_cols=()) -> None:
    for col in range(1, columns + 1):
        cell = sheet.cell(row, col)
        cell.font = Font(name=_FONT, size=11)
        cell.border = Border(left=_THIN, right=_THIN, top=_THIN, bottom=_THIN)
        cell.alignment = Alignment(vertical="top", wrap_text=col in wrap_cols)


def write_report(path: Path, analysis: Analysis, now_text: str, study: bool = False) -> Path:
    workbook = Workbook()
    progress = analysis.progress

    summary = workbook.active
    summary.title = "Summary"
    title = "Sentinel six-month door study" if study else "Sentinel learning report"
    summary["A1"] = title
    summary["A1"].font = Font(name=_FONT, size=18, bold=True, color="1F3864")
    rows = [
        ("Generated", now_text),
        ("Days of data learned from", f"{progress['days']} of {progress['target_days']} ({progress['percent']}%)"),
        ("Stage", progress["phase"]),
        ("Doors", progress["doors"]),
        ("Ledger records read", progress["records"]),
        ("Ledger head code", progress["ledger_head"]),
        ("Findings", f"{sum(f['severity'] == 'alert' for f in analysis.findings)} alert, "
                     f"{sum(f['severity'] == 'watch' for f in analysis.findings)} watch, "
                     f"{sum(f['severity'] == 'info' for f in analysis.findings)} info"),
        ("Model", analysis.model_info.get("model", "none yet") + (
            f" (trained {analysis.model_info['trained']}, {analysis.model_info['training_door_days']} door-days, "
            f"scikit-learn {analysis.model_info['scikit_learn']})" if analysis.model_info.get("trained") else "")),
        ("Important", "Advisory only. Not part of the GMP record; it never changes the logs or any alarm."),
    ]
    for offset, (label, value) in enumerate(rows, start=3):
        summary.cell(offset, 1, label).font = Font(name=_FONT, size=12, bold=True)
        cell = summary.cell(offset, 2, value)
        cell.font = Font(name=_FONT, size=12)
        cell.alignment = Alignment(wrap_text=True, vertical="top")
    summary.column_dimensions["A"].width = 30
    summary.column_dimensions["B"].width = 100

    sheet = workbook.create_sheet("Findings")
    _header(sheet, ("Severity", "Door", "Airlock", "Finding", "Evidence", "Suggested action", "Date", "First seen"),
            (11, 16, 20, 34, 62, 48, 12, 12))
    if not analysis.findings:
        sheet.append(["", "", "", "Nothing to report" if progress["days"] >= 14 else "Still collecting data", "", "", "", ""])
        _style_row(sheet, 2, 8)
    for finding in analysis.findings:
        sheet.append([finding["severity"].upper(), finding["door"], finding["airlock"], finding["title"],
                      finding["evidence"], finding["suggest"], finding["date"], finding.get("first_seen", "")])
        row = sheet.max_row
        _style_row(sheet, row, 8, wrap_cols=(4, 5, 6))
        fill, color = _SEVERITY[finding["severity"]]
        cell = sheet.cell(row, 1)
        cell.fill = PatternFill("solid", start_color=fill, end_color=fill)
        cell.font = Font(name=_FONT, size=11, bold=True, color=color)
        sheet.row_dimensions[row].height = 48
    sheet.auto_filter.ref = f"A1:H{max(2, sheet.max_row)}"

    base = workbook.create_sheet("Door baselines")
    _header(base, ("Door", "Airlock", "Days", "Openings / weekday", "Openings / weekend day", "Typical open (s)",
                   "Long open, 95% (s)", "Timed-out share", "Last 7 d typical open (s)", "Last 7 d openings / day",
                   "Chatter alerts (30 d)", "Interlock violations (30 d)", "Latest anomaly score"),
            (16, 20, 7, 12, 14, 12, 14, 12, 16, 14, 14, 16, 14))
    for b in analysis.baselines:
        base.append([b["door"], b["airlock"], b["days"], b["openings_per_weekday"], b["openings_per_weekend_day"],
                     b["median_open_s"], b["p95_open_s"], b["timeout_rate"], b["last7_median_open_s"],
                     b["last7_openings_per_day"], b["chatter_30d"], b["interlock_30d"],
                     analysis.scores.get(b["door"])])
        _style_row(base, base.max_row, 13)
        base.cell(base.max_row, 8).number_format = "0.0%"
    base.auto_filter.ref = f"A1:M{max(2, base.max_row)}"

    guide = workbook.create_sheet("How to read")
    _header(guide, ("Term", "Meaning"), (30, 120))
    for term, meaning in EXPLAIN:
        guide.append([term, meaning])
        _style_row(guide, guide.max_row, 2, wrap_cols=(2,))
        guide.cell(guide.max_row, 1).font = Font(name=_FONT, size=11, bold=True)
        guide.row_dimensions[guide.max_row].height = 34

    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    workbook.save(tmp)
    tmp.replace(path)
    return path
