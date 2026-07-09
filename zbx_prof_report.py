#!/usr/bin/env python3
#
# Generate a self-contained HTML report from Zabbix runtime profiler logs.
#
# The server profiler prints cumulative counters per process. This tool turns
# them into per-snapshot deltas before aggregating, otherwise later snapshots
# dominate the report.

import argparse
import datetime
import html
import json
import math
import os
import random
import re
import sys
from collections import Counter, defaultdict


HEADER_RE = re.compile(
    r"^\s*(?P<pid>\d+):(?P<date>\d{8}):(?P<time>\d{6}\.\d{3}) "
    r"=== Profiling statistics for (?P<process>.*?) ==="
)

METRIC_RE = re.compile(
    r"^(?P<function>.*?)\(\) (?P<scope>processing|rwlock|mutex) : "
    r"(?:(?:busy:(?P<busy>[0-9.]+) sec)|"
    r"(?:locked:(?P<locked>\d+) holding:(?P<holding>[0-9.]+) sec waiting:(?P<waiting>[0-9.]+) sec))$"
)

TOTAL_RE = re.compile(
    r"^(?P<name>rwlocks|mutexes|locking total) : locked:(?P<locked>\d+) "
    r"holding:(?P<holding>[0-9.]+) sec waiting:(?P<waiting>[0-9.]+) sec$"
)


def parse_timestamp(date_text, time_text):
    return datetime.datetime(
        int(date_text[0:4]),
        int(date_text[4:6]),
        int(date_text[6:8]),
        int(time_text[0:2]),
        int(time_text[2:4]),
        int(time_text[4:6]),
        int(time_text[7:].ljust(6, "0")[:6]),
    )


def fmt_seconds(value):
    if value is None:
        return ""
    if abs(value) >= 100:
        return f"{value:.1f}s"
    if abs(value) >= 1:
        return f"{value:.3f}s"
    return f"{value * 1000:.3f}ms"


def pct(part, total):
    return 0.0 if total == 0 else 100.0 * part / total


def percentile(values, p):
    if not values:
        return 0.0

    values = sorted(values)
    index = (len(values) - 1) * p / 100.0
    lo = math.floor(index)
    hi = math.ceil(index)

    if lo == hi:
        return values[int(index)]

    return values[lo] * (hi - index) + values[hi] * (index - lo)


class Reservoir:
    def __init__(self, limit, rng):
        self.limit = limit
        self.rng = rng
        self.count = 0
        self.values = []

    def add(self, value):
        self.count += 1
        if self.limit <= 0:
            return
        if len(self.values) < self.limit:
            self.values.append(value)
            return

        index = self.rng.randrange(self.count)
        if index < self.limit:
            self.values[index] = value

    def percentile(self, p):
        return percentile(self.values, p)


def top_rows(rows, key, limit):
    return sorted(rows, key=key, reverse=True)[:limit]


def parse_log(path):
    snapshots = []
    current = None
    line_no = 0
    malformed = 0

    with open(path, "r", encoding="utf-8", errors="replace") as f:
        for line_no, line in enumerate(f, 1):
            line = line.rstrip("\n")

            match = HEADER_RE.match(line)
            if match is not None:
                if current is not None:
                    snapshots.append(current)

                current = {
                    "pid": int(match.group("pid")),
                    "process": match.group("process"),
                    "ts": parse_timestamp(match.group("date"), match.group("time")),
                    "metrics": {},
                    "totals": {},
                    "line": line_no,
                }
                continue

            if current is None or line == "" or line.startswith("-") or line.startswith("Total blocks:"):
                continue

            match = METRIC_RE.match(line)
            if match is not None:
                scope = match.group("scope")
                function = match.group("function")

                current["metrics"][(function, scope)] = {
                    "busy": float(match.group("busy") or 0.0),
                    "locked": int(match.group("locked") or 0),
                    "holding": float(match.group("holding") or 0.0),
                    "waiting": float(match.group("waiting") or 0.0),
                    "line": line_no,
                }
                continue

            match = TOTAL_RE.match(line)
            if match is not None:
                current["totals"][match.group("name")] = {
                    "locked": int(match.group("locked")),
                    "holding": float(match.group("holding")),
                    "waiting": float(match.group("waiting")),
                    "line": line_no,
                }
                continue

            malformed += 1

    if current is not None:
        snapshots.append(current)

    return snapshots, {"lines": line_no, "malformed": malformed}


def make_delta_row(snapshot, previous, function, scope, current_metric, previous_metric, kind):
    if scope == "processing":
        busy = current_metric["busy"] - previous_metric["busy"]
        locked = 0
        holding = 0.0
        waiting = 0.0
        values = [busy]
    else:
        busy = 0.0
        locked = current_metric["locked"] - previous_metric["locked"]
        holding = current_metric["holding"] - previous_metric["holding"]
        waiting = current_metric["waiting"] - previous_metric["waiting"]
        values = [locked, holding, waiting]

    if any(value < -1e-9 for value in values):
        return None

    return {
        "ts": snapshot["ts"],
        "pid": snapshot["pid"],
        "process": snapshot["process"],
        "function": function,
        "scope": scope,
        "kind": kind,
        "busy": max(0.0, busy),
        "locked": max(0, locked),
        "holding": max(0.0, holding),
        "waiting": max(0.0, waiting),
    }


