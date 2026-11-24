# SPDX-License-Identifier: Apache-2.0
"""Render one NCP JSONL trace as a human-readable timeline."""

import argparse
from collections import Counter
import json
import math
from pathlib import Path
import sys

SCHEMA = "ncp-trace-0.1"


def reject_constant(value):
    raise ValueError(f"invalid JSON constant: {value}")


def read_trace(path):
    rows = []
    counts = Counter()
    unknown = 0
    seen = False
    trace_id = None
    with path.open(encoding="utf-8") as stream:
        for number, line in enumerate(stream, 1):
            if not line.strip():
                continue
            try:
                record = json.loads(line, parse_constant=reject_constant)
                if not isinstance(record, dict):
                    raise ValueError("record must be a JSON object")
                if record.get("trace_schema_version") != SCHEMA:
                    raise ValueError(f"expected schema {SCHEMA}")
                kind = record.get("type")
                if not isinstance(kind, str) or not kind:
                    raise ValueError("missing or invalid type")
                seen = True
                if kind == "runtime_info":
                    continue
                if kind != "invoke":
                    unknown += 1
                    continue
                step = record.get("step")
                if type(step) is not int or step < 0:
                    raise ValueError("step must be a non-negative integer")
                for key in ("trace_id", "node_id", "brick_id", "result_type"):
                    if not isinstance(record.get(key), str) or not record[key]:
                        raise ValueError(f"{key} must be a non-empty string")
                if trace_id is None:
                    trace_id = record["trace_id"]
                elif trace_id != record["trace_id"]:
                    raise ValueError("multiple trace IDs in one trace file")
                latency = record.get("latency_ms")
                if (
                    type(latency) not in (int, float)
                    or not math.isfinite(latency)
                    or latency < 0
                ):
                    raise ValueError("latency_ms must be a finite non-negative number")
                rows.append((step, record["node_id"], record["brick_id"],
                             record["result_type"], latency))
                counts[record["result_type"]] += 1
            except (ValueError, OverflowError) as exc:
                raise ValueError(f"line {number}: {exc}") from exc
    if not seen:
        raise ValueError("trace contains no records")
    return rows, counts, unknown


def display(value):
    # Escape control characters (including terminal escape sequences).
    return json.dumps(value, ensure_ascii=True)[1:-1]


def render(rows, counts, unknown):
    total = math.fsum(row[4] for row in rows)
    headers = ("step", "node_id", "brick_id", "result_type", "latency_ms")
    data = [
        (str(step), display(node), display(brick), display(result), f"{latency:.3f}")
        for step, node, brick, result, latency in rows
    ]
    widths = [max(len(header), *(len(row[i]) for row in data), 0)
              for i, header in enumerate(headers)]

    def format_row(row):
        return " | ".join(value.rjust(width) if i in (0, 4) else value.ljust(width)
                          for i, (value, width) in enumerate(zip(row, widths)))

    lines = [format_row(headers), "-+-".join("-" * width for width in widths)]
    lines.extend(format_row(row) for row in data)
    lines.append(f"Total steps: {len(rows)}")
    lines.append(f"Total latency (sum of invokes): {total:.3f} ms")
    lines.append("Results by type:")
    lines.extend(f"  {display(result)}: {counts[result]}" for result in sorted(counts))
    if not counts:
        lines.append("  (none)")
    if unknown:
        lines.append(f"Skipped unknown record types: {unknown}")
    return "\n".join(lines)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("trace", type=Path, help="path to one NCP JSONL trace")
    args = parser.parse_args(argv)
    try:
        output = render(*read_trace(args.trace))
    except (OSError, ValueError, OverflowError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    print(output)
    return 0


if __name__ == "__main__":
    sys.exit(main())
