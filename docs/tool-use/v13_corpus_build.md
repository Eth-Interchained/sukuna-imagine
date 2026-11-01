# v13 corpus build — what was built, how it was verified

Build date: 2026-10-31. Branch: `v13-corpus-upgrade`. This note summarises the
v13 corpus build; the training procedure itself lives in the corpus bundle's
`TRAINING_RUNBOOK.md` (bundle: `~/workspace/sukuna-imagine-v13-corpus/`).

## Mark's spec

- v12 base preserved untouched: 131,400 rows at 33/20/47
  (identity 43,362 / protocol 26,280 / sql 61,758). The base is NOT in this
  repo or on this VM — it lives on the training box; `scripts/merge_v13_corpus.py`
  validates it against that manifest at merge time (runbook preflight).
- Additive: +20% tool-usage = 26,280 rows; +15% discrimination = 19,710 rows.
- Projected v13 total: 177,390 rows (projection until the box merge runs).

## Families

| family | generator | seed(s) | candidates | admitted | tiers |
|---|---|---|---|---|---|
| builder (PR #29) | `scripts/make_tooluse_corpus.py` | 20261014 | 23,500 | 20,000 | parse 20,000 |
| temporal | `scripts/make_temporal_corpus.py` | 2901 | 4,600 | 4,000 | deterministic 4,000 |
| tool-protocol | `scripts/make_toolproto_protocol.py` | 4101–4114 (chunks) | 2,700 | 2,280 | execution 1,103 / deterministic 1,177 |
| discrimination | `scripts/make_discrimination_corpus.py` | 1301–1358 (chunks) | 11,500 pairs | 9,855 pairs (19,710 rows) | execution 13,138 rows / deterministic 6,572 rows (re-derived — see below) |

Discrimination forge outcome (from the admitted/rejects files of record):
9,855 pairs admitted, **1 pair rejected** (`disc-read_vs_act-0104`,
`write_effect` — a genuine run_write state mismatch caught by the gate),
1,644 pairs unprocessed, admit rate 9855/9856 = 0.9999. Tier split
re-derived by the finisher session: the original run's stats write died
with its session's write containment, so the forge's own verify path was
re-run over exactly the 9,855 shipped pairs (candidates matched by
content) — all 9,855 pairs re-passed, 0 failures: execution 6,569 pairs
(13,138 rows) / deterministic 3,286 pairs (6,572 rows); per-pair record
in the bundle at `evidence/discrimination_tier_rederive.tsv`. One
sensitivity note: an independent full re-forge of the candidate file
admitted pair `disc-read_vs_act-0104` (rejected `write_effect` in the
original run) and stopped one pair earlier — the write-effect check is
sensitive to NEDB scratch-DB state; the shipped file is the original
run's output, hash-verified. Pair integrity
verified on the admitted file: pairs never split (pair_ids are chunk-local
and repeat across chunks; every id's row count is even). BUILDER FAMILY
STATUS (completed 2026-10-31 by the finisher session): 23,500 candidates
generated with the stock generator's loop replicated by a streaming driver
(byte-identical to the stock generator on a 2,000-row prefix check,
sha256 6605f98e16395d05 — the stock generator's single end-of-run write is
what the sandbox write containment kills), then forge-admitted in 4
sequential segment runs of the unmodified forge (6,000/6,000/6,000/5,500
candidates, target 5,000 each): **20,000 admitted, 0 rejected, 3,500
unprocessed, admit rate 1.0, all tier=parse, 7,692 alias-normalised rows**
(validation-only normalisation, admitted content byte-identical to
generator output).

Tool-usage total: 20,000 + 4,000 + 2,280 = 26,280. Additive total: 45,990.

Generation note: the discrimination and protocol generators raise
`RuntimeError: could not build pair/row` after ~290 units in one process
(an internal pool exhausts), so they were driven in 200-unit chunks with
consecutive seeds (first chunk at the specified seed); every chunk succeeded
on its first attempt. The builder and temporal generators ran in a single
process at the specified seeds. Full per-family forge stats (including
reject reasons and unprocessed counts) are in the bundle's `forge_stats.json`.

## Gate-4 forge (`scripts/forge_v13_corpus.py`)

Every candidate row passed: (1) the protocol gate — each assistant turn is
exactly one END-terminated envelope under `toolproto.parser`, a permitted
grounded summary, or a write proposal (SQL envelope + one authorization
line); TOOL_CALL→TOOL_RESULT relay names match; ≤3 calls/row; (2) SQL
verification via `stealth.gate` L0–L4 against the reference on the row's
fixture DB, with symmetric clock injection for temporal rows plus a Python
zoneinfo bounds cross-check (2,010 recompute-ok, 114 literal-ok, 0 mismatch);
(3) host simulation for chain rows (sql.execute/sql.explain executed in
order, schema.inspect/nedb.provenance argument tables validated, summary
facts grounded in TOOL_RESULT payloads or executed results, writes verified
by `run_write` effect-equivalence); (4) builder rows (no `_verify`): protocol
gate + gate L0/L1, execution only when the prompt's table set uniquely
matches a catalog schema (the builder's prompts use single-table synthetic
schemas, so builder rows verify at the parse tier — see below).

### Verification tiers (honest, never upgraded)

- **execution** — real execution on NEDB (L3/L4 or run_write).
- **deterministic** — the NEDB NQL translator refuses the construct
  (INTERVAL / date_trunc / TIMESTAMPTZ / DATE literals — probed, not assumed),
  so the row was verified by pglast L0 parse + L1 safety + gate-catalog bind
  (`stealth.schema.check_schema`) + pglast AST equivalence vs the reference
  modulo AND/OR operand order. All 4,000 temporal rows are in this tier:
  their SQL is exactly the temporal arithmetic the engine cannot run.
- **parse** — builder rows whose prompt table set matches no catalog schema
  uniquely: protocol gate + gate L0/L1 + summary grounding only.

### Parser-normative decision / JSON-schema divergence

`docs/tool-use/tool_protocol_spec.md` §5–§6 is the definition of record: the
`toolproto.parser` registry is normative (`sql.execute{sql, limit?}`,
`nedb.provenance{table, row_id?}`); the PR #26 JSON schemas diverge
(`max_rows`, `key`). The new v13 families (discrimination, temporal,
protocol) emit registry names. The PR #29 builder predates the decision and
emits the schema names; those two names are pure aliases (same types and
semantics), so the forge normalises them **for validation only** — admitted
row content is byte-identical to generator output — and counts every
normalised row in `forge_stats.json` (`alias_normalised_rows`). No other
parser deviation was admitted.

## Discrimination design

Contrastive pairs (kept whole: admit/reject at pair level, pairs never
split) over six axes (`meta.decisive_feature`): schema_sufficiency,
live_state, dependency_depth, read_vs_act, temporal, provenance. Modes
admitted: sql 9,850 / tool 6,572 / chain 3,288. Axes admitted:
live_state 3,294, schema_sufficiency 3,290, provenance 3,290,
temporal 3,282, dependency_depth 3,282, read_vs_act 3,272.

## Eval sets (`evals/` in repo and bundle)

- `discrimination_eval.jsonl` (240 rows) — scored separately with
  `evals/score_discrimination.py` (deterministic mode scorer; a model's
  chosen mode per row vs the labelled mode).
- `temporal_eval.jsonl` (15 rows), `tooluse_eval.jsonl` (55 rows) — run
  through `stealth.evaluate` per the runbook.
- `evals/make_eval_v13.py` regenerates them; `evals/eval_schemas.py` carries
  their schemas.

## Bundle and HF status

Bundle: `~/workspace/sukuna-imagine-v13-corpus/` — `v13_tooluse.jsonl`
(26,280 = builder + protocol + temporal admitted, in that order),
`v13_temporal.jsonl` (the 4,000 temporal subset, same rows, for inspection),
`v13_discrimination.jsonl` (19,710), `v13_additive.jsonl` (45,990,
merge `--additive-only` output), `mix_report.json`, `forge_stats.json`,
`rejects/`, `evals/`, `TRAINING_RUNBOOK.md`.

HF dataset `Interchained/imagine-v13-corpus`: **not published** — no HF
write credential is reachable in this environment (no token in env or local
stores; `HfApi().whoami()` → `LocalTokenNotFoundError`). The local bundle is
the fallback of record.