def build_deltas(snapshots):
    previous_by_pid = {}
    rows = []
    skipped_first = 0
    skipped_negative = 0
    skipped_type_change = 0

    for snapshot in snapshots:
        previous = previous_by_pid.get(snapshot["pid"])

        if previous is None:
            skipped_first += 1
            previous_by_pid[snapshot["pid"]] = snapshot
            continue

        if previous["process"] != snapshot["process"]:
            skipped_type_change += 1
            previous_by_pid[snapshot["pid"]] = snapshot
            continue

        for key, current_metric in snapshot["metrics"].items():
            previous_metric = previous["metrics"].get(key)
            if previous_metric is None:
                continue

            row = make_delta_row(
                snapshot, previous, key[0], key[1], current_metric, previous_metric, "function"
            )
            if row is None:
                skipped_negative += 1
                continue
            rows.append(row)

        for name, current_total in snapshot["totals"].items():
            previous_total = previous["totals"].get(name)
            if previous_total is None:
                continue

            locked = current_total["locked"] - previous_total["locked"]
            holding = current_total["holding"] - previous_total["holding"]
            waiting = current_total["waiting"] - previous_total["waiting"]

            if locked < 0 or holding < -1e-9 or waiting < -1e-9:
                skipped_negative += 1
                continue

            rows.append({
                "ts": snapshot["ts"],
                "pid": snapshot["pid"],
                "process": snapshot["process"],
                "function": name,
                "scope": "summary",
                "kind": "summary",
                "busy": 0.0,
                "locked": locked,
                "holding": max(0.0, holding),
                "waiting": max(0.0, waiting),
            })

        previous_by_pid[snapshot["pid"]] = snapshot

    return rows, {
        "skipped_first": skipped_first,
        "skipped_negative": skipped_negative,
        "skipped_type_change": skipped_type_change,
    }


def empty_agg():
    return {
        "samples": 0,
        "pids": set(),
        "locked": 0,
        "busy": 0.0,
        "holding": 0.0,
        "waiting": 0.0,
        "max_busy": 0.0,
        "max_holding": 0.0,
        "max_waiting": 0.0,
        "first_ts": None,
        "last_ts": None,
    }


def add_agg(agg, row):
    agg["samples"] += 1
    agg["pids"].add(row["pid"])
    agg["locked"] += row["locked"]
    agg["busy"] += row["busy"]
    agg["holding"] += row["holding"]
    agg["waiting"] += row["waiting"]
    agg["max_busy"] = max(agg["max_busy"], row["busy"])
    agg["max_holding"] = max(agg["max_holding"], row["holding"])
    agg["max_waiting"] = max(agg["max_waiting"], row["waiting"])

    if agg["first_ts"] is None or row["ts"] < agg["first_ts"]:
        agg["first_ts"] = row["ts"]
    if agg["last_ts"] is None or row["ts"] > agg["last_ts"]:
        agg["last_ts"] = row["ts"]


def finish_agg(key, agg, fields):
    result = {field: value for field, value in zip(fields, key)}
    result.update({
        "samples": agg["samples"],
        "pid_count": len(agg["pids"]),
        "locked": agg["locked"],
        "busy": agg["busy"],
        "holding": agg["holding"],
        "waiting": agg["waiting"],
        "max_busy": agg["max_busy"],
        "max_holding": agg["max_holding"],
        "max_waiting": agg["max_waiting"],
        "avg_wait_per_lock": 0.0 if agg["locked"] == 0 else agg["waiting"] / agg["locked"],
        "avg_hold_per_lock": 0.0 if agg["locked"] == 0 else agg["holding"] / agg["locked"],
        "wait_ratio": pct(agg["waiting"], agg["waiting"] + agg["holding"]),
        "first_ts": agg["first_ts"].isoformat(sep=" ") if agg["first_ts"] else "",
        "last_ts": agg["last_ts"].isoformat(sep=" ") if agg["last_ts"] else "",
    })
    return result


def bucket_start(ts, bucket_seconds):
    epoch = int(ts.timestamp())
    return datetime.datetime.fromtimestamp(epoch - epoch % bucket_seconds)


def add_delta_to_report_state(state, row):
    if row["kind"] == "summary":
        if row["function"] == "locking total":
            add_agg(state["process_aggs"][(row["process"],)], row)
            add_agg(state["pid_aggs"][(row["process"], row["pid"])], row)
            add_agg(
                state["bucket_aggs"][(bucket_start(row["ts"], state["bucket_seconds"]), row["process"])],
                row,
            )
        return

    key = (row["process"], row["function"], row["scope"])
    add_agg(state["function_aggs"][key], row)
    state["waiting_samples"][key].add(row["waiting"])
    state["busy_samples"][key].add(row["busy"])


def account_snapshot(state, snapshot):
    state["snapshot_count"] += 1
    state["pids"].add(snapshot["pid"])
    state["snapshot_process_counts"][snapshot["process"]] += 1
    state["snapshot_pid_counts"][snapshot["process"]].add(snapshot["pid"])

    if state["first_ts"] is None or snapshot["ts"] < state["first_ts"]:
        state["first_ts"] = snapshot["ts"]
    if state["last_ts"] is None or snapshot["ts"] > state["last_ts"]:
        state["last_ts"] = snapshot["ts"]


