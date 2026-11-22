"""T-030: summarize a saved hub log from the 72 h soak. Offline and read-only; stdlib only.

    python3 tools/soak_summary.py hub.log [--notes notes.csv] [--json out.json]

`hub.log` is `docker logs piotr-voice-hub-hub-1` output (the hub's LOG_FORMAT, times in UTC).
Everything is reported in Europe/Warsaw time. The optional notes file is Maciej's CSV, with
local (Europe/Warsaw) times and a header row:

    time,event,satellite
    2026-11-23 18:00,tv_on,
    2026-11-23 18:05,wake,voice-pe-02
    2026-11-23 22:30,tv_off,

`tv_on`/`tv_off` mark the living-room TV (a `tv_on` with no `tv_off` runs to the end of the
log). A `wake` is a real wake phrase Maciej said. It is a hit when that satellite accepted
within WAKE_MATCH_SECONDS of the noted time. Every accept that matches no noted wake counts as
a false accept. A day (local date) with no notes at all has TV state unknown and no real-wake
figures. Without a notes file every accept is a false-accept candidate.

Exit 0 after printing the summary; 2 if the log is missing or has no hub log lines.
"""

import argparse
import csv
import json
import re
import statistics
import sys
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

LOCAL = ZoneInfo("Europe/Warsaw")
WAKE_MATCH_SECONDS = 120.0  # noted times are rough
PROCESSING_LIMIT_SECONDS = 35.0  # reply_timeout 30 s plus the cue (plan criterion 2)

LINE = re.compile(
    r"^(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}),(\d{3}) (DEBUG|INFO|WARNING|ERROR|CRITICAL) "
    r"(\S+?)(?:\[([^\]]+)\])?: (.*)$"
)
SEGMENT = re.compile(r"^segment .* candidate=(True|False) wake=(True|False)")
COUNTED = {  # name: (logger suffix, message pattern), per satellite
    "wake_alone": ("session", re.compile(r"^wake word alone")),
    "no_request": ("session", re.compile(r"^no request within")),
    "reply_timeout": ("session", re.compile(r"^reply timeout")),
    "connected": ("esphome", re.compile(r"^connected to ")),
    "still_busy": ("esphome", re.compile(r"still busy|reported no change within")),
    "lacks_services": ("esphome", re.compile(r"^firmware lacks optional services")),
}
PROCESSING_END = re.compile(r"^(reply timeout|released|speaking |stop requested)")
UNAVAILABLE = re.compile(r"^unavailable: (.*); re-arming in ")
VERSION = re.compile(r"esphome_version=(\S+)")


@dataclass
class Satellite:
    counts: Counter[str] = field(default_factory=Counter)
    accepts: list[datetime] = field(default_factory=list)
    latencies: dict[datetime, float] = field(default_factory=dict)  # by accept time
    versions: set[str] = field(default_factory=set)
    connects: list[datetime] = field(default_factory=list)
    unavailable_at: list[datetime] = field(default_factory=list)
    stuck: list[datetime] = field(default_factory=list)
    hourly: dict[str, Counter[str]] = field(default_factory=lambda: defaultdict(Counter))
    _accepted_at: datetime | None = None
    _cue_pending: datetime | None = None


@dataclass
class Summary:
    first: datetime | None = None
    last: datetime | None = None
    lines: int = 0
    satellites: dict[str, Satellite] = field(default_factory=lambda: defaultdict(Satellite))
    hub_starts: list[datetime] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    tracebacks: int = 0


def local(moment: datetime) -> datetime:
    return moment.astimezone(LOCAL)


def hour_key(moment: datetime) -> str:
    return local(moment).strftime("%Y-%m-%d %H:00")


def parse_log(path: Path) -> Summary:
    summary = Summary()
    with path.open(encoding="utf-8", errors="replace") as lines:
        for raw in lines:
            line = raw.rstrip("\n")
            if line.startswith("Traceback (most recent call last)"):
                summary.tracebacks += 1
                continue
            match = LINE.match(line)
            if match is None:
                continue
            stamp, millis, level, logger, name, message = match.groups()
            moment = datetime.strptime(stamp, "%Y-%m-%d %H:%M:%S").replace(
                microsecond=int(millis) * 1000, tzinfo=UTC
            )
            summary.lines += 1
            summary.first = summary.first or moment
            summary.last = moment
            if level in ("ERROR", "CRITICAL"):
                summary.errors.append(f"{local(moment):%Y-%m-%d %H:%M:%S} {level} {message}")
            if message.startswith("hub running:"):
                summary.hub_starts.append(moment)
            if name is not None:
                observe(summary.satellites[name], logger, moment, message)
    for satellite in summary.satellites.values():
        close_processing(satellite, None)
    return summary


def close_processing(satellite: Satellite, moment: datetime | None) -> None:
    started = satellite._accepted_at
    if started is None:
        return
    if moment is None or (moment - started).total_seconds() > PROCESSING_LIMIT_SECONDS:
        satellite.stuck.append(started)
    satellite._accepted_at = None


