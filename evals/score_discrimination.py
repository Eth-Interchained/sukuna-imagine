#!/usr/bin/env python3
"""score_discrimination.py -- deterministic mode scorer for discrimination_eval.jsonl.

Reads an eval JSONL (rows with pair_id / question / expected_mode /
decisive_feature) and a responses JSONL of {"pair_id", "question", "response"}
(response = raw single-turn model text), classifies each response's mode, and
reports overall mode accuracy, per-axis accuracy, and the expected x predicted
confusion counts. All rates are printed as raw fractions (n/N) alongside any
percentage -- never percentages alone (baseline_plan.md section 5).

Classification (deterministic):
  * If toolproto.parser is importable (loaded by path from --repo, default:
    the sukuna-imagine checkout if present), a single-block response is
    classified from the parser's kind (TOOL_CALL -> tool, SQL -> sql).
  * Counting always uses the envelope markers, so multi-block responses --
    which the strict parser rejects as a turn -- still classify: >=2
    <<<TOOL_CALL>>> blocks in one response = "chain", exactly 1 = "tool",
    0 tool calls with a <<<SQL>>> block = "sql", anything else = "other".

Usage:
  python3 score_discrimination.py --eval discrimination_eval.jsonl \
      --responses responses.jsonl [--repo ~/workspace/sukuna-imagine]
  python3 score_discrimination.py --self-test
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path

TOOL_CALL_RE = re.compile(r"<<<TOOL_CALL>>>")
SQL_RE = re.compile(r"<<<SQL>>>")
MODES = ("sql", "tool", "chain", "other")


def load_toolproto(repo):
    """Load toolproto.parser by path from a repo checkout; None if absent."""
    if not repo:
        return None
    path = Path(repo).expanduser() / "toolproto" / "parser.py"
    if not path.is_file():
        return None
    try:
        spec = importlib.util.spec_from_file_location("toolproto_parser", path)
        mod = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = mod  # dataclass resolution needs the module registered
        spec.loader.exec_module(mod)
        return mod
    except Exception:
        return None


def classify(response, parser=None):
    """Classify one raw response text into sql | tool | chain | other."""
    text = response or ""
    n_tool = len(TOOL_CALL_RE.findall(text))
    if n_tool >= 2:
        return "chain"
    if n_tool == 1:
        return "tool"
    if SQL_RE.search(text):
        return "sql"
    if parser is not None:
        try:
            result = parser.parse(text)
            if result.ok and result.kind == "TOOL_CALL":
                return "tool"
            if result.ok and result.kind == "SQL":
                return "sql"
        except Exception:
            pass
    return "other"


def load_jsonl(path):
    return [json.loads(line) for line in Path(path).read_text(encoding="utf-8").splitlines() if line.strip()]


def score(eval_rows, response_rows, parser=None):
    """Join responses to eval rows on (pair_id, question); return stats dict."""
    # Contrastive pairs can share (pair_id, question) -- the two rows differ in
    # the prompt, not the question -- so responses for one key form a queue,
    # consumed in file order against eval rows in file order.
    by_key = defaultdict(list)
    for r in response_rows:
        by_key[(r.get("pair_id"), r.get("question"))].append(r.get("response", ""))
    confusion = Counter()
    per_axis_total, per_axis_ok = Counter(), Counter()
    total = ok = answered = 0
    for row in eval_rows:
        key = (row.get("pair_id"), row.get("question"))
        if not by_key.get(key):
            continue
        answered += 1
        expected = row["expected_mode"]
        predicted = classify(by_key[key].pop(0), parser)
        confusion[(expected, predicted)] += 1
        axis = row.get("decisive_feature", "?")
        per_axis_total[axis] += 1
        total += 1
        if predicted == expected:
            ok += 1
            per_axis_ok[axis] += 1
    return {"total": total, "correct": ok, "answered": answered,
            "confusion": confusion, "per_axis_total": per_axis_total,
            "per_axis_ok": per_axis_ok}


def frac(n, d):
    pct = ("%.1f%%" % (100.0 * n / d)) if d else "n/a"
    return "%d/%d (%s)" % (n, d, pct)


def report(stats):
    lines = ["mode accuracy: %s" % frac(stats["correct"], stats["total"])]
    for axis in sorted(stats["per_axis_total"]):
        lines.append("axis %-20s %s" % (axis, frac(stats["per_axis_ok"][axis],
                                                    stats["per_axis_total"][axis])))
    lines.append("confusion (expected x predicted):")
    for expected in MODES:
        for predicted in MODES:
            n = stats["confusion"].get((expected, predicted), 0)
            if n:
                lines.append("  %-6s -> %-6s %d" % (expected, predicted, n))
    if stats["answered"] < stats["total"]:
        lines.append("unanswered eval rows: %d" % (stats["total"] - stats["answered"]))
    return "\n".join(lines)


def _canned(eval_rows, style):
    """Build synthetic responses: 'perfect' follows expected_mode; 'always-sql'
    answers every row with a lone SQL envelope."""
    out = []
    for row in eval_rows:
        mode = row["expected_mode"] if style == "perfect" else "sql"
        if mode == "sql":
            resp = "<<<SQL>>>\n%s\n<<<END>>>" % (row.get("reference_sql") or "SELECT 1")
        elif mode == "tool":
            resp = '<<<TOOL_CALL>>>\n{"name": "%s", "arguments": {}}\n<<<END>>>' % (
                row.get("expected_tool") or "schema.inspect")
        else:  # chain: two tool calls in one response
            resp = ('<<<TOOL_CALL>>>\n{"name": "schema.inspect", "arguments": {}}\n<<<END>>>\n'
                    '<<<TOOL_CALL>>>\n{"name": "sql.execute", "arguments": {"sql": "SELECT 1"}}\n<<<END>>>')
        out.append({"pair_id": row["pair_id"], "question": row["question"], "response": resp})
    return out


def self_test(parser=None):
    eval_rows = []
    for i, (axis, mode) in enumerate([
            ("schema_sufficiency", "sql"), ("schema_sufficiency", "tool"),
            ("live_state", "sql"), ("live_state", "chain"),
            ("temporal", "sql"), ("temporal", "tool"),
            ("provenance", "sql"), ("provenance", "chain")]):
        eval_rows.append({"pair_id": "selftest-%d" % (i // 2), "question": "q%d" % i,
                          "expected_mode": mode, "decisive_feature": axis,
                          "expected_tool": "schema.inspect", "reference_sql": "SELECT 1"})
    perfect = score(eval_rows, _canned(eval_rows, "perfect"), parser)
    assert perfect["total"] == 8 and perfect["correct"] == 8, perfect
    assert perfect["confusion"][("chain", "chain")] == 2
    degenerate = score(eval_rows, _canned(eval_rows, "always-sql"), parser)
    # 4 of the 8 canned rows expect sql; always-sql must score exactly 4/8
    assert degenerate["total"] == 8 and degenerate["correct"] == 4, degenerate
    assert degenerate["confusion"][("tool", "sql")] == 2
    assert degenerate["confusion"][("chain", "sql")] == 2
    assert classify("plain prose, no envelope") == "other"
    assert classify("<<<SQL>>>\nSELECT 1\n<<<END>>>") == "sql"
    print("self-test PASS: perfect %s ; always-sql %s ; confusion arithmetic verified"
          % (frac(perfect["correct"], perfect["total"]),
             frac(degenerate["correct"], degenerate["total"])))
    return 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--eval", dest="eval_path")
    ap.add_argument("--responses")
    ap.add_argument("--repo", default="~/workspace/sukuna-imagine",
                    help="checkout containing toolproto/parser.py (optional)")
    ap.add_argument("--self-test", action="store_true")
    args = ap.parse_args()
    parser = load_toolproto(args.repo)
    if args.self_test:
        return self_test(parser)
    if not args.eval_path or not args.responses:
        ap.error("--eval and --responses are required unless --self-test")
    stats = score(load_jsonl(args.eval_path), load_jsonl(args.responses), parser)
    print("classifier: %s" % ("toolproto.parser + marker counting" if parser else "regex fallback"))
    print(report(stats))
    return 0


if __name__ == "__main__":
    sys.exit(main())
