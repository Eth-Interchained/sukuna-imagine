#!/usr/bin/env python3
"""merge_v13_corpus.py — build the v13 training mix.

v13 = the v12 base corpus, preserved untouched, plus the v13 additive
families (tool-use 26,280 + discrimination 19,710 = 45,990), deterministically
shuffled with seed 20261031. Projected total: 131,400 + 45,990 = 177,390.

The base is validated against the v12 manifest before anything is merged:
total 131,400 rows with per-kind counts identity 43,362 / protocol 26,280 /
sql 61,758 (33/20/47). A row's kind is read from ``meta.kind`` when present,
else from a top-level ``kind`` field; if neither encoding is found the merge
refuses and reports what it saw rather than forcing the counts.

With ``--additive-only`` (no ``--base``) only the additive files are merged
(45,990 rows) — the form used to ship the v13 bundle before the base is
present on the training box. The base checks are enforced there by the
runbook preflight when the full merge runs.

Usage:
  python scripts/merge_v13_corpus.py --base corpus/v12_train.jsonl \
      --tooluse corpus/v13_tooluse.jsonl \
      --discrimination corpus/v13_discrimination.jsonl \
      --out corpus/v13_train.jsonl
  python scripts/merge_v13_corpus.py --additive-only \
      --tooluse v13_tooluse.jsonl --discrimination v13_discrimination.jsonl \
      --out v13_additive.jsonl

The mix report is written alongside the output as ``<out>.mix.json`` and
printed to stdout.
"""
from __future__ import annotations

import argparse
import json
import random
import sys
from collections import Counter
from pathlib import Path

SEED = 20261031
EXPECT_BASE_TOTAL = 131_400
EXPECT_BASE_KINDS = {"identity": 43_362, "protocol": 26_280, "sql": 61_758}
EXPECT_TOOLUSE = 26_280
EXPECT_DISCRIMINATION = 19_710


def load_rows(path: str) -> list[dict]:
    rows = []
    with open(path, encoding="utf-8") as fh:
        for lineno, line in enumerate(fh, 1):
            if not line.strip():
                continue
            rec = json.loads(line)
            messages = rec.get("messages") if isinstance(rec, dict) else None
            if (not isinstance(messages, list) or not messages
                    or any(not isinstance(m, dict) or m.get("role") not in
                           {"system", "user", "assistant"}
                           or not isinstance(m.get("content"), str)
                           or not m["content"].strip() for m in messages)
                    or messages[-1]["role"] != "assistant"
                    or not any(m["role"] == "user" for m in messages[:-1])):
                raise ValueError(
                    f"{path}:{lineno}: expected nonempty chat messages ending in assistant")
            rows.append(rec)
    if not rows:
        raise ValueError(f"{path}: empty corpus")
    return rows


def row_kind(row: dict) -> str | None:
    meta = row.get("meta")
    if isinstance(meta, dict) and isinstance(meta.get("kind"), str):
        return meta["kind"]
    if isinstance(row.get("kind"), str):
        return row["kind"]
    return None


def validate_base(rows: list[dict], expect_total: int, expect_kinds: dict) -> dict:
    kinds = Counter()
    unknown = 0
    for r in rows:
        k = row_kind(r)
        if k is None:
            unknown += 1
        else:
            kinds[k] += 1
    if unknown:
        raise ValueError(
            f"base kind encoding not recognised: {unknown} rows carry neither "
            f"meta.kind nor a top-level kind field (kinds seen: {dict(kinds)}); "
            "refusing to force the v12 manifest counts")
    if len(rows) != expect_total:
        raise ValueError(f"base total {len(rows)} != expected {expect_total}")
    for kind, want in expect_kinds.items():
        if kinds.get(kind, 0) != want:
            raise ValueError(
                f"base kind {kind!r}: {kinds.get(kind, 0)} != expected {want} "
                f"(all kinds: {dict(kinds)})")
    extra = set(kinds) - set(expect_kinds)
    if extra:
        raise ValueError(f"base carries unexpected kinds: {sorted(extra)} ({dict(kinds)})")
    return dict(kinds)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--base", help="v12 base corpus JSONL (required unless --additive-only)")
    ap.add_argument("--tooluse", required=True)
    ap.add_argument("--discrimination", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--additive-only", action="store_true",
                    help="merge only the additive files (no --base)")
    ap.add_argument("--seed", type=int, default=SEED)
    ap.add_argument("--expect-base-total", type=int, default=EXPECT_BASE_TOTAL)
    ap.add_argument("--expect-base-kinds", default=None,
                    help="JSON object overriding expected base kind counts (testing)")
    ap.add_argument("--expect-tooluse", type=int, default=EXPECT_TOOLUSE)
    ap.add_argument("--expect-discrimination", type=int, default=EXPECT_DISCRIMINATION)
    a = ap.parse_args()

    expect_kinds = EXPECT_BASE_KINDS
    if a.expect_base_kinds:
        expect_kinds = json.loads(a.expect_base_kinds)

    try:
        tooluse = load_rows(a.tooluse)
        discrimination = load_rows(a.discrimination)
        if len(tooluse) != a.expect_tooluse:
            raise ValueError(f"tooluse rows {len(tooluse)} != expected {a.expect_tooluse}")
        if len(discrimination) != a.expect_discrimination:
            raise ValueError(
                f"discrimination rows {len(discrimination)} != expected {a.expect_discrimination}")
        base_rows: list[dict] = []
        base_kinds: dict = {}
        if not a.additive_only:
            if not a.base:
                raise ValueError("--base is required unless --additive-only")
            base_rows = load_rows(a.base)
            base_kinds = validate_base(base_rows, a.expect_base_total, expect_kinds)
    except (ValueError, OSError) as exc:
        ap.error(str(exc))

    merged = base_rows + tooluse + discrimination
    rng = random.Random(a.seed)
    rng.shuffle(merged)

    report = {
        "seed": a.seed,
        "additive_only": bool(a.additive_only),
        "components": {
            "base": {"input_rows": len(base_rows), "kinds": base_kinds,
                     "path": a.base},
            "tooluse": {"input_rows": len(tooluse), "path": a.tooluse},
            "discrimination": {"input_rows": len(discrimination),
                               "path": a.discrimination},
        },
        "additive_rows": len(tooluse) + len(discrimination),
        "total_rows": len(merged),
    }
    if a.additive_only:
        report["projected_total_with_base"] = a.expect_base_total + report["additive_rows"]

    out = Path(a.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w", encoding="utf-8") as fh:
        for r in merged:
            fh.write(json.dumps(r, ensure_ascii=False) + "\n")
    report_path = Path(str(out) + ".mix.json")
    report_path.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n",
                           encoding="utf-8")
    print(json.dumps(report, indent=2, ensure_ascii=False))
    print(f"wrote {out}; mix report {report_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