def observe(satellite: Satellite, logger: str, moment: datetime, message: str) -> None:
    hour = satellite.hourly[hour_key(moment)]
    if logger.endswith("session"):
        if (segment := SEGMENT.match(message)) is not None:
            satellite.counts["segments"] += 1
            hour["segments"] += 1
            candidate, wake = segment.groups()
            if wake == "True":
                satellite.counts["accepts"] += 1
                hour["accepts"] += 1
                satellite.accepts.append(moment)
                satellite._accepted_at = moment
                satellite._cue_pending = moment
            elif candidate == "True":
                satellite.counts["candidates_not_accepted"] += 1
            return
        if PROCESSING_END.match(message) and satellite._accepted_at is not None:
            close_processing(satellite, moment)
        if (unavailable := UNAVAILABLE.match(message)) is not None:
            reason = unavailable.group(1)
            kind = "liveness" if "stalled stream or digital silence" in reason else "unavailable"
            kind = "connect_failed" if reason.startswith("connect to ") else kind
            satellite.counts[kind] += 1
            hour[kind] += 1
            satellite.unavailable_at.append(moment)
            satellite._accepted_at = satellite._cue_pending = None
            return
    if logger.endswith("esphome") and message.startswith("played cue utterance_accepted"):
        if satellite._cue_pending is not None:
            pending = satellite._cue_pending
            satellite.latencies[pending] = (moment - pending).total_seconds()
            satellite._cue_pending = None
    for name, (suffix, pattern) in COUNTED.items():
        if logger.endswith(suffix) and pattern.search(message):
            satellite.counts[name] += 1
            if name == "connected":
                satellite.connects.append(moment)
                hour["connected"] += 1
                if (version := VERSION.search(message)) is not None:
                    satellite.versions.add(version.group(1))


@dataclass
class Notes:
    days: set[date]
    tv: list[tuple[datetime, datetime]]
    wakes: list[tuple[datetime, str]]


def parse_notes(path: Path, end: datetime) -> Notes:
    days: set[date] = set()
    tv: list[tuple[datetime, datetime]] = []
    wakes: list[tuple[datetime, str]] = []
    tv_on: datetime | None = None
    with path.open(encoding="utf-8", newline="") as file:
        rows = sorted(csv.DictReader(file), key=lambda row: row["time"].strip())
    for row in rows:
        moment = datetime.strptime(row["time"].strip(), "%Y-%m-%d %H:%M").replace(tzinfo=LOCAL)
        event = row["event"].strip()
        days.add(moment.date())
        if event == "tv_on":
            tv_on = tv_on or moment
        elif event == "tv_off":
            if tv_on is not None:
                tv.append((tv_on, moment))
            tv_on = None
        elif event == "wake":
            wakes.append((moment, (row.get("satellite") or "").strip()))
        else:
            raise ValueError(f"unknown notes event {event!r}")
    if tv_on is not None:
        tv.append((tv_on, end))
    return Notes(days, tv, wakes)


def overlap_hours(
    intervals: list[tuple[datetime, datetime]], start: datetime, end: datetime
) -> float:
    seconds = 0.0
    for begin, finish in intervals:
        seconds += max(0.0, (min(finish, end) - max(begin, start)).total_seconds())
    return seconds / 3600


def latency_stats(values: list[float]) -> dict[str, Any] | None:
    if not values:
        return None
    ordered = sorted(values)
    return {
        "n": len(ordered),
        "min": round(ordered[0], 3),
        "median": round(statistics.median(ordered), 3),
        "max": round(ordered[-1], 3),
    }


def classify(summary: Summary, notes: Notes | None) -> dict[str, Any]:
    """False accepts per satellite (by TV state), the real-wake hit rate and latency."""
    assert summary.first is not None and summary.last is not None
    real: set[tuple[str, datetime]] = set()
    hits: list[dict[str, Any]] = []
    if notes is not None:
        for noted, name in notes.wakes:
            accepts = summary.satellites[name].accepts if name in summary.satellites else []
            near = [
                moment
                for moment in accepts
                if abs((moment - noted).total_seconds()) <= WAKE_MATCH_SECONDS
                and (name, moment) not in real
            ]
            if near:
                real.add((name, near[0]))
            hits.append({"time": f"{noted:%Y-%m-%d %H:%M}", "satellite": name, "hit": bool(near)})
    real_latency = [
        summary.satellites[name].latencies[moment]
        for name, moment in real
        if moment in summary.satellites[name].latencies
    ]
    tv_hours = None if notes is None else overlap_hours(notes.tv, summary.first, summary.last)
    result: dict[str, Any] = {
        "tv_hours": tv_hours,
        "real_wakes": hits,
        "real_latency": latency_stats(real_latency),
        "false_accepts": {},
    }
    for name, satellite in summary.satellites.items():
        counts = Counter[str]()
        for moment in satellite.accepts:
            if (name, moment) in real:
                continue
            when = local(moment)
            if notes is None or when.date() not in notes.days:
                counts["tv_unknown"] += 1
            elif any(begin <= when <= end for begin, end in notes.tv):
                counts["tv_on"] += 1
            else:
                counts["tv_off"] += 1
            satellite.hourly[hour_key(moment)]["false_accepts"] += 1
        result["false_accepts"][name] = dict(counts)
    return result


