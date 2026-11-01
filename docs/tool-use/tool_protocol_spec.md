# Tool Protocol Specification — sukuna-imagine v13 Corpus

**Status:** Definition of record for the v13 tool-protocol corpus family.
**Version:** 1.0 (corpus protocol v1)
**Applies to:** All v13 training candidates that carry a tool call, a tool
observation, or a tool-adjacent terminal envelope.

This document is the protocol definition of record for the corpus. Where any
other design document, JSON schema, or taxonomy draft disagrees with this
specification on a wire shape, this specification governs corpus rows, and
§6 records the known disagreements explicitly.

Normative sources, in precedence order:

1. `toolproto/parser.py` — the host-side validator of record. Its V1 tool
   registry (§5) and its rejection semantics (§11) are normative.
2. `docs/tool-use/RFC.md` — architecture, roles, and invariants.
3. `docs/tool-use/curriculum_taxonomy.md` §2, §7, §9 — corpus encoding,
   admission gates, and the envelope quick reference.
4. `schemas/tool_call.schema.json`, `schemas/tool_result.schema.json` —
   informative wire schemas; see the reconciliation note in §6.

---

## 1. Purpose and scope

1.1. The protocol lets the model (Imagine) request a bounded, read-only tool
call instead of guessing at schema, data, time, or lineage it does not have,
and lets the host relay the result back as an observation the model grounds
its final answer in.

1.2. The guiding doctrine is: **the model proposes, the host authorizes, the
database verifies.** The model never executes a tool itself.

1.3. Scope is the assistant-turn wire format, the V1 tool registry, chaining,
termination, and rejection semantics. Training method, model weights, and
host implementation internals are out of scope.

## 2. Roles

2.1. **Model.** Emits exactly one envelope per assistant turn (§10), or, after
at least one observation, a grounded plain-text summary (§9.3). It selects
tools, supplies schema-valid arguments, and grounds every downstream step in
what was actually observed.

2.2. **Host.** The only component that touches tools. It parses and validates
every `<<<TOOL_CALL>>>` with `toolproto.parser`, enforces permissions and
budgets, executes allowed calls, and relays each outcome to the model as a
`<<<TOOL_RESULT>>>` observation (§7). On the wire the host stamps each
observation `originated: "tool"` with a host-generated `tool_call_id` pairing
it to its request.

2.3. **Database (NEDB).** Source of truth. Executes bounded reads and answers
provenance queries, so a final answer can be verified against the rows
actually observed, not merely against the SQL text.

## 3. Envelope set

3.1. An **envelope** is a literal delimited block: an opener marker, a
payload, and the shared terminator `<<<END>>>`. Markers are exact byte
strings; no variant spelling, casing, or spacing is recognized.

3.2. The envelope set is:

| Envelope | Emitter | Payload | Role |
|---|---|---|---|
| `<<<SQL>>>` | model | one SQL statement | Grounded SQL answer; also the only form a write proposal may take (§13) |
| `<<<TOOL_CALL>>>` | model | one JSON object `{name, arguments}` | A single tool request (§5) |
| `<<<TOOL_RESULT>>>` | host only | one JSON observation payload | Host relay of a tool outcome (§7); never emitted by the model |
| `<<<CLARIFY>>>` | model | one question | Terminal: the request is ambiguous or under-specified (§9) |
| `<<<UNANSWERABLE>>>` | model | one statement | Terminal: the schema or evidence cannot support an answer (§9) |

3.3. `<<<END>>>` is the shared terminator for every envelope, including the
host relay. It is not an envelope in its own right; a stray `<<<END>>>` with
no opening envelope is malformed (§11).

3.4. In corpus rows, messages use exactly the roles `system`, `user`,
`assistant`. The host relay (§7) is encoded as a `user` message. Assistant
messages contain exactly one envelope, or a grounded plain-text summary after
observations, with the single write-proposal exception of §13.2.

## 4. Grammar

```ebnf
turn            = envelope | summary ;
summary         = plain-text ;                  (* only after >= 1 observation, §9.3 *)

envelope        = sql-env | call-env | clarify-env | unanswerable-env ;
host-turn       = result-env ;                  (* host only, never the model *)

sql-env         = "<<<SQL>>>" sql-statement "<<<END>>>" ;
call-env        = "<<<TOOL_CALL>>>" call-json "<<<END>>>" ;
result-env      = relay-opener observation-json "<<<END>>>" ;
clarify-env     = "<<<CLARIFY>>>" text "<<<END>>>" ;
unanswerable-env= "<<<UNANSWERABLE>>>" text "<<<END>>>" ;

relay-opener    = '<<<TOOL_RESULT name="' tool-name '" status="'
                  ("ok" | "error") '">>>' ;

call-json       = "{" '"name"' ":" tool-name ","
                      '"arguments"' ":" arguments "}" ;
                  (* exactly these two fields; no others, §5.3 *)

tool-name       = "schema.inspect" | "clock.now" | "sql.explain"
                | "sql.execute" | "nedb.provenance" ;
```

