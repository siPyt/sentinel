"""The learning itself: baselines, drift, trends, sister-door comparison, anomalies.

Everything here is explainable on purpose: each finding carries the numbers behind it
(what was seen, what is usual). Models only ever *read* history; nothing is written back
to the logs. Findings are held back until there is enough data (see ``PHASES``) so a
brand-new installation does not raise false alarms.
"""

from __future__ import annotations

import math
import statistics
import warnings
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta

import numpy as np

from sentinel.learn.features import (
    FEATURE_LABELS,
    FEATURES,
    Dataset,
    DayStats,
    ReleaseDayStats,
    day_features,
    NIGHT_HOURS,
)

BASELINE_DAYS = 14  # first baselines
ANOMALY_DAYS = 30  # anomaly detection and drift watch
TREND_DAYS = 60  # trends, sister-door and traffic comparisons
RECENT_DAYS = 7  # how far back a finding may point
_FLOORS = np.array([1.0, 1.0, 2.0, 0.05, 0.5, 0.5, 0.05])  # smallest believable spread per feature
SEVERITY_ORDER = {"alert": 0, "watch": 1, "info": 2}


@dataclass
class Analysis:
    progress: dict
    findings: list[dict]
    baselines: list[dict]
    model_info: dict
    scores: dict[str, float] = field(default_factory=dict)  # door -> anomaly score of the latest day
    release_baselines: list[dict] = field(default_factory=list)
    release_model_info: dict = field(default_factory=dict)


def _phase(days: int, target: int) -> tuple[str, str]:
    if days >= target:
        return "complete", "Six-month study complete. Keep running for ongoing monitoring."
    if days >= TREND_DAYS:
        return "trends", f"Trends, sister-door and traffic comparisons active. Six-month study at day {target}."
    if days >= ANOMALY_DAYS:
        return "anomaly", f"Anomaly detection and drift watch active. Trends start at day {TREND_DAYS}."
    if days >= BASELINE_DAYS:
        return "baseline", f"Baselines forming. Anomaly detection starts at day {ANOMALY_DAYS}."
    return "collecting", f"Collecting data. Baselines start at day {BASELINE_DAYS}."