def process_snapshot_delta(state, snapshot):
    previous_by_pid = state["previous_by_pid"]
    previous = previous_by_pid.get(snapshot["pid"])

    if previous is None:
        state["delta_stats"]["skipped_first"] += 1
        previous_by_pid[snapshot["pid"]] = snapshot
        return

    if previous["process"] != snapshot["process"]:
        state["delta_stats"]["skipped_type_change"] += 1
        previous_by_pid[snapshot["pid"]] = snapshot
        return

    for key, current_metric in snapshot["metrics"].items():
        previous_metric = previous["metrics"].get(key)
        if previous_metric is None:
            continue

        row = make_delta_row(
            snapshot, previous, key[0], key[1], current_metric, previous_metric, "function"
        )
        if row is None:
            state["delta_stats"]["skipped_negative"] += 1
            continue
        state["delta_rows"] += 1
        add_delta_to_report_state(state, row)

    for name, current_total in snapshot["totals"].items():
        previous_total = previous["totals"].get(name)
        if previous_total is None:
            continue

        locked = current_total["locked"] - previous_total["locked"]
        holding = current_total["holding"] - previous_total["holding"]
        waiting = current_total["waiting"] - previous_total["waiting"]

        if locked < 0 or holding < -1e-9 or waiting < -1e-9:
            state["delta_stats"]["skipped_negative"] += 1
            continue

        state["delta_rows"] += 1
        add_delta_to_report_state(state, {
            "ts": snapshot["ts"],
            "pid": snapshot["pid"],
            "process": snapshot["process"],
            "function": name,
            "scope": "summary",
            "kind": "summary",
            "busy": 0.0,
            "locked": locked,
            "holding": max(0.0, holding),
            "waiting": max(0.0, waiting),
        })

    previous_by_pid[snapshot["pid"]] = snapshot


def parse_log_streaming(path, bucket_seconds, sample_limit):
    rng = random.Random(1)
    state = {
        "bucket_seconds": bucket_seconds,
        "first_ts": None,
        "last_ts": None,
        "snapshot_count": 0,
        "pids": set(),
        "snapshot_process_counts": Counter(),
        "snapshot_pid_counts": defaultdict(set),
        "process_aggs": defaultdict(empty_agg),
        "function_aggs": defaultdict(empty_agg),
        "pid_aggs": defaultdict(empty_agg),
        "bucket_aggs": defaultdict(empty_agg),
        "waiting_samples": defaultdict(lambda: Reservoir(sample_limit, rng)),
        "busy_samples": defaultdict(lambda: Reservoir(sample_limit, rng)),
        "previous_by_pid": {},
        "delta_rows": 0,
        "delta_stats": {
            "skipped_first": 0,
            "skipped_negative": 0,
            "skipped_type_change": 0,
        },
    }
    current = None
    line_no = 0
    malformed = 0

    def finish_current():
        if current is None:
            return
        account_snapshot(state, current)
        process_snapshot_delta(state, current)

    with open(path, "r", encoding="utf-8", errors="replace") as f:
        for line_no, line in enumerate(f, 1):
            line = line.rstrip("\n")

            if "=== Profiling statistics for " in line:
                match = HEADER_RE.match(line)
                if match is not None:
                    finish_current()
                    current = {
                        "pid": int(match.group("pid")),
                        "process": match.group("process"),
                        "ts": parse_timestamp(match.group("date"), match.group("time")),
                        "metrics": {},
                        "totals": {},
                        "line": line_no,
                    }
                    continue

            if current is None or line == "" or line.startswith("-") or line.startswith("Total blocks:"):
                continue

            if "() " in line and " : " in line:
                match = METRIC_RE.match(line)
                if match is not None:
                    scope = match.group("scope")
                    function = match.group("function")

                    current["metrics"][(function, scope)] = {
                        "busy": float(match.group("busy") or 0.0),
                        "locked": int(match.group("locked") or 0),
                        "holding": float(match.group("holding") or 0.0),
                        "waiting": float(match.group("waiting") or 0.0),
                        "line": line_no,
                    }
                    continue

            if line.startswith(("rwlocks : ", "mutexes : ", "locking total : ")):
                match = TOTAL_RE.match(line)
                if match is not None:
                    current["totals"][match.group("name")] = {
                        "locked": int(match.group("locked")),
                        "holding": float(match.group("holding")),
                        "waiting": float(match.group("waiting")),
                        "line": line_no,
                    }
                    continue

            malformed += 1

    finish_current()

    parse_stats = {"lines": line_no, "malformed": malformed}
    return state, parse_stats