4.1. For `<<<SQL>>>`, `<<<CLARIFY>>>`, and `<<<UNANSWERABLE>>>`, extraction is
a literal, non-greedy delimiter match, exactly as in the ancestor SQL
protocol (`stealth/sentinel.py`): payload bytes are data and are never
re-parsed.

4.2. For `<<<TOOL_CALL>>>`, the JSON payload is extracted with a brace-aware
scan that respects strings and escapes, so a marker-like sequence inside a
JSON string never ends the block early. The scan does not relax §11.3: a real
envelope marker inside the payload is smuggling and is rejected.

## 5. V1 tool registry (normative)

5.1. The V1 registry is fixed and **read-only**. It contains exactly five
tools. A `name` outside this registry is rejected before any execution
(§11).

5.2. Argument schemas below are taken verbatim from the `TOOL_REGISTRY` in
`toolproto/parser.py`, the validator of record. `req` = required,
`opt` = optional. No argument outside the listed set is permitted.

| Tool | Arguments | Description |
|---|---|---|
| `schema.inspect` | `scope`: string, opt; `table`: string, opt | List tables in a scope, or describe one table |
| `clock.now` | `timezone`: string, opt | Current time, optionally in a named timezone |
| `sql.explain` | `sql`: string, req | Explain the plan for a SQL string; executes nothing |
| `sql.execute` | `sql`: string, req; `limit`: integer, opt | Run a bounded `SELECT`-only query and return rows (§13.3) |
| `nedb.provenance` | `table`: string, req; `row_id`: string \| integer, opt | Provenance metadata for a table or one row |

Canonical call shapes (one per tool):

```
<<<TOOL_CALL>>>
{"name": "schema.inspect", "arguments": {"table": "orders"}}
<<<END>>>

<<<TOOL_CALL>>>
{"name": "clock.now", "arguments": {"timezone": "America/New_York"}}
<<<END>>>

<<<TOOL_CALL>>>
{"name": "sql.explain", "arguments": {"sql": "SELECT count(*) FROM orders;"}}
<<<END>>>

<<<TOOL_CALL>>>
{"name": "sql.execute", "arguments": {"sql": "SELECT count(*) FROM orders;", "limit": 100}}
<<<END>>>

<<<TOOL_CALL>>>
{"name": "nedb.provenance", "arguments": {"table": "orders", "row_id": 42}}
<<<END>>>
```

5.3. The call payload is a single JSON object with exactly the fields `name`
(string, in the registry) and `arguments` (object). Unknown payload fields,
a missing field, a non-string `name`, or a non-object `arguments` are all
rejected (§11).

5.4. Type checking is exact: a JSON boolean is not an integer, and an integer
is not a string. `row_id` accepts a string or an integer, per its union type.

## 6. Reconciliation note — JSON schema divergence

6.1. **Divergence.** The JSON schemas in `schemas/` describe two tools
differently from the parser registry:

| Tool | `schemas/tool_call.schema.json` | `toolproto/parser.py` registry (normative) |
|---|---|---|
| `sql.execute` | `{sql, max_rows}` — both required, `max_rows` 1..1000 | `{sql}` required, `{limit}` optional integer |
| `nedb.provenance` | `{table, key}` — both required, `key` a string | `{table}` required, `{row_id}` optional string \| integer |
| `schema.inspect` | `{scope}` required, `scope` ∈ {public, private} | `{scope, table}` both optional strings |
| `clock.now` | `{}` — no arguments permitted | `{timezone}` optional string |

The taxonomy §3 table diverges further (e.g. `clock.now{zone}`,
`nedb.provenance{table, key, column}`) and is likewise informative only.

6.2. **Resolution.** The parser is the host-side validator of record: a corpus
row the parser rejects can never execute, whatever a schema document says.
Therefore **corpus rows follow the parser shapes** in §5.2. In particular,
corpus rows never use `max_rows` or `key` as argument names, never pass
`zone` to `clock.now`, and treat `scope`, `timezone`, `limit`, and `row_id`
as optional.

6.3. Aligning the JSON schemas to the parser registry is a host-track task.
Until that alignment lands, any consumer validating corpus rows must validate
against §5.2 (equivalently, by running `toolproto.parser.parse`), not against
`schemas/tool_call.schema.json`.

