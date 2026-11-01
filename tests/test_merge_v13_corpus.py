"""Tests for scripts/merge_v13_corpus.py (v13 mix builder)."""
import json
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPT = REPO_ROOT / "scripts" / "merge_v13_corpus.py"
PY = sys.executable


def _row(kind, marker):
    return {"messages": [
        {"role": "system", "content": "sys"},
        {"role": "user", "content": f"q {marker}"},
        {"role": "assistant", "content": f"a {marker}"}],
        "meta": {"kind": kind}}


def _write(path, rows):
    path.write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")


def _run(args):
    return subprocess.run([PY, str(SCRIPT), *args], capture_output=True, text=True)


def test_additive_only_and_full_merge(tmp_path):
    base = [_row("identity", f"i{i}") for i in range(99)]
    base += [_row("protocol", f"p{i}") for i in range(60)]
    base += [_row("sql", f"s{i}") for i in range(141)]
    tooluse = [_row("tool-use", f"t{i}") for i in range(10)]
    disc = [_row("discrimination", f"d{i}") for i in range(6)]
    bp, tp, dp = tmp_path / "base.jsonl", tmp_path / "tu.jsonl", tmp_path / "di.jsonl"
    _write(bp, base)
    _write(tp, tooluse)
    _write(dp, disc)
    kinds = json.dumps({"identity": 99, "protocol": 60, "sql": 141})
    out = tmp_path / "v13.jsonl"
    r = _run(["--base", str(bp), "--tooluse", str(tp), "--discrimination", str(dp),
              "--out", str(out), "--expect-base-total", "300",
              "--expect-base-kinds", kinds, "--expect-tooluse", "10",
              "--expect-discrimination", "6"])
    assert r.returncode == 0, r.stderr
    merged = [json.loads(l) for l in out.read_text().splitlines()]
    assert len(merged) == 316
    report = json.loads((tmp_path / "v13.jsonl.mix.json").read_text())
    assert report["total_rows"] == 316 and report["seed"] == 20261031
    # Deterministic shuffle: same inputs -> identical output bytes.
    out2 = tmp_path / "v13b.jsonl"
    r2 = _run(["--base", str(bp), "--tooluse", str(tp), "--discrimination", str(dp),
               "--out", str(out2), "--expect-base-total", "300",
               "--expect-base-kinds", kinds, "--expect-tooluse", "10",
               "--expect-discrimination", "6"])
    assert r2.returncode == 0
    assert out.read_bytes() == out2.read_bytes()
    # Additive-only.
    out3 = tmp_path / "add.jsonl"
    r3 = _run(["--additive-only", "--tooluse", str(tp), "--discrimination", str(dp),
               "--out", str(out3), "--expect-tooluse", "10", "--expect-discrimination", "6"])
    assert r3.returncode == 0, r3.stderr
    assert len(out3.read_text().splitlines()) == 16


def test_base_validation_rejects_wrong_counts(tmp_path):
    bp, tp, dp = tmp_path / "base.jsonl", tmp_path / "tu.jsonl", tmp_path / "di.jsonl"
    _write(bp, [_row("identity", "x")])
    _write(tp, [_row("tool-use", "t")])
    _write(dp, [_row("discrimination", "d")])
    out = tmp_path / "o.jsonl"
    r = _run(["--base", str(bp), "--tooluse", str(tp), "--discrimination", str(dp),
              "--out", str(out), "--expect-base-total", "300",
              "--expect-base-kinds", json.dumps({"identity": 99, "protocol": 60, "sql": 141}),
              "--expect-tooluse", "1", "--expect-discrimination", "1"])
    assert r.returncode != 0
    assert not out.exists()