def _robust_z(matrix: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Each feature measured against the door's own usual (median, MAD). Returns (z, median)."""
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", category=RuntimeWarning)
        med = np.nanmedian(matrix, axis=0)
        mad = np.nanmedian(np.abs(matrix - med), axis=0) * 1.4826
    scale = np.maximum(np.nan_to_num(mad, nan=0.0), _FLOORS)
    z = np.nan_to_num((matrix - med) / scale, nan=0.0)
    return np.clip(z, -10, 10), np.nan_to_num(med, nan=0.0)


def _door_days(ds: Dataset, door: str, days: list[date]) -> list[DayStats]:
    per_day = ds.doors.get(door, {})
    return [per_day.get(d, DayStats()) for d in days]


def _series(stats: list[DayStats], pick) -> list[tuple[int, float]]:
    out = []
    for i, s in enumerate(stats):
        value = pick(s)
        if value is not None and not math.isnan(value):
            out.append((i, value))
    return out


def _median_open(s: DayStats) -> float | None:
    return statistics.median(s.open_s) if len(s.open_s) >= 3 else None


def _timeout_rate(s: DayStats) -> float | None:
    return s.timeouts / s.openings if s.openings >= 3 else None


def _fmt(value: float) -> str:
    return f"{value:.0f}" if abs(value) >= 10 else f"{value:.1f}"


def _finding(door, airlock, kind, severity, title, evidence, suggest, when: date | None) -> dict:
    probable_causes = {
        "drift_open_time": [
            "Door closer, hinge or latch wear increasing resistance.",
            "A switch or wiring problem changing the recorded open/close time.",
            "A sustained change in staffing, cleaning or traffic patterns.",
        ],
        "anomalous_day": [
            "An abnormal operating event combined several unusual door conditions.",
            "A sticking closer, failing position switch or temporary blockage.",
            "Planned maintenance, cleaning or an unusual traffic surge.",
        ],
        "trend_open_time": [
            "Progressive closer, hinge, seal or latch degradation.",
            "Dust/debris or misalignment increasing the door travel time.",
            "A gradual operational change rather than a physical fault.",
        ],
        "trend_timeouts": [
            "Door is sticking, propped open or used for staged transfers.",
            "Closer force or latch engagement is deteriorating.",
            "Magnetic interlock or operator workflow changed.",
        ],
        "slower_than_peers": [
            "Door-specific closer, hinge, seal or latch condition.",
            "Position-switch placement or intermittent wiring fault.",
            "Different use pattern for this side of the airlock.",
        ],
        "unusual_traffic": [
            "Planned cleaning, maintenance, shift change or exceptional transfer activity.",
            "Switch chatter counted as real door transitions.",
            "Unexpected access requiring operational review.",
        ],
        "emergency_release_burst": [
            "Repeated legitimate emergency/maintenance operation.",
            "Button contact chatter or a wiring fault.",
            "PLC latch reset behavior or safety-control issue.",
        ],
        "unusual_release_pattern": [
            "Unplanned use at an unusual time or frequency.",
            "Sticky/bouncing pushbutton contacts or intermittent field wiring.",
            "Related interlock/door behavior or PLC latch reset problem.",
        ],
    }.get(kind, ["Confirm the recorded event against site operations and equipment condition."])
    return {
        "door": door, "airlock": airlock, "kind": kind, "severity": severity, "title": title,
        "evidence": evidence, "probable_causes": probable_causes, "suggest": suggest,
        "date": when.isoformat() if when else "",
    }


def _ewma_drift(values: list[float]) -> tuple[float, float, float] | None:
    """EWMA control chart: (current level, usual level, control limit), or None if too short."""
    base = values[:-10]
    if len(values) < 21 or len(base) < 14:
        return None
    usual = statistics.median(base)
    mad = statistics.median(abs(v - usual) for v in base) * 1.4826
    sigma = max(mad, 0.5, 0.05 * usual)
    lam, level = 0.2, usual
    for value in values:
        level = lam * value + (1 - lam) * level
    return level, usual, 3 * sigma * math.sqrt(lam / (2 - lam))


def analyze(ds: Dataset, today: date, now: datetime, target_days: int = 180) -> Analysis:
    days = ds.complete_days(today)
    count = len(days)
    stage, phase_text = _phase(count, target_days)
    progress = {
        "days": count, "target_days": target_days, "percent": min(100, round(100 * count / target_days)),
        "stage": stage, "phase": phase_text, "doors": len(ds.doors), "records": ds.records,
        "ledger_head": ds.head[:12], "first_day": ds.first_day.isoformat() if ds.first_day else "",
        "last_run": now.isoformat(timespec="seconds"),
        "release_points": len(ds.releases),
    }
    model_info: dict = {"trained": None, "note": "Not trained yet: needs 30 days of data."}
    findings: list[dict] = []
    scores: dict[str, float] = {}
    baselines = _baselines(ds, days)
    release_baselines = _release_baselines(ds, days)
    if count < BASELINE_DAYS:
        return Analysis(
            progress,
            findings,
            baselines,
            model_info,
            release_baselines=release_baselines,
            release_model_info={"trained": None, "note": "Needs 30 days of monitored emergency-release history."},
        )

    recent = set(days[-RECENT_DAYS:])
    if count >= ANOMALY_DAYS:
        findings += _drift_findings(ds, days)
        anomalies, model_info, scores = _anomaly_findings(ds, days, recent, now)
        findings += anomalies
        release_findings, release_model_info = _release_anomaly_findings(ds, days, recent, now)
        findings += release_findings
        findings += _release_burst_findings(ds, days)
    else:
        release_model_info = {"trained": None, "note": "Needs 30 days of monitored emergency-release history."}
    if count >= TREND_DAYS:
        findings += _trend_findings(ds, days)
        findings += _peer_findings(ds, days)
        findings += _traffic_findings(ds, days)
    findings.sort(key=lambda f: (SEVERITY_ORDER[f["severity"]], f["door"]))
    return Analysis(
        progress, findings, baselines, model_info, scores, release_baselines, release_model_info
    )


def _baselines(ds: Dataset, days: list[date]) -> list[dict]:
    rows = []
    last30, last7 = days[-30:], days[-7:]
    for door in sorted(ds.doors):
        stats = _door_days(ds, door, days)
        weekday = [s.openings for d, s in zip(days, stats) if d.weekday() < 5]
        weekend = [s.openings for d, s in zip(days, stats) if d.weekday() >= 5]
        opens = [x for s in stats for x in s.open_s]
        per_day = ds.doors[door]
        week = _door_days(ds, door, last7)
        recent_open = [x for s in week for x in s.open_s]
        rows.append({
            "door": door, "airlock": ds.airlock.get(door, ""), "days": len(days),
            "openings_per_weekday": round(statistics.median(weekday), 1) if weekday else 0.0,
            "openings_per_weekend_day": round(statistics.median(weekend), 1) if weekend else 0.0,
            "median_open_s": round(statistics.median(opens), 1) if opens else None,
            "p95_open_s": round(sorted(opens)[min(len(opens) - 1, int(0.95 * len(opens)))], 1) if opens else None,
            "timeout_rate": round(sum(s.timeouts for s in stats) / max(1, sum(s.openings for s in stats)), 3),
            "last7_median_open_s": round(statistics.median(recent_open), 1) if recent_open else None,
            "last7_openings_per_day": round(sum(s.openings for s in week) / max(1, len(last7)), 1),
            "chatter_30d": sum(per_day.get(d, DayStats()).chatter for d in last30),
            "interlock_30d": sum(per_day.get(d, DayStats()).interlock for d in last30),
        })
    return rows


def _release_baselines(ds: Dataset, days: list[date]) -> list[dict]:
    rows = []
    last30 = days[-30:]
    for release in sorted(ds.releases):
        per_day = ds.releases[release]
        counts = [per_day.get(day, ReleaseDayStats()).activations for day in last30]
        active_days = [day for day in last30 if per_day.get(day, ReleaseDayStats()).activations]
        hours = [
            hour
            for day in last30
            for hour, count in enumerate(
                per_day.get(day, ReleaseDayStats()).hourly
            )
            for _ in range(count)
        ]
        rows.append(
            {
                "release": release,
                "airlock": ds.release_airlock.get(release, ""),
                "days": len(last30),
                "activations_30d": sum(counts),
                "median_daily": round(statistics.median(counts), 2) if counts else 0.0,
                "busiest_day": max(counts, default=0),
                "active_days_30d": len(active_days),
                "night_share": round(
                    sum(hour in NIGHT_HOURS for hour in hours) / len(hours), 3
                )
                if hours
                else None,
                "last_activation": max(active_days).isoformat() if active_days else "",
            }
        )
    return rows


def _release_burst_findings(ds: Dataset, days: list[date]) -> list[dict]:
    """Flag repeated emergency-release use with a robust sparse-event baseline."""
    findings = []
    history, recent = days[-37:-7], days[-7:]
    if len(history) < 14:
        return findings
    for release in sorted(ds.releases):
        per_day = ds.releases[release]
        usual = [per_day.get(day, ReleaseDayStats()).activations for day in history]
        median = statistics.median(usual)
        mad = statistics.median(abs(value - median) for value in usual) * 1.4826
        limit = median + max(2.0, 4.0 * mad)
        for day in recent:
            count = per_day.get(day, ReleaseDayStats()).activations
            if count >= 2 and count > limit:
                findings.append(
                    _finding(
                        release,
                        ds.release_airlock.get(release, ""),
                        "emergency_release_burst",
                        "watch",
                        f"Repeated emergency releases on {day:%d-%b}",
                        (
                            f"{count} activation(s) in one day; usual daily count "
                            f"was about {_fmt(median)} over the preceding 30 monitored days."
                        ),
                        (
                            "Confirm the activations were expected and inspect the "
                            "door controls, interlocks and emergency hardware."
                        ),
                        day,
                    )
                )
    return findings


def _release_features(ds: Dataset, release: str, days: list[date]) -> np.ndarray:
    """Sparse-event point-day features for a separate multivariate release model."""
    per_day = ds.releases.get(release, {})
    rows = []
    since = 30.0
    recent_counts: list[int] = []
    for day in days:
        stats = per_day.get(day, ReleaseDayStats())
        count = stats.activations
        recent_counts.append(count)
        seven_day = sum(recent_counts[-7:])
        if count:
            since = 0.0
        else:
            since = min(30.0, since + 1.0)
        rows.append(
            [
                float(count),
                float(seven_day),
                float(since),
                sum(stats.hourly[hour] for hour in NIGHT_HOURS) / count if count else 0.0,
                1.0 if count else 0.0,
            ]
        )
    return np.array(rows, dtype=float)


def _release_anomaly_findings(
    ds: Dataset, days: list[date], recent: set[date], now: datetime
) -> tuple[list[dict], dict]:
    """IsolationForest for multivariate release patterns, separate from door traffic."""
    from sklearn import __version__ as sk_version
    from sklearn.ensemble import IsolationForest

    releases = sorted(ds.releases)
    matrices = {release: _release_features(ds, release, days) for release in releases}
    total_activations = sum(matrix[:, 0].sum() for matrix in matrices.values())
    info = {
        "trained": None,
        "note": "No emergency-release activations yet; learning remains in baseline mode.",
    }
    if not releases or total_activations == 0:
        return [], info
    stacked = np.vstack(list(matrices.values()))
    z, med = _robust_release_z(stacked)
    if len(stacked) < 200:
        info["note"] = "Not enough release-point days to train yet."
        return [], info
    forest = IsolationForest(
        n_estimators=150,
        contamination=0.015,
        random_state=17,
    ).fit(z)
    scores = forest.decision_function(z)
    threshold = float(np.percentile(scores, 1.5))
    info = {
        "trained": now.isoformat(timespec="seconds"),
        "model": "IsolationForest",
        "n_estimators": 150,
        "contamination": 0.015,
        "training_point_days": int(len(stacked)),
        "release_points": len(releases),
        "features": [
            "daily activations",
            "rolling 7-day activations",
            "days since activation",
            "night activation share",
            "active day",
        ],
        "scikit_learn": sk_version,
        "ledger_records": ds.records,
    }
    findings = []
    offset = 0
    for release in releases:
        matrix = matrices[release]
        point_z = z[offset: offset + len(days)]
        point_scores = scores[offset: offset + len(days)]
        offset += len(days)
        flagged = [
            i
            for i, day in enumerate(days)
            if day in recent
            and matrix[i, 0] > 0
            and point_scores[i] < threshold
            and float(np.max(np.abs(point_z[i]))) >= 3.0
        ]
        if not flagged:
            continue
        index = flagged[-1]
        reasons = []
        labels = (
            "activations today",
            "activations in 7 days",
            "days since prior activation",
            "night activation share",
        )
        for feature, label in zip(range(4), labels):
            if abs(point_z[index, feature]) >= 3.0:
                reasons.append(
                    f"{label}: {_fmt(matrix[index, feature])} "
                    f"(usually {_fmt(med[feature])})"
                )
        findings.append(
            _finding(
                release,
                ds.release_airlock.get(release, ""),
                "unusual_release_pattern",
                "watch",
                f"Unusual emergency-release pattern on {days[index]:%d-%b}",
                "; ".join(reasons) or "Activation timing differed from its learned pattern.",
                (
                    "Verify the use was legitimate, inspect the emergency button/contacts, "
                    "and compare related door and PLC events."
                ),
                days[index],
            )
        )
    return findings, info


def _robust_release_z(matrix: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    med = np.median(matrix, axis=0)
    mad = np.median(np.abs(matrix - med), axis=0) * 1.4826
    floors = np.array([0.5, 1.0, 1.0, 0.05, 0.25])
    scale = np.maximum(mad, floors)
    return np.clip((matrix - med) / scale, -10.0, 10.0), med


def _drift_findings(ds: Dataset, days: list[date]) -> list[dict]:
    out = []
    for door in sorted(ds.doors):
        stats = _door_days(ds, door, days)
        values = [v for _, v in _series(stats, _median_open)]
        drift = _ewma_drift(values)
        if drift is None:
            continue
        level, usual, limit = drift
        change = level - usual
        if abs(change) > limit and usual > 0 and abs(change) / usual >= 0.25:
            up = change > 0
            out.append(_finding(
                door, ds.airlock.get(door, ""), "drift_open_time", "watch" if up else "info",
                "Open times have drifted " + ("longer" if up else "shorter"),
                f"Typical open time moved from {_fmt(usual)} s to {_fmt(level)} s ({change / usual:+.0%}).",
                "Check the door closer, hinges and latch." if up else "Confirm nothing changed in how the door is used.",
                days[-1],
            ))
    return out


def _anomaly_findings(ds: Dataset, days: list[date], recent: set[date], now: datetime):
    from sklearn import __version__ as sk_version
    from sklearn.ensemble import IsolationForest

    per_door: dict[str, tuple[np.ndarray, np.ndarray, np.ndarray]] = {}
    for door in sorted(ds.doors):
        matrix = np.array(
            [day_features(s, ds.coverage(d)) for d, s in zip(days, _door_days(ds, door, days))], dtype=float
        )
        z, med = _robust_z(matrix)
        per_door[door] = (matrix, z, med)
    stacked = np.vstack([z for _, z, _ in per_door.values()])
    info: dict = {"trained": None, "note": "Not enough door-days to train yet."}
    if len(stacked) < 200:
        return [], info, {}
    forest = IsolationForest(n_estimators=200, contamination="auto", random_state=0).fit(stacked)
    threshold = float(np.percentile(forest.decision_function(stacked), 1.5))
    info = {
        "trained": now.isoformat(timespec="seconds"), "model": "IsolationForest", "n_estimators": 200,
        "training_door_days": int(len(stacked)), "doors": len(per_door), "features": list(FEATURES),
        "flag_threshold_score": round(threshold, 4), "scikit_learn": sk_version, "numpy": np.__version__,
        "ledger_records": ds.records, "ledger_head": ds.head[:12],
    }
    findings, scores = [], {}
    for door, (matrix, z, med) in per_door.items():
        door_scores = forest.decision_function(z)
        scores[door] = round(float(door_scores[-1]), 4)
        flagged = [
            i for i, d in enumerate(days)
            if d in recent and door_scores[i] < threshold and float(np.max(np.abs(z[i]))) >= 3.5
        ]
        if not flagged:
            continue
        i = flagged[-1]
        reasons = []
        for j in np.argsort(-np.abs(z[i]))[:3]:
            if abs(z[i][j]) >= 3.0:
                reasons.append(
                    f"{FEATURE_LABELS[FEATURES[j]]}: {_fmt(float(matrix[i][j]))} (usually {_fmt(float(med[j]))})"
                )
        findings.append(_finding(
            door, ds.airlock.get(door, ""), "anomalous_day", "alert" if len(flagged) >= 2 else "watch",
            f"Unusual day on {days[i]:%d-%b}", "; ".join(reasons) or "Several numbers differ from its normal.",
            "Look at that day on the Events sheet and check the door and its switch.", days[i],
        ))
    return findings, info, scores


def _trend_findings(ds: Dataset, days: list[date]) -> list[dict]:
    from scipy.stats import kendalltau, theilslopes

    out = []
    window = days[-56:]
    for door in sorted(ds.doors):
        stats = _door_days(ds, door, window)
        points = _series(stats, _median_open)
        if len(points) >= 28:
            x = np.array([p[0] for p in points], dtype=float)
            y = np.array([p[1] for p in points], dtype=float)
            slope = float(theilslopes(y, x)[0])
            p_value = float(kendalltau(x, y).pvalue)
            usual = float(np.median(y))
            per_month = slope * 30 / usual if usual > 0 else 0.0
            if p_value < 0.01 and abs(per_month) >= 0.2:
                up = per_month > 0
                out.append(_finding(
                    door, ds.airlock.get(door, ""), "trend_open_time", "watch" if per_month >= 0.3 else "info",
                    "Open times are trending " + ("up" if up else "down"),
                    f"Steady change of about {per_month:+.0%} per month over the last {len(window)} days "
                    f"(from about {_fmt(usual)} s).",
                    "Plan an inspection of the closer and hinges." if up else "Confirm nothing changed in use.",
                    days[-1],
                ))
        rates = _series(stats, _timeout_rate)
        if len(rates) >= 28:
            x = np.array([p[0] for p in rates], dtype=float)
            y = np.array([p[1] for p in rates], dtype=float)
            recent_rate = float(np.mean(y[-14:]))
            if kendalltau(x, y).pvalue < 0.01 and y[-14:].mean() > y[:14].mean() and recent_rate >= 0.1:
                out.append(_finding(
                    door, ds.airlock.get(door, ""), "trend_timeouts", "watch",
                    "More openings are timing out",
                    f"About {recent_rate:.0%} of recent openings ran past the timer, and the share is rising.",
                    "Check whether the door is sticking or is being propped open.", days[-1],
                ))
    return out


def _peer_findings(ds: Dataset, days: list[date]) -> list[dict]:
    out, window = [], days[-28:]
    by_airlock: dict[str, list[str]] = {}
    for door, label in ds.airlock.items():
        by_airlock.setdefault(label, []).append(door)
    medians = {}
    for door in ds.doors:
        values = [x for s in _door_days(ds, door, window) for x in s.open_s]
        if len(values) >= 30:
            medians[door] = statistics.median(values)
    for label, members in by_airlock.items():
        for door in members:
            peers = [medians[m] for m in members if m != door and m in medians]
            if door in medians and peers:
                usual = statistics.median(peers)
                if usual > 0 and medians[door] >= 1.8 * usual and medians[door] - usual >= 5:
                    out.append(_finding(
                        door, label, "slower_than_peers", "watch", "Slower than its sister doors",
                        f"Typical open time {_fmt(medians[door])} s versus {_fmt(usual)} s for the other doors "
                        f"in {label} over the last {len(window)} days.",
                        "Compare it with its sister doors; look for a sticking closer or latch.", days[-1],
                    ))
    return out


def _traffic_findings(ds: Dataset, days: list[date]) -> list[dict]:
    history, latest = days[:-3], days[-3:]
    if len(history) < 28:
        return []
    usual: dict[tuple[int, int], list[int]] = {}
    for d in history:
        for hour, count in enumerate(ds.fleet_hourly(d)):
            usual.setdefault((d.weekday(), hour), []).append(count)
    hits = []
    for d in latest:
        for hour, count in enumerate(ds.fleet_hourly(d)):
            samples = usual.get((d.weekday(), hour), [])
            if len(samples) < 4:
                continue
            med = statistics.median(samples)
            mad = statistics.median(abs(v - med) for v in samples) * 1.4826
            if count >= 8 and count >= med + max(5 * mad, 8):
                hits.append((count - med, d, hour, count, med))
    hits.sort(reverse=True)
    return [
        _finding(
            "(all doors)", "", "unusual_traffic", "info", f"Unusual activity on {d:%a %d-%b} at {hour:02d}:00",
            f"{count} door openings in that hour (usually about {_fmt(med)}).",
            "Check whether the activity was expected (cleaning, maintenance, a shift change).", d,
        )
        for _, d, hour, count, med in hits[:3]
    ]