def build_report_data_from_state(path, state, parse_stats, sample_limit, limit):
    process_rows = [
        finish_agg(key, value, ["process"])
        for key, value in state["process_aggs"].items()
    ]
    function_rows = [
        finish_agg(key, value, ["process", "function", "scope"])
        for key, value in state["function_aggs"].items()
    ]
    pid_rows = [
        finish_agg(key, value, ["process", "pid"])
        for key, value in state["pid_aggs"].items()
    ]

    for row in function_rows:
        key = (row["process"], row["function"], row["scope"])
        row["p95_waiting"] = state["waiting_samples"][key].percentile(95)
        row["p95_busy"] = state["busy_samples"][key].percentile(95)

    bucket_rows = []
    for key, value in state["bucket_aggs"].items():
        row = finish_agg(key, value, ["bucket", "process"])
        row["bucket"] = row["bucket"].isoformat(sep=" ")
        bucket_rows.append(row)

    total_waiting = sum(row["waiting"] for row in process_rows)
    total_holding = sum(row["holding"] for row in process_rows)
    total_busy = sum(row["busy"] for row in function_rows if row["scope"] == "processing")

    top_waiting = top_rows(
        [row for row in function_rows if row["scope"] in ("rwlock", "mutex")],
        lambda row: row["waiting"],
        limit,
    )
    top_busy = top_rows(
        [row for row in function_rows if row["scope"] == "processing"],
        lambda row: row["busy"],
        limit,
    )
    top_avg_wait = top_rows(
        [row for row in function_rows if row["scope"] in ("rwlock", "mutex") and row["locked"] > 0],
        lambda row: row["avg_wait_per_lock"],
        limit,
    )

    first_ts = state["first_ts"]
    last_ts = state["last_ts"]
    snapshot_process_counts = state["snapshot_process_counts"]
    snapshot_pid_counts = state["snapshot_pid_counts"]

    return {
        "input": os.path.abspath(path),
        "generated_at": datetime.datetime.now().isoformat(sep=" ", timespec="seconds"),
        "bucket_seconds": state["bucket_seconds"],
        "summary": {
            "first_ts": first_ts.isoformat(sep=" ") if first_ts else "",
            "last_ts": last_ts.isoformat(sep=" ") if last_ts else "",
            "duration_seconds": 0 if not first_ts or not last_ts else (last_ts - first_ts).total_seconds(),
            "snapshots": state["snapshot_count"],
            "pids": len(state["pids"]),
            "processes": len(snapshot_process_counts),
            "delta_rows": state["delta_rows"],
            "total_waiting": total_waiting,
            "total_holding": total_holding,
            "total_busy": total_busy,
            "parse_stats": parse_stats,
            "delta_stats": state["delta_stats"],
            "p95_sample_limit": sample_limit,
        },
        "snapshot_processes": [
            {
                "process": process,
                "snapshots": count,
                "pid_count": len(snapshot_pid_counts[process]),
            }
            for process, count in snapshot_process_counts.most_common()
        ],
        "process_rows": sorted(process_rows, key=lambda row: row["waiting"], reverse=True),
        "function_rows": sorted(function_rows, key=lambda row: row["waiting"] + row["busy"], reverse=True),
        "pid_rows": sorted(pid_rows, key=lambda row: row["waiting"], reverse=True),
        "bucket_rows": sorted(bucket_rows, key=lambda row: (row["bucket"], row["process"])),
        "top_waiting": top_waiting,
        "top_busy": top_busy,
        "top_avg_wait": top_avg_wait,
    }


def build_report_data(path, snapshots, parse_stats, delta_rows, delta_stats, bucket_seconds, limit):
    if snapshots:
        first_ts = min(snapshot["ts"] for snapshot in snapshots)
        last_ts = max(snapshot["ts"] for snapshot in snapshots)
    else:
        first_ts = None
        last_ts = None

    snapshot_process_counts = Counter(snapshot["process"] for snapshot in snapshots)
    snapshot_pid_counts = defaultdict(set)
    for snapshot in snapshots:
        snapshot_pid_counts[snapshot["process"]].add(snapshot["pid"])

    process_aggs = defaultdict(empty_agg)
    function_aggs = defaultdict(empty_agg)
    pid_aggs = defaultdict(empty_agg)
    bucket_aggs = defaultdict(empty_agg)
    waiting_samples = defaultdict(list)
    busy_samples = defaultdict(list)

    for row in delta_rows:
        if row["kind"] == "summary":
            if row["function"] == "locking total":
                add_agg(process_aggs[(row["process"],)], row)
                add_agg(pid_aggs[(row["process"], row["pid"])], row)
                add_agg(bucket_aggs[(bucket_start(row["ts"], bucket_seconds), row["process"])], row)
            continue

        add_agg(function_aggs[(row["process"], row["function"], row["scope"])], row)
        waiting_samples[(row["process"], row["function"], row["scope"])].append(row["waiting"])
        busy_samples[(row["process"], row["function"], row["scope"])].append(row["busy"])

    process_rows = [
        finish_agg(key, value, ["process"])
        for key, value in process_aggs.items()
    ]
    function_rows = [
        finish_agg(key, value, ["process", "function", "scope"])
        for key, value in function_aggs.items()
    ]
    pid_rows = [
        finish_agg(key, value, ["process", "pid"])
        for key, value in pid_aggs.items()
    ]

    for row in function_rows:
        key = (row["process"], row["function"], row["scope"])
        row["p95_waiting"] = percentile(waiting_samples[key], 95)
        row["p95_busy"] = percentile(busy_samples[key], 95)

    bucket_rows = []
    for key, value in bucket_aggs.items():
        row = finish_agg(key, value, ["bucket", "process"])
        row["bucket"] = row["bucket"].isoformat(sep=" ")
        bucket_rows.append(row)

    total_waiting = sum(row["waiting"] for row in process_rows)
    total_holding = sum(row["holding"] for row in process_rows)
    total_busy = sum(row["busy"] for row in function_rows if row["scope"] == "processing")

    top_waiting = top_rows(
        [row for row in function_rows if row["scope"] in ("rwlock", "mutex")],
        lambda row: row["waiting"],
        limit,
    )
    top_busy = top_rows(
        [row for row in function_rows if row["scope"] == "processing"],
        lambda row: row["busy"],
        limit,
    )
    top_avg_wait = top_rows(
        [row for row in function_rows if row["scope"] in ("rwlock", "mutex") and row["locked"] > 0],
        lambda row: row["avg_wait_per_lock"],
        limit,
    )

    return {
        "input": os.path.abspath(path),
        "generated_at": datetime.datetime.now().isoformat(sep=" ", timespec="seconds"),
        "bucket_seconds": bucket_seconds,
        "summary": {
            "first_ts": first_ts.isoformat(sep=" ") if first_ts else "",
            "last_ts": last_ts.isoformat(sep=" ") if last_ts else "",
            "duration_seconds": 0 if not first_ts or not last_ts else (last_ts - first_ts).total_seconds(),
            "snapshots": len(snapshots),
            "pids": len({snapshot["pid"] for snapshot in snapshots}),
            "processes": len(snapshot_process_counts),
            "delta_rows": len(delta_rows),
            "total_waiting": total_waiting,
            "total_holding": total_holding,
            "total_busy": total_busy,
            "parse_stats": parse_stats,
            "delta_stats": delta_stats,
        },
        "snapshot_processes": [
            {
                "process": process,
                "snapshots": count,
                "pid_count": len(snapshot_pid_counts[process]),
            }
            for process, count in snapshot_process_counts.most_common()
        ],
        "process_rows": sorted(process_rows, key=lambda row: row["waiting"], reverse=True),
        "function_rows": sorted(function_rows, key=lambda row: row["waiting"] + row["busy"], reverse=True),
        "pid_rows": sorted(pid_rows, key=lambda row: row["waiting"], reverse=True),
        "bucket_rows": sorted(bucket_rows, key=lambda row: (row["bucket"], row["process"])),
        "top_waiting": top_waiting,
        "top_busy": top_busy,
        "top_avg_wait": top_avg_wait,
    }


