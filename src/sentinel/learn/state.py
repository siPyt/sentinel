"""Where the learning job keeps things, and the small JSON state the cockpit reads.

No numpy/scikit-learn imports here: the polling service and cockpit import this module.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

from sentinel.config import AppConfig
from sentinel.integrity import ledger_directory

PROGRESS_FILE = "progress.json"
FINDINGS_FILE = "findings.json"
BASELINES_FILE = "baselines.json"
MODEL_INFO_FILE = "model_info.json"
HISTORY_FILE = "history.csv"


def ml_directory(config: AppConfig) -> Path:
    """Learning state folder; by default next to the ledger (e.g. /var/lib/sentinel/ml)."""
    configured = config.learning.directory
    return Path(configured).expanduser() if configured else ledger_directory(config).parent / "ml"


def report_directory(config: AppConfig) -> Path:
    configured = config.learning.report_directory
    if configured:
        return Path(configured).expanduser()
    return Path(config.event_log.directory).expanduser() / "ML-Reports"


def write_json(path: Path, data) -> None:
    """Atomic write so the cockpit never reads a half-written file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(data, indent=1, sort_keys=True), encoding="utf-8")
    os.replace(tmp, path)


def read_json(path: Path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def load_state(directory: Path) -> dict | None:
    """What the cockpit shows: progress plus current findings (None until the first run)."""
    progress = read_json(directory / PROGRESS_FILE)
    if progress is None:
        return None
    findings = read_json(directory / FINDINGS_FILE) or []
    return {"progress": progress, "findings": findings}
