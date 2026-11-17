"""One learning run: read the ledger, analyse, save state, write the weekly report."""

from __future__ import annotations

import csv
import logging
from datetime import date, datetime
from pathlib import Path

from sentinel.config import AppConfig
from sentinel.event_log import date_from_stem, gmp_date
from sentinel.integrity import ledger_directory
from sentinel.learn.analysis import Analysis, analyze
from sentinel.learn.features import load_dataset
from sentinel.learn.report import write_report
from sentinel.learn.state import (
    BASELINES_FILE,
    FINDINGS_FILE,
    HISTORY_FILE,
    MODEL_INFO_FILE,
    PROGRESS_FILE,
    ml_directory,
    read_json,
    report_directory,
    write_json,
)

logger = logging.getLogger(__name__)


def _latest_report_day(folder: Path, prefix: str) -> date | None:
    days = [
        date_from_stem(p.stem[len(prefix):])
        for p in folder.glob(f"{prefix}*.xlsx")
        if date_from_stem(p.stem[len(prefix):]) is not None
    ]
    return max(days) if days else None


def _append_history(folder: Path, analysis: Analysis, today: date) -> None:
    """One row per door per day, so the learning curve itself can be reviewed later."""
    path = folder / HISTORY_FILE
    if path.exists():
        with path.open(newline="", encoding="utf-8") as handle:
            if any(row and row[0] == today.isoformat() for row in csv.reader(handle)):
                return  # already recorded today
    counts: dict[str, int] = {}
    for finding in analysis.findings:
        counts[finding["door"]] = counts.get(finding["door"], 0) + 1
    new = not path.exists()
    with path.open("a", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        if new:
            writer.writerow(["date", "door", "anomaly_score", "last7_median_open_s", "last7_openings_per_day", "findings"])
        for b in analysis.baselines:
            writer.writerow([today.isoformat(), b["door"], analysis.scores.get(b["door"], ""),
                             "" if b["last7_median_open_s"] is None else b["last7_median_open_s"],
                             b["last7_openings_per_day"], counts.get(b["door"], 0)])


def run_learning(config: AppConfig, now: datetime | None = None, force_report: bool = False) -> dict:
    """Run once. Returns a short summary (also printed by ``--learn-now``)."""
    now = now or datetime.now()
    today = now.date()
    ledger_path = ledger_directory(config) / "ledger.jsonl"
    release_names = {
        release.object_name: release.airlock
        for plc in config.plcs
        for release in plc.emergency_releases
    }
    dataset = load_dataset(ledger_path, now, release_names)
    analysis = analyze(dataset, today, now, config.learning.target_days)

    folder = ml_directory(config)
    previous = read_json(folder / FINDINGS_FILE) or []
    first_seen = {(f["door"], f["kind"]): f.get("first_seen") for f in previous}
    for finding in analysis.findings:
        finding["first_seen"] = first_seen.get((finding["door"], finding["kind"])) or today.isoformat()

    write_json(folder / PROGRESS_FILE, analysis.progress)
    write_json(folder / FINDINGS_FILE, analysis.findings)
    write_json(folder / BASELINES_FILE, analysis.baselines)
    write_json(folder / MODEL_INFO_FILE, analysis.model_info)
    if dataset.records:
        _append_history(folder, analysis, today)

    written: list[str] = []
    if dataset.records and (force_report or analysis.progress["days"] >= 7):  # nothing worth reporting before that
        reports = report_directory(config)
        stamp = now.strftime("%Y-%m-%d %H:%M:%S")
        last = _latest_report_day(reports, "ML-Report-")
        if force_report or last is None or (today - last).days >= config.learning.report_every_days:
            written.append(str(write_report(reports / f"ML-Report-{gmp_date(today)}.xlsx", analysis, stamp)))
        if analysis.progress["stage"] == "complete" and _latest_report_day(reports, "ML-Study-") is None:
            written.append(str(write_report(reports / f"ML-Study-{gmp_date(today)}.xlsx", analysis, stamp, study=True)))
    logger.info("Learning run: %d day(s) of data, %d finding(s)", analysis.progress["days"], len(analysis.findings))
    return {"progress": analysis.progress, "findings": analysis.findings, "reports": written, "state_dir": str(folder)}