def html_page(data):
    payload = json.dumps(data, ensure_ascii=False, separators=(",", ":"))
    escaped_payload = payload.replace("</", "<\\/")

    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Zabbix profiler report</title>
<style>
:root {{
  --bg: #f6f7f9;
  --panel: #ffffff;
  --text: #1f2933;
  --muted: #637083;
  --line: #d9dee7;
  --accent: #c62828;
  --accent2: #1565c0;
  --ok: #2e7d32;
}}
* {{ box-sizing: border-box; }}
body {{
  margin: 0;
  font: 14px/1.45 -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
  color: var(--text);
  background: var(--bg);
}}
header {{
  padding: 22px 28px 16px;
  background: #263238;
  color: #fff;
}}
h1 {{ margin: 0 0 8px; font-size: 24px; font-weight: 650; }}
h2 {{ margin: 28px 0 12px; font-size: 18px; }}
h3 {{ margin: 18px 0 10px; font-size: 15px; }}
main {{ padding: 20px 28px 40px; max-width: 1500px; margin: 0 auto; }}
.subtle {{ color: var(--muted); }}
header .subtle {{ color: #cfd8dc; }}
.legend {{
  display: flex;
  flex-wrap: wrap;
  gap: 6px 14px;
  margin-top: 10px;
  color: #d8e1e6;
  font-size: 12px;
  line-height: 1.35;
}}
.legend span {{ white-space: nowrap; }}
.legend b {{ color: #fff; font-weight: 650; }}
.grid {{
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(190px, 1fr));
  gap: 12px;
}}
.card {{
  background: var(--panel);
  border: 1px solid var(--line);
  border-radius: 6px;
  padding: 14px;
}}
.metric-label {{ color: var(--muted); font-size: 12px; text-transform: uppercase; }}
.metric-value {{ font-size: 22px; font-weight: 650; margin-top: 4px; }}
.toolbar {{
  display: flex;
  flex-wrap: wrap;
  gap: 10px;
  align-items: end;
  margin: 14px 0;
}}
label {{ display: grid; gap: 4px; color: var(--muted); font-size: 12px; }}
input, select {{
  min-height: 32px;
  border: 1px solid var(--line);
  border-radius: 4px;
  background: #fff;
  color: var(--text);
  padding: 5px 8px;
}}
button {{
  min-height: 32px;
  border: 1px solid #aeb7c4;
  border-radius: 4px;
  background: #fff;
  color: var(--text);
  padding: 5px 10px;
  cursor: pointer;
}}
button:hover {{ background: #eef2f7; }}
table {{
  width: 100%;
  border-collapse: collapse;
  background: var(--panel);
  border: 1px solid var(--line);
}}
th, td {{
  border-bottom: 1px solid var(--line);
  padding: 7px 8px;
  text-align: left;
  vertical-align: top;
}}
th {{
  position: sticky;
  top: 0;
  background: #eef2f7;
  color: #374151;
  font-weight: 650;
  cursor: pointer;
  z-index: 1;
}}
td.num, th.num {{ text-align: right; font-variant-numeric: tabular-nums; }}
tbody tr:hover {{ background: #f9fbfd; }}
.bar {{
  height: 12px;
  background: #e1e7ef;
  border-radius: 3px;
  overflow: hidden;
  min-width: 120px;
}}
.bar > span {{
  display: block;
  height: 100%;
  background: var(--accent);
}}
.chart {{
  height: 320px;
  border: 1px solid var(--line);
  background: var(--panel);
  border-radius: 6px;
  padding: 8px;
}}
.note {{
  background: #fff8e1;
  border: 1px solid #ffe082;
  border-radius: 6px;
  padding: 12px;
  color: #5d4700;
}}
.split {{
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(420px, 1fr));
  gap: 16px;
}}
@media (max-width: 700px) {{
  main, header {{ padding-left: 14px; padding-right: 14px; }}
  .split {{ grid-template-columns: 1fr; }}
  .legend span {{ white-space: normal; }}
  table {{ font-size: 12px; }}
}}
</style>
</head>
<body>
<header>
  <h1>Zabbix profiler report</h1>
  <div class="subtle" id="input"></div>
  <div class="legend" aria-label="Profiler metric legend">
    <span><b>rwlock</b>: read/write lock scope</span>
    <span><b>mutex</b>: mutex lock scope</span>
    <span><b>processing</b>: measured code scope</span>
    <span><b>locked</b>: lock acquisitions</span>
    <span><b>waiting</b>: time until lock acquired</span>
    <span><b>holding</b>: time after acquire until unlock</span>
    <span><b>busy</b>: processing scope elapsed time</span>
  </div>
</header>
<main>
  <section class="grid" id="summary"></section>

  <p class="note">
    The runtime profiler prints cumulative counters per PID. This report uses deltas between adjacent
    snapshots of the same PID/process/function/scope. The first snapshot of each PID is used only as a baseline.
  </p>

  <section class="split">
    <div>
      <h2>Top Lock Waiting</h2>
      <div id="topWaiting"></div>
    </div>
    <div>
      <h2>Top Processing Busy</h2>
      <div id="topBusy"></div>
    </div>
  </section>

  <h2>Timeline</h2>
  <div class="toolbar">
    <label>Metric
      <select id="timelineMetric">
        <option value="waiting">waiting</option>
        <option value="holding">holding</option>
        <option value="locked">locked</option>
      </select>
    </label>
    <label>Process
      <select id="timelineProcess"></select>
    </label>
  </div>
  <canvas class="chart" id="timeline"></canvas>

  <h2>Process Types</h2>
  <div id="processTable"></div>

  <h2>Function Hotspots</h2>
  <div class="toolbar">
    <label>Search
      <input id="functionSearch" placeholder="function or process">
    </label>
    <label>Scope
      <select id="scopeFilter">
        <option value="">all</option>
        <option value="rwlock">rwlock</option>
        <option value="mutex">mutex</option>
        <option value="processing">processing</option>
      </select>
    </label>
    <label>Process
      <select id="processFilter"></select>
    </label>
  </div>
  <div id="functionTable"></div>

  <h2>PID Comparison</h2>
  <div id="pidTable"></div>

  <h2>Snapshot Coverage</h2>
  <div id="coverageTable"></div>
</main>

<script id="report-data" type="application/json">{escaped_payload}</script>
<script>
const DATA = JSON.parse(document.getElementById('report-data').textContent);

function sec(value) {{
  if (!value) return '0';
  if (Math.abs(value) >= 100) return value.toFixed(1) + 's';
  if (Math.abs(value) >= 1) return value.toFixed(3) + 's';
  return (value * 1000).toFixed(3) + 'ms';
}}
function num(value) {{
  return Number(value || 0).toLocaleString(undefined, {{maximumFractionDigits: 3}});
}}
function pct(value) {{
  return Number(value || 0).toFixed(1) + '%';
}}
function escapeHtml(value) {{
  return String(value).replace(/[&<>"']/g, ch => ({{
    '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'
  }}[ch]));
}}

function metricCards() {{
  const s = DATA.summary;
  const items = [
    ['Period', s.first_ts + '<br>' + s.last_ts],
    ['Duration', sec(s.duration_seconds)],
    ['Snapshots', num(s.snapshots)],
    ['PIDs', num(s.pids)],
    ['Processes', num(s.processes)],
    ['Delta rows', num(s.delta_rows)],
    ['Total waiting', sec(s.total_waiting)],
    ['Processing busy', sec(s.total_busy)]
  ];
  document.getElementById('summary').innerHTML = items.map(([label, value]) => `
    <div class="card"><div class="metric-label">${{label}}</div><div class="metric-value">${{value}}</div></div>
  `).join('');
  document.getElementById('input').textContent = DATA.input + ' · generated ' + DATA.generated_at;
}}

let sortState = {{}};
function renderTable(target, rows, columns, options = {{}}) {{
  const key = target;
  const limit = options.limit || rows.length;
  let localRows = rows.slice();
  const state = sortState[key] || options.defaultSort;
  if (state) {{
    localRows.sort((a, b) => {{
      const av = a[state.name], bv = b[state.name];
      if (typeof av === 'number' && typeof bv === 'number') return state.dir * (av - bv);
      return state.dir * String(av).localeCompare(String(bv));
    }});
  }}
  localRows = localRows.slice(0, limit);

  const head = columns.map(col => `<th class="${{col.num ? 'num' : ''}}" data-name="${{col.name}}">${{col.label}}</th>`).join('');
  const body = localRows.map(row => '<tr>' + columns.map(col => {{
    let value = col.format ? col.format(row[col.name], row) : escapeHtml(row[col.name] ?? '');
    return `<td class="${{col.num ? 'num' : ''}}">${{value}}</td>`;
  }}).join('') + '</tr>').join('');
  document.getElementById(target).innerHTML = `<table><thead><tr>${{head}}</tr></thead><tbody>${{body}}</tbody></table>`;
  document.querySelectorAll(`#${{target}} th`).forEach(th => {{
    th.addEventListener('click', () => {{
      const name = th.dataset.name;
      const old = sortState[key];
      sortState[key] = {{name, dir: old && old.name === name ? -old.dir : -1}};
      renderTable(target, rows, columns, options);
    }});
  }});
}}

function bar(value, max, formatter = sec) {{
  const width = max > 0 ? Math.max(1, 100 * value / max) : 0;
  return `<div>${{formatter(value)}}</div><div class="bar"><span style="width:${{width}}%"></span></div>`;
}}

const processColumns = [
  {{name:'process', label:'Process'}},
  {{name:'pid_count', label:'PIDs', num:true, format:num}},
  {{name:'samples', label:'Samples', num:true, format:num}},
  {{name:'locked', label:'Locked', num:true, format:num}},
  {{name:'waiting', label:'Waiting', num:true, format:sec}},
  {{name:'holding', label:'Holding', num:true, format:sec}},
  {{name:'wait_ratio', label:'Wait ratio', num:true, format:pct}},
  {{name:'avg_wait_per_lock', label:'Avg wait/lock', num:true, format:sec}},
  {{name:'max_waiting', label:'Max bucket wait', num:true, format:sec}}
];

const functionColumns = [
  {{name:'process', label:'Process'}},
  {{name:'function', label:'Function'}},
  {{name:'scope', label:'Scope'}},
  {{name:'locked', label:'Locked', num:true, format:num}},
  {{name:'waiting', label:'Waiting', num:true, format:sec}},
  {{name:'holding', label:'Holding', num:true, format:sec}},
  {{name:'busy', label:'Busy', num:true, format:sec}},
  {{name:'wait_ratio', label:'Wait ratio', num:true, format:pct}},
  {{name:'avg_wait_per_lock', label:'Avg wait/lock', num:true, format:sec}},
  {{name:'p95_waiting', label:'P95 wait', num:true, format:sec}},
  {{name:'max_waiting', label:'Max wait', num:true, format:sec}}
];

function renderTopTables() {{
  const maxWaiting = Math.max(0, ...DATA.top_waiting.map(row => row.waiting));
  const maxBusy = Math.max(0, ...DATA.top_busy.map(row => row.busy));
  renderTable('topWaiting', DATA.top_waiting, [
    {{name:'process', label:'Process'}},
    {{name:'function', label:'Function'}},
    {{name:'scope', label:'Scope'}},
    {{name:'waiting', label:'Waiting', num:true, format:(v) => bar(v, maxWaiting)}},
    {{name:'avg_wait_per_lock', label:'Avg wait/lock', num:true, format:sec}}
  ], {{defaultSort: {{name:'waiting', dir:-1}}}});
  renderTable('topBusy', DATA.top_busy, [
    {{name:'process', label:'Process'}},
    {{name:'function', label:'Function'}},
    {{name:'busy', label:'Busy', num:true, format:(v) => bar(v, maxBusy)}},
    {{name:'max_busy', label:'Max sample', num:true, format:sec}}
  ], {{defaultSort: {{name:'busy', dir:-1}}}});
}}

function fillFilters() {{
  const processes = [...new Set(DATA.function_rows.map(row => row.process))].sort();
  for (const id of ['processFilter', 'timelineProcess']) {{
    const select = document.getElementById(id);
    select.innerHTML = '<option value="">all</option>' + processes.map(p => `<option value="${{escapeHtml(p)}}">${{escapeHtml(p)}}</option>`).join('');
  }}
}}

function renderFunctions() {{
  const q = document.getElementById('functionSearch').value.toLowerCase();
  const scope = document.getElementById('scopeFilter').value;
  const process = document.getElementById('processFilter').value;
  const rows = DATA.function_rows.filter(row =>
    (!q || row.function.toLowerCase().includes(q) || row.process.toLowerCase().includes(q)) &&
    (!scope || row.scope === scope) &&
    (!process || row.process === process)
  );
  renderTable('functionTable', rows, functionColumns, {{defaultSort: {{name:'waiting', dir:-1}}}});
}}

function renderStaticTables() {{
  renderTable('processTable', DATA.process_rows, processColumns, {{defaultSort: {{name:'waiting', dir:-1}}}});
  renderTable('pidTable', DATA.pid_rows, [
    {{name:'process', label:'Process'}},
    {{name:'pid', label:'PID', num:true, format:num}},
    {{name:'samples', label:'Samples', num:true, format:num}},
    {{name:'locked', label:'Locked', num:true, format:num}},
    {{name:'waiting', label:'Waiting', num:true, format:sec}},
    {{name:'holding', label:'Holding', num:true, format:sec}},
    {{name:'wait_ratio', label:'Wait ratio', num:true, format:pct}},
    {{name:'max_waiting', label:'Max sample wait', num:true, format:sec}}
  ], {{defaultSort: {{name:'waiting', dir:-1}}}});
  renderTable('coverageTable', DATA.snapshot_processes, [
    {{name:'process', label:'Process'}},
    {{name:'snapshots', label:'Snapshots', num:true, format:num}},
    {{name:'pid_count', label:'PIDs', num:true, format:num}}
  ], {{defaultSort: {{name:'snapshots', dir:-1}}}});
}}

function drawTimeline() {{
  const canvas = document.getElementById('timeline');
  const ctx = canvas.getContext('2d');
  const rect = canvas.getBoundingClientRect();
  const scale = window.devicePixelRatio || 1;
  canvas.width = Math.floor(rect.width * scale);
  canvas.height = Math.floor(rect.height * scale);
  ctx.setTransform(scale, 0, 0, scale, 0, 0);

  const metric = document.getElementById('timelineMetric').value;
  const process = document.getElementById('timelineProcess').value;
  const rows = DATA.bucket_rows.filter(row => !process || row.process === process);
  const buckets = [...new Set(rows.map(row => row.bucket))].sort();
  const processes = [...new Set(rows.map(row => row.process))].sort();
  const colors = ['#c62828','#1565c0','#2e7d32','#ef6c00','#6a1b9a','#00838f','#ad1457','#455a64','#827717','#5d4037'];
  const byKey = new Map(rows.map(row => [row.bucket + '\\t' + row.process, row]));

  const w = rect.width, h = rect.height;
  ctx.clearRect(0, 0, w, h);
  ctx.fillStyle = '#fff';
  ctx.fillRect(0, 0, w, h);

  const pad = {{left: 58, right: 16, top: 18, bottom: 46}};
  const plotW = w - pad.left - pad.right;
  const plotH = h - pad.top - pad.bottom;
  const totals = buckets.map(bucket => processes.reduce((sum, p) => {{
    const row = byKey.get(bucket + '\\t' + p);
    return sum + (row ? row[metric] : 0);
  }}, 0));
  const max = Math.max(1e-9, ...totals);

  ctx.strokeStyle = '#d9dee7';
  ctx.fillStyle = '#637083';
  ctx.font = '12px sans-serif';
  for (let i = 0; i <= 4; i++) {{
    const y = pad.top + plotH - plotH * i / 4;
    ctx.beginPath();
    ctx.moveTo(pad.left, y);
    ctx.lineTo(w - pad.right, y);
    ctx.stroke();
    const value = max * i / 4;
    ctx.fillText(metric === 'locked' ? num(value) : sec(value), 6, y + 4);
  }}

  const barW = Math.max(1, plotW / Math.max(1, buckets.length));
  buckets.forEach((bucket, i) => {{
    let y = pad.top + plotH;
    processes.forEach((p, j) => {{
      const row = byKey.get(bucket + '\\t' + p);
      const value = row ? row[metric] : 0;
      const bh = plotH * value / max;
      ctx.fillStyle = colors[j % colors.length];
      ctx.fillRect(pad.left + i * barW, y - bh, Math.max(1, barW - 1), bh);
      y -= bh;
    }});
  }});

  ctx.fillStyle = '#637083';
  if (buckets.length) {{
    ctx.fillText(buckets[0], pad.left, h - 24);
    const last = buckets[buckets.length - 1];
    ctx.fillText(last, Math.max(pad.left, w - pad.right - ctx.measureText(last).width), h - 24);
  }}
  ctx.fillText(`bucket: ${{DATA.bucket_seconds}}s`, pad.left, h - 8);
}}

metricCards();
fillFilters();
renderTopTables();
renderStaticTables();
renderFunctions();
drawTimeline();

document.getElementById('functionSearch').addEventListener('input', renderFunctions);
document.getElementById('scopeFilter').addEventListener('change', renderFunctions);
document.getElementById('processFilter').addEventListener('change', renderFunctions);
document.getElementById('timelineMetric').addEventListener('change', drawTimeline);
document.getElementById('timelineProcess').addEventListener('change', drawTimeline);
window.addEventListener('resize', drawTimeline);
</script>
</body>
</html>
"""


def write_json(path, data):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
        f.write("\n")


def write_html(path, data):
    with open(path, "w", encoding="utf-8") as f:
        f.write(html_page(data))


def parse_args():
    parser = argparse.ArgumentParser(
        description="Generate an HTML report from Zabbix runtime profiler logs."
    )
    parser.add_argument("log", help="Path to zabbix_server.log/profiling.log with profiler blocks.")
    parser.add_argument(
        "-o", "--output",
        default="zbx-prof-report.html",
        help="HTML output path. Default: %(default)s"
    )
    parser.add_argument(
        "--json",
        dest="json_output",
        help="Optional path for normalized report data in JSON format."
    )
    parser.add_argument(
        "--bucket",
        type=int,
        default=60,
        help="Timeline bucket size in seconds. Default: %(default)s"
    )
    parser.add_argument(
        "--top",
        type=int,
        default=20,
        help="Number of rows in top sections. Default: %(default)s"
    )
    parser.add_argument(
        "--p95-samples",
        type=int,
        default=10000,
        help=(
            "Reservoir sample size per function for approximate P95 values. "
            "Lower values use less memory. Default: %(default)s"
        )
    )
    return parser.parse_args()


def main():
    args = parse_args()

    if args.bucket <= 0:
        print("Bucket size must be greater than zero.", file=sys.stderr)
        return 2

    if args.p95_samples < 0:
        print("P95 sample size cannot be negative.", file=sys.stderr)
        return 2

    state, parse_stats = parse_log_streaming(args.log, args.bucket, args.p95_samples)
    if state["snapshot_count"] == 0:
        print("No profiler snapshots found.", file=sys.stderr)
        return 1

    data = build_report_data_from_state(args.log, state, parse_stats, args.p95_samples, args.top)

    write_html(args.output, data)
    if args.json_output:
        write_json(args.json_output, data)

    summary = data["summary"]
    print(f"Snapshots: {summary['snapshots']}")
    print(f"PIDs: {summary['pids']}")
    print(f"Delta rows: {summary['delta_rows']}")
    print(f"Total lock waiting: {fmt_seconds(summary['total_waiting'])}")
    print(f"Processing busy: {fmt_seconds(summary['total_busy'])}")
    print(f"HTML report: {os.path.abspath(args.output)}")
    if args.json_output:
        print(f"JSON data: {os.path.abspath(args.json_output)}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