def report(summary: Summary, classified: dict[str, Any]) -> dict[str, Any]:
    assert summary.first is not None and summary.last is not None
    hours = (summary.last - summary.first).total_seconds() / 3600
    tv_hours = classified["tv_hours"]
    satellites: dict[str, Any] = {}
    for name in sorted(summary.satellites):
        satellite = summary.satellites[name]
        false = classified["false_accepts"].get(name, {})
        satellites[name] = {
            "counts": dict(sorted(satellite.counts.items())),
            "reconnects": max(0, len(satellite.connects) - 1),
            "esphome_versions": sorted(satellite.versions),
            "false_accepts": false,
            "false_accepts_per_hour": round(sum(false.values()) / hours, 4) if hours else None,
            "false_accepts_per_tv_hour": round(false.get("tv_on", 0) / tv_hours, 4)
            if tv_hours
            else None,
            "stuck_processing": [
                f"{local(moment):%Y-%m-%d %H:%M:%S}" for moment in satellite.stuck
            ],
            "accept_latency_s": latency_stats(list(satellite.latencies.values())),
            "hourly": {hour: dict(counts) for hour, counts in sorted(satellite.hourly.items())},
        }
    wakes = classified["real_wakes"]
    return {
        "start": f"{local(summary.first):%Y-%m-%d %H:%M:%S %Z}",
        "end": f"{local(summary.last):%Y-%m-%d %H:%M:%S %Z}",
        "hours": round(hours, 2),
        "log_lines": summary.lines,
        "hub_starts": [f"{local(moment):%Y-%m-%d %H:%M:%S}" for moment in summary.hub_starts],
        "errors": summary.errors,
        "tracebacks": summary.tracebacks,
        "tv_hours": None if classified["tv_hours"] is None else round(classified["tv_hours"], 2),
        "real_wakes": {
            "noted": len(wakes),
            "hit": sum(1 for wake in wakes if wake["hit"]),
            "missed": [wake for wake in wakes if not wake["hit"]],
            "latency_s": classified["real_latency"],
        }
        if wakes
        else None,
        "satellites": satellites,
    }


def render(result: dict[str, Any]) -> str:
    out = [
        f"soak {result['start']} .. {result['end']} ({result['hours']} h, "
        f"{result['log_lines']} log lines)",
        f"hub starts in log: {len(result['hub_starts'])} {result['hub_starts']}",
        f"ERROR/CRITICAL: {len(result['errors'])}, tracebacks: {result['tracebacks']}",
        f"TV hours: {'unknown' if result['tv_hours'] is None else result['tv_hours']}",
    ]
    wakes = result["real_wakes"]
    out.append(
        "real wakes: N/A (no notes)"
        if wakes is None
        else f"real wakes: {wakes['hit']}/{wakes['noted']} accepted, latency {wakes['latency_s']}; "
        f"missed {wakes['missed']}"
    )
    out.extend(f"  {line}" for line in result["errors"])
    for name, sat in result["satellites"].items():
        out.append(f"\n{name}: versions {sat['esphome_versions']}, reconnects {sat['reconnects']}")
        out.append(f"  counts {sat['counts']}")
        out.append(
            f"  false accepts {sat['false_accepts']} ({sat['false_accepts_per_hour']}/h, "
            f"{sat['false_accepts_per_tv_hour']}/TV-hour)"
        )
        out.append(f"  accept latency {sat['accept_latency_s']}")
        out.append(f"  stuck processing {sat['stuck_processing']}")
        out.append("  hour              segm acc false live unav connf conn")
        for hour, c in sat["hourly"].items():
            out.append(
                f"  {hour}  {c.get('segments', 0):4} {c.get('accepts', 0):3} "
                f"{c.get('false_accepts', 0):5} {c.get('liveness', 0):4} "
                f"{c.get('unavailable', 0):4} {c.get('connect_failed', 0):5} "
                f"{c.get('connected', 0):4}"
            )
    return "\n".join(out)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="T-030: summarize a saved hub soak log.")
    parser.add_argument("log", type=Path, help="saved hub log (docker logs output)")
    parser.add_argument("--notes", type=Path, help="Maciej's notes CSV (time,event,satellite)")
    parser.add_argument("--json", type=Path, help="also write the summary as JSON here")
    args = parser.parse_args(argv)
    if not args.log.is_file():
        print(f"no such log file: {args.log}", file=sys.stderr)
        return 2
    summary = parse_log(args.log)
    if summary.first is None or summary.last is None:
        print(f"no hub log lines in {args.log}", file=sys.stderr)
        return 2
    if args.notes is not None and not args.notes.is_file():
        print(f"no such notes file: {args.notes}", file=sys.stderr)
        return 2
    notes = None if args.notes is None else parse_notes(args.notes, local(summary.last))
    result = report(summary, classify(summary, notes))
    print(render(result))
    if args.json is not None:
        args.json.write_text(json.dumps(result, indent=2, ensure_ascii=False) + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
