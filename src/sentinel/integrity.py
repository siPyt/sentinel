"""Tamper-evident event ledger and run-continuity helpers.

Every event written to the daily Excel/CSV file is also appended, in the same
order, to ``ledger.jsonl``: one JSON line per event, each carrying the SHA-256 of
the previous line. Editing, deleting or inserting any record breaks the chain, and
``python -m sentinel --verify-logs`` compares the day files against the ledger to
show exactly what differs.

The ledger is meant to live in a folder the desktop user can read but not write
(see ``event_log.ledger_directory``), so a person who can edit the Excel files
cannot quietly make the ledger agree with them.

Limits (by design, stated plainly): this detects changes; it does not prevent
someone with root access from rewriting the whole ledger. Cutting the *tail* off
the ledger is only caught by comparing against the head code shown on the cockpit
and printed by ``--verify-logs``, so note that code in a controlled record.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import subprocess
import sys
import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from sentinel.config import AppConfig

logger = logging.getLogger(__name__)

LEDGER_NAME = "ledger.jsonl"
HEARTBEAT_NAME = "heartbeat.json"
GENESIS = "0" * 64
CHECK_CODE_LENGTH = 12  # characters of the hash shown in the day files


def canonical(record: dict) -> str:
    return json.dumps(record, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def chain_hash(record: dict) -> str:
    """SHA-256 of a record (its own ``hash`` field excluded)."""
    body = {k: v for k, v in record.items() if k != "hash"}
    return hashlib.sha256(canonical(body).encode("ascii")).hexdigest()


def config_fingerprint(config: AppConfig) -> str:
    """Short fingerprint of the running configuration (shown in the start row and cockpit)."""
    return hashlib.sha256(config.model_dump_json().encode()).hexdigest()[:12]


def ledger_directory(config: AppConfig) -> Path:
    configured = config.event_log.ledger_directory
    if configured:
        return Path(configured).expanduser()
    return Path(config.event_log.directory).expanduser() / ".ledger"


def parse_utc(text: str) -> datetime:
    return datetime.fromisoformat(text).astimezone(timezone.utc)


def clock_status() -> str:
    """'synced', 'not synced' or 'unknown' -- is the system clock disciplined by a time server?"""
    if sys.platform != "linux":
        return "unknown"
    try:
        out = subprocess.run(
            ["timedatectl", "show", "-p", "NTPSynchronized", "--value"],
            capture_output=True,
            text=True,
            timeout=3,
            check=False,
        ).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return "unknown"
    return {"yes": "synced", "no": "not synced"}.get(out, "unknown")


@dataclass(frozen=True)
class PreviousRun:
    """How the previous run of sentinel ended, judged from the ledger."""

    kind: str  # "none" (first start) | "clean" (SENTINEL STOPPED logged) | "unexpected"
    last_seen_utc: datetime | None


class Ledger:
    """Append-only, hash-chained event ledger (one fsync'd line per event)."""

    def __init__(self, directory: str | Path) -> None:
        self.directory = Path(directory).expanduser()
        self.directory.mkdir(parents=True, exist_ok=True)
        self.path = self.directory / LEDGER_NAME
        self._lock = threading.Lock()
        self._lock_handle = self._acquire_writer_lock()
        self.seq = 0
        self.head = GENESIS
        self.last: dict | None = None
        self._needs_newline = False
        self._load_tail()

    def _acquire_writer_lock(self):
        """Only one process may write the chain; a second sentinel gets an OSError instead."""
        handle = (self.directory / "ledger.lock").open("a+b")
        try:
            try:
                import fcntl

                fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except ImportError:  # Windows (development machines)
                import msvcrt

                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        except OSError as exc:
            handle.close()
            raise OSError(f"another sentinel process is already writing {self.path}") from exc
        return handle

    def close(self) -> None:
        """Release the writer lock (the daemon holds it until it exits)."""
        self._lock_handle.close()

    def _load_tail(self) -> None:
        if not self.path.exists():
            return
        data = self.path.read_bytes()
        if not data:
            return
        self._needs_newline = not data.endswith(b"\n")  # a power cut mid-write
        for line in reversed(data.decode("ascii", "replace").splitlines()):
            try:
                record = json.loads(line)
                self.seq, self.head, self.last = int(record["seq"]), str(record["hash"]), record
                return
            except (ValueError, KeyError, TypeError):
                continue  # skip a damaged last line; --verify-logs reports it

    def append(self, fields: dict) -> dict:
        """Add one record. Raises OSError if it could not be made durable."""
        with self._lock:
            record = {"seq": self.seq + 1, "prev": self.head, **fields}
            record["hash"] = chain_hash(record)
            line = canonical(record) + "\n"
            with self.path.open("ab") as handle:
                if self._needs_newline:
                    handle.write(b"\n")
                handle.write(line.encode("ascii"))
                handle.flush()
                os.fsync(handle.fileno())
            self._needs_newline = False
            self.seq, self.head, self.last = record["seq"], record["hash"], record
            return record

    # -- run continuity ----------------------------------------------------

    def touch_heartbeat(self, now_utc: datetime | None = None) -> None:
        """Record 'still alive' so an unexpected stop can be dated to within a minute or so."""
        now_utc = now_utc or datetime.now(timezone.utc)
        tmp = self.directory / (HEARTBEAT_NAME + ".tmp")
        tmp.write_text(json.dumps({"utc": now_utc.isoformat()}), encoding="ascii")
        os.replace(tmp, self.directory / HEARTBEAT_NAME)

    def _read_heartbeat(self) -> datetime | None:
        try:
            return parse_utc(json.loads((self.directory / HEARTBEAT_NAME).read_text("ascii"))["utc"])
        except (OSError, ValueError, KeyError, TypeError):
            return None

    def previous_run(self, stopped_event: str) -> PreviousRun:
        """Call before logging this run's first row."""
        if self.last is None:
            return PreviousRun("none", None)
        last_utc = parse_utc(self.last["utc"])
        if self.last.get("event") == stopped_event:
            return PreviousRun("clean", last_utc)
        beat = self._read_heartbeat()
        return PreviousRun("unexpected", max(last_utc, beat) if beat else last_utc)
