# SPDX-License-Identifier: Apache-2.0
"""CLI tests with synthetic records; no runtime or network required."""

import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

TOOL = Path(__file__).resolve().parents[1] / "trace_pretty.py"
SCHEMA = "ncp-trace-0.1"


def runtime_info():
    return {"type": "runtime_info", "trace_schema_version": SCHEMA,
            "runtime_version": "0.4.0", "wasmtime_version": "43",
            "timestamp": "2026-01-01T00:00:00Z"}


def invoke(**changes):
    record = {"type": "invoke", "trace_schema_version": SCHEMA, "step": 0,
              "trace_id": "trace-1", "node_id": "echo_node",
              "brick_id": "org.ncp-examples.echo", "result_type": "Success",
              "latency_ms": 2.5}
    record.update(changes)
    return record


class TracePrettyTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "trace file.jsonl"

    def run_cli(self, *args):
        return subprocess.run([sys.executable, str(TOOL), *map(str, args)],
                              capture_output=True, text=True, timeout=5)

    def run_records(self, *records):
        self.path.write_text("\n".join(json.dumps(r) for r in records) + "\n",
                             encoding="utf-8")
        return self.run_cli(self.path)

    def assert_invalid(self, result, message):
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(result.stdout, "")
        self.assertIn(message, result.stderr)

    def test_timeline_and_summary(self):
        result = self.run_records(runtime_info(), invoke(),
                                  invoke(step=1, node_id="next", latency_ms=1,
                                         result_type="Failure"),
                                  invoke(step=2, latency_ms=0.25,
                                         result_type="LowConfidence"))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("step | node_id", result.stdout)
        self.assertLess(result.stdout.index("echo_node"), result.stdout.index("next"))
        self.assertIn("Total steps: 3", result.stdout)
        self.assertIn("Total latency (sum of invokes): 3.750 ms", result.stdout)
        for kind in ("Success", "Failure", "LowConfidence"):
            self.assertIn(f"  {kind}: 1", result.stdout)

    def test_zero_latency_and_repeated_result(self):
        result = self.run_records(invoke(latency_ms=0), invoke(step=1, latency_ms=0))
        self.assertEqual(result.returncode, 0)
        self.assertIn("0.000 ms", result.stdout)
        self.assertIn("Success: 2", result.stdout)

    def test_missing_file(self):
        self.assert_invalid(self.run_cli(self.path), "error:")

    def test_malformed_json_after_valid_record(self):
        self.path.write_text(json.dumps(invoke()) + "\n{bad\n", encoding="utf-8")
        self.assert_invalid(self.run_cli(self.path), "line 2")

    def test_empty_or_blank_file(self):
        for content in ("", "\n  \n"):
            with self.subTest(content=content):
                self.path.write_text(content, encoding="utf-8")
                self.assert_invalid(self.run_cli(self.path), "no records")

    def test_runtime_info_only(self):
        result = self.run_records(runtime_info())
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("Total steps: 0", result.stdout)
        self.assertIn("(none)", result.stdout)

    def test_blank_lines_and_unknown_type(self):
        self.path.write_text("\n" + json.dumps(runtime_info()) + "\n\n"
                             + json.dumps({"type": "future", "trace_schema_version": SCHEMA})
                             + "\n" + json.dumps(invoke()) + "\n", encoding="utf-8")
        result = self.run_cli(self.path)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("Total steps: 1", result.stdout)
        self.assertIn("Skipped unknown record types: 1", result.stdout)

    def test_invalid_shape_and_schema(self):
        for record in ([], 42, invoke(trace_schema_version="future"), {"type": "invoke"}):
            with self.subTest(record=record):
                self.assert_invalid(self.run_records(record), "line 1")

    def test_invalid_display_fields(self):
        for changes in ({"step": True}, {"step": -1}, {"node_id": None},
                        {"brick_id": ""}, {"result_type": 3}, {"trace_id": ""}):
            with self.subTest(changes=changes):
                self.assert_invalid(self.run_records(invoke(**changes)), "line 1")
        record = invoke()
        del record["node_id"]
        self.assert_invalid(self.run_records(record), "node_id")

    def test_invalid_latency(self):
        for value in (True, -1, "1", None, float("inf"), float("nan"), 10**400):
            with self.subTest(value=value):
                self.assert_invalid(self.run_records(invoke(latency_ms=value)), "line 1")

    def test_latency_overflow(self):
        self.assert_invalid(self.run_records(invoke(latency_ms=1e308),
                                            invoke(step=1, latency_ms=1e308)), "error:")

    def test_mixed_trace_ids(self):
        self.assert_invalid(self.run_records(invoke(), invoke(trace_id="trace-2")),
                            "multiple trace IDs")

    def test_terminal_control_is_escaped(self):
        result = self.run_records(invoke(node_id="echo\x1b[31m\nnode"))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotIn("\x1b", result.stdout)
        self.assertIn(r"\u001b", result.stdout)
        self.assertIn(r"\nnode", result.stdout)

    def test_invalid_utf8(self):
        self.path.write_bytes(b"\xff")
        self.assert_invalid(self.run_cli(self.path), "error:")

    def test_usage_error(self):
        self.assert_invalid(self.run_cli(), "usage:")


if __name__ == "__main__":
    unittest.main()