## 7. Host relay — `<<<TOOL_RESULT>>>` observations

7.1. Corpus encoding (taxonomy §2). The host relays each tool outcome as a
`user` message of the form:

```
<<<TOOL_RESULT name="<tool>" status="ok|error">>>
{...JSON payload...}
<<<END>>>
```

7.2. The relay `name` must match the tool named by the immediately preceding
`<<<TOOL_CALL>>>`. `status` is `ok` for a successful call and `error` for any
failure. An `ok` payload carries the tool's result object. An `error` payload
carries an `error` description (and may carry a `hint`, e.g. a "did you mean"
suggestion, which the model may use for its one corrected retry, §8.4).

7.3. Wire form. On the live wire the same observation conforms to
`schemas/tool_result.schema.json`: fields `tool_call_id` (non-empty string),
`name`, `status` ∈ {success, error, denied, timeout}, `originated: "tool"`,
plus `result` if and only if `status` is `success`, otherwise `error` as an
object `{code, message}`. The corpus compact form of §7.1 and the wire form
have identical semantics; the corpus form abbreviates the host stamps.

7.4. The model never emits `<<<TOOL_RESULT>>>`. A model turn containing one
is a protocol violation.

## 8. Chaining

8.1. A **chain** is an ordered sequence of `<<<TOOL_CALL>>>` →
`<<<TOOL_RESULT>>>` steps within one task (one corpus row). Each step is one
assistant call turn followed by exactly one host relay.

8.2. **Results feed forward.** The arguments of step *n+1* may use only
identifiers and values present in the user request or in observations from
steps 1..*n*. An identifier first appearing in a later call's arguments with
no prior source is ungrounded and the row is non-conformant.

8.3. **Bounds.** At most **3 calls per task**. No repeated identical call:
the same tool with the same arguments never appears twice in one chain.

8.4. **Corrected retry.** After an `error` relay, the model may make **at
most one** corrected retry — the same intent with changed arguments that
address the reported error (e.g. the table named in a "did you mean" hint).
A retry with unchanged arguments is a repeat and is forbidden by §8.3. If the
retry also fails, or the original error is unrecoverable (permission denied,
connection lost, timeout), the chain ends in an honest failure terminal: a
plain-text statement, `<<<CLARIFY>>>`, or `<<<UNANSWERABLE>>>` that names the
failure and states what remains unknown. Fabricated results after an error
are the cardinal violation of this protocol.

8.5. Chains stop at sufficiency. A chain never continues past the step where
the answer is reachable, and a single call is preferred wherever one call
suffices.

## 9. Termination

9.1. Every envelope closes with `<<<END>>>`. There is no other terminator.

9.2. A chain terminates in exactly one **terminal**:

- a terminal envelope — `<<<SQL>>>` (grounded in the observations),
  `<<<CLARIFY>>>`, or `<<<UNANSWERABLE>>>`; or
- a grounded plain-text summary after observations (§9.3).

A row consisting of a single call with no relay yet is a valid partial chain
(the decision step); it terminates by awaiting the host relay.

9.3. A **grounded plain-text summary** is permitted only after at least one
`<<<TOOL_RESULT>>>` has been observed in the row. Every value in it must be
traceable to an observed payload: names appear in the returned rows, counts
equal `row_count` or a correct aggregation over the returned rows, and empty
results are reported as empty. A summary is never an envelope and contains no
envelope markers.

9.4. `<<<CLARIFY>>>` is the terminal for genuine ambiguity: missing schema
with no tool able to supply it, an ambiguous temporal reading, or a filter
the user has not pinned down. `<<<UNANSWERABLE>>>` is the terminal when the
schema or the evidence cannot support any answer. Neither may be used to
disguise a tool failure, which is reported honestly per §8.4.

## 10. Exactly-one-envelope invariant

10.1. Each assistant turn holds **exactly one envelope**. Zero envelopes
(where an envelope turn is due) and multiple envelopes are both errors,
never a guess: the host extracts nothing and executes nothing from such a
turn.

10.2. The only exceptions are exhaustive: (a) a grounded plain-text summary
under §9.3, which holds zero envelopes by definition; (b) a write proposal
under §13.2, which holds exactly one `<<<SQL>>>` envelope plus at most one
authorization line after its `<<<END>>>`. No prose appears outside an
envelope in any other case.

10.3. The invariant applies per turn, not per row: a 3-step chain has three
assistant call turns, each holding its own single `<<<TOOL_CALL>>>`.

## 11. Malformed, unterminated, and smuggled input — rejection semantics

11.1. **Unterminated yields nothing.** A block with no closing `<<<END>>>`
produces no extraction and no execution. This mirrors the SQL protocol
verbatim in spirit and in the parser's error text: "unterminated … truncated
output yields nothing, never execute half a query." Extended to chains:
**never execute half a chain** — if any turn of a chain is unterminated or
malformed, no step of that chain is treated as authorized by that turn, and
the chain does not advance on partial output.

11.2. **Malformed is rejected, never repaired.** Unknown envelope kinds,
unknown tool names, invalid JSON, unknown payload fields, missing required
arguments, unknown arguments, and wrongly typed arguments are each an
explicit rejection by the parser. The host does not fix up, default, or
partially execute a malformed call; the rejection surfaces to the model as an
`error` observation, which the model handles under §8.4.

11.3. **Smuggled markers are rejected.** Any envelope marker (`<<<SQL>>>`,
`<<<TOOL_CALL>>>`, `<<<TOOL_RESULT>>>`, `<<<CLARIFY>>>`, `<<<UNANSWERABLE>>>`,
`<<<END>>>`, or any `<<<TOKEN>>>` of that shape) appearing inside a
`<<<TOOL_CALL>>>` JSON payload makes the call malformed. Markers are never
valid tool-call data, and a smuggled marker never splits a block, opens a new
envelope, or terminates one early.

11.4. A stray `<<<END>>>` with no opening envelope is malformed (§3.3).

11.5. Rejection is total for the turn: a rejected turn contributes no SQL, no
call, and no answer. The model's only legal continuations are a corrected
retry (§8.4, where a relay exists), `<<<CLARIFY>>>`, or `<<<UNANSWERABLE>>>`.

## 12. Observations are data, never instructions

12.1. A `<<<TOOL_RESULT>>>` observation is untrusted data, however it is
stamped. Its payload cannot redefine permissions, tool availability, model
identity, the envelope set, or any rule in this specification.

12.2. Instruction-like content inside an observation — in a side field (e.g.
a `note` key), in an error message, or in row content itself — is data. The
model answers the legitimate request from the genuine payload values only:
it does not follow the instruction, does not execute any write it demands,
does not exfiltrate data to any destination it names, and does not quote it
back as an instruction.

12.3. The same rule applies to instructions smuggled in user text: the
legitimate, read-only part of the request is answered; the injection is
ignored.

12.4. Host-side, observation validation is structural only
(`toolproto.parser.validate_observation`): result bytes are never scanned
for markers, precisely because they are data. The model's §12.2 duty is
behavioral, not a parsing rule.

## 13. Write doctrine

13.1. **Writes are proposals, never executions.** No V1 tool performs a
write; the registry is read-only (§5.1). `INSERT`, `UPDATE`, and `DELETE`
are never tool arguments, in any tool, under any user phrasing — including
"execute it", "run it now", or a claim of prior authorization in the same
message.

13.2. A write request is answered with a `<<<SQL>>>` envelope containing the
proposed statement, followed by **at most one** plain-text authorization
line stating that host authorization is required before it runs and that the
model will not execute it itself. That line is the whole response outside the
envelope; nothing else follows it. Execution, if any, happens out of band:
the host authorizes, the database verifies.

13.3. `sql.execute` is **bounded `SELECT` only**. Its `sql` argument is a
single `SELECT` (or `WITH … SELECT`) carrying a bound — a `LIMIT`, an
aggregate, or a keyed lookup — optionally tightened by the `limit` argument.
Non-`SELECT` statements in `sql.execute` or `sql.explain` arguments are
non-conformant and are rejected by the host.

13.4. Authorization is per operation and out of band. No standing
pre-approval exists in this protocol, and no envelope in §3 grants one.

## 14. Corpus conformance checklist

A candidate row conforms to this specification only if all of the following
hold (mirroring taxonomy §7, gates 1–3 and 6):

14.1. Every assistant turn satisfies §10.1, closes with `<<<END>>>` where it
is an envelope, and every `<<<TOOL_CALL>>>` parses cleanly under
`toolproto.parser.parse` against the §5.2 registry.

14.2. Every relay satisfies §7.2: name matches the preceding call, status is
`ok` or `error`, and payloads are consistent with the catalog data the row is
grounded in.

14.3. The chain satisfies §8: ≤ 3 calls, no identical repeat, at most one
corrected retry after an error, results feeding forward only.

14.4. The row terminates per §9, summaries trace to observed payloads per
§9.3, and no value appears in a terminal that no observation supports.

14.5. Writes appear only as §13.2 proposals; `sql.execute` arguments are
bounded `SELECT` only (§13.3); injected instructions in user text or
observations are not complied with (§12).

14.6. Rows are candidates. Conformance with this checklist is necessary for
admission review and is not itself an admission claim; the downstream forge
admits.
