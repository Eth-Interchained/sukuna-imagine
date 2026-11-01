#!/usr/bin/env python3
"""make_temporal_corpus.py -- sukuna-imagine v13 TEMPORAL training families.

Candidate generator ONLY: rows are candidates for the downstream forge, which
executes and admits. This script never claims admission.

Split discipline vs docs/tool-use/temporal_eval_spec.md (fixtures T-01..T-15,
clocks N1..N8) and curriculum_taxonomy.md sec. 8 -- hard rules enforced here
and re-checked by --self-check:
  (a) no DST-transition rows: no spring-forward / fall-back clocks or literals;
  (b) no leap-year February (February is excluded everywhere);
  (c) no year-end / quarter UTC-vs-local split clocks (N6/N7 patterns);
  (d) no explicit-user-zone rows (zones are harness session zones only and are
      never named in the question text);
  (e) no fall-back ambiguity / CLARIFY rows -- every row is one <<<SQL>>> block;
  (f) fixed clocks are disjoint from N1..N8;
  (g) normalized prompts are unique vs the spec's fixture inputs (denylist);
  (h) the spec's golden SQL strings never appear verbatim (denylist).
Relative references compile to CURRENT_DATE / CURRENT_TIMESTAMP arithmetic
with half-open [start, end) bounds; literals appear only where the user names
the date (named_* / asof / datecol families). Questions never state the fixed
clock's date. Schemas come from stealth.schemas.CATALOG TRAIN keys (held-out
telemetry is never touched; CATALOG has no timestamptz column, so calendar /
rolling families run on its date/timestamp columns) plus two in-generator
custom fixtures (temporal:asof, temporal:datecol) whose DDL + seed rows ride
inside each row's _verify block.
"""
from __future__ import annotations

import argparse
import calendar as _cal
import json
import re
import sys
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

# --------------------------------------------------------------------------
# eval quarantine: clocks N1..N8, normalized fixture inputs, golden SQL
# --------------------------------------------------------------------------

EVAL_CLOCKS = {
    "2026-10-13T14:00:00Z", "2026-01-15T08:00:00Z", "2024-03-01T05:00:00Z",
    "2026-03-08T07:30:00Z", "2026-11-01T05:30:00Z", "2027-01-01T04:59:59Z",
    "2026-07-01T03:30:00Z", "2026-10-13T23:30:00Z",
}


def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9 ]", " ", s.lower())).strip()


EVAL_PROMPTS = {_norm(p) for p in (
    "How many orders were placed yesterday?",
    "Show last month's completed orders.",
    "What is our revenue quarter to date?",
    "How many users signed up in the past 24 hours?",
    "Show orders placed between 1:00 AM and 2:00 AM on November 1st.",
    "All orders from February 2024.",
    "What were today's total sales?",
    "Revenue this quarter.",
    "How many orders came in today?",
    "Order count for the trailing 30 days.",
    "Signups last week.",
    "Orders from October 12.",
    "What is the current time at the Mint salon?",
    "What was customer 42's address on September 15, 2026?",
)}

GOLDEN_SQL = {_norm(s) for s in (
    "SELECT * FROM orders WHERE status = 'completed' AND placed_at >= "
    "date_trunc('month', CURRENT_DATE) - INTERVAL '1 month' AND placed_at < "
    "date_trunc('month', CURRENT_DATE);",
    "SELECT COUNT(*) FROM orders WHERE placed_at >= CURRENT_DATE - INTERVAL "
    "'1 day' AND placed_at < CURRENT_DATE;",
    "SELECT SUM(total) FROM orders WHERE placed_at >= date_trunc('quarter', "
    "CURRENT_DATE) AND placed_at < CURRENT_DATE + INTERVAL '1 day';",
    "SELECT COUNT(*) FROM users WHERE created_at >= CURRENT_TIMESTAMP - "
    "INTERVAL '24 hours';",
    "SELECT * FROM orders WHERE placed_at >= TIMESTAMPTZ '2024-02-01 "
    "00:00:00 America/New_York' AND placed_at < TIMESTAMPTZ '2024-03-01 "
    "00:00:00 America/New_York';",
    "SELECT SUM(amount) FROM sales WHERE sold_at >= CURRENT_DATE AND sold_at "
    "< CURRENT_DATE + INTERVAL '1 day';",
    "SELECT COUNT(*) FROM orders WHERE placed_at >= CURRENT_DATE AND "
    "placed_at < CURRENT_DATE + INTERVAL '1 day';",
    "SELECT COUNT(*) FROM orders WHERE placed_at >= CURRENT_TIMESTAMP - "
    "INTERVAL '30 days';",
    "SELECT COUNT(*) FROM users WHERE created_at >= date_trunc('week', "
    "CURRENT_DATE) - INTERVAL '1 week' AND created_at < date_trunc('week', "
    "CURRENT_DATE);",
    "SELECT * FROM orders WHERE order_date = DATE '2026-10-12';",
    "SELECT address FROM customer_addresses WHERE customer_id = 42 AND "
    "valid_from <= TIMESTAMPTZ '2026-09-15 00:00:00 America/New_York' AND "
    "(valid_to IS NULL OR valid_to > TIMESTAMPTZ '2026-09-15 00:00:00 "
    "America/New_York') ORDER BY valid_from DESC LIMIT 1;",
)}

# Training clocks: ordinary mid-period instants, disjoint from N1..N8, never
# on a DST transition day, never in February, never at a year/quarter edge.
CLOCKS = [
    ("2026-05-20T15:00:00Z", "America/New_York"),
    ("2026-08-19T16:30:00Z", "America/New_York"),
    ("2026-04-22T14:30:00Z", "America/Chicago"),
    ("2025-06-18T15:00:00Z", "America/New_York"),
    ("2025-09-17T16:00:00Z", "America/New_York"),
    ("2026-06-24T15:30:00Z", "America/Denver"),
]
ROLLOVER_CLOCKS = [  # clock sits on the 31st; "last month" bound math
    ("2026-05-31T15:00:00Z", "America/New_York"),
    ("2026-08-31T15:00:00Z", "America/New_York"),
    ("2026-01-31T16:00:00Z", "America/New_York"),
]

FAMILIES = (
    "prev_quarter", "quarter_to_date_ordinary", "year_to_date",
    "previous_year", "this_week", "this_month", "last_7_days",
    "past_24_hours", "trailing_12_months", "named_month_nonleap",
    "named_date_range", "month_end_rollover", "asof_event_time",
    "date_column_whole_day",
)
LITERAL_FAMILIES = {"named_month_nonleap", "named_date_range",
                    "asof_event_time", "date_column_whole_day"}
ROLLING_FAMILIES = {"last_7_days", "past_24_hours", "trailing_12_months"}

NAMED_MONTHS = [  # (year, month) -- never February, leap or not
    (2026, 3), (2025, 6), (2026, 9), (2025, 4), (2026, 8), (2025, 10),
    (2026, 5), (2025, 7), (2026, 11), (2025, 12), (2026, 6), (2025, 9),
]
NAMED_RANGES = [  # (from, to) inclusive in the question; never spans February
    (date(2026, 4, 6), date(2026, 4, 17)), (date(2025, 7, 7), date(2025, 7, 25)),
    (date(2026, 9, 8), date(2026, 9, 21)), (date(2025, 5, 12), date(2025, 5, 30)),
    (date(2026, 6, 3), date(2026, 6, 12)), (date(2025, 10, 6), date(2025, 10, 24)),
    (date(2026, 1, 12), date(2026, 1, 23)), (date(2025, 8, 4), date(2025, 8, 15)),
]
NAMED_DAYS = [date(2026, 4, 14), date(2025, 7, 22), date(2026, 9, 9),
              date(2025, 5, 6), date(2026, 6, 17), date(2025, 10, 21),
              date(2026, 1, 20), date(2025, 8, 13), date(2026, 5, 19),
              date(2025, 6, 16), date(2026, 8, 25), date(2025, 9, 16),
              date(2026, 11, 10), date(2025, 4, 9), date(2026, 7, 7),
              date(2025, 1, 27)]
ASOF_CASES = [  # (customer_id, as-of date) -- never customer 42 / 2026-09-15;
    # every date falls inside a seeded validity window for that customer
    (7, date(2025, 6, 10)), (7, date(2025, 9, 22)), (7, date(2026, 1, 19)),
    (7, date(2026, 4, 8)), (7, date(2025, 4, 14)), (7, date(2026, 7, 14)),
    (19, date(2025, 6, 10)), (19, date(2025, 9, 22)), (19, date(2026, 4, 8)),
    (19, date(2025, 11, 12)), (19, date(2025, 7, 22)), (19, date(2026, 7, 14)),
    (23, date(2026, 1, 19)), (23, date(2026, 4, 8)), (23, date(2026, 7, 14)),
    (23, date(2026, 5, 6)), (23, date(2026, 9, 9)), (23, date(2025, 12, 15)),
    (31, date(2025, 6, 10)), (31, date(2025, 9, 22)), (31, date(2026, 1, 19)),
    (31, date(2026, 4, 8)), (31, date(2025, 11, 12)), (31, date(2026, 7, 14)),
]

# --------------------------------------------------------------------------
# repo imports (system prompt + prompt rendering + catalog), with fallback
# --------------------------------------------------------------------------

_FALLBACK_SYSTEM = None  # filled from stealth.forge when the repo imports


def load_repo(repo: Path):
    """Load CATALOG + the forge SYSTEM prompt without importing stealth.forge
    (it hard-imports psycopg2, absent on candidate-only boxes). The SYSTEM
    string is extracted verbatim from forge.py's AST; build_prompt below is
    forge.build_prompt's exact rendering. stealth.schemas is dependency-free
    and is imported through a stub package so stealth/__init__ (which pulls
    the DB gate) never runs."""
    import ast
    import importlib.util
    import types
    pkg = types.ModuleType("stealth")
    pkg.__path__ = [str(repo / "stealth")]
    sys.modules.setdefault("stealth", pkg)
    spec = importlib.util.spec_from_file_location(
        "stealth.schemas", repo / "stealth" / "schemas.py")
    schemas = importlib.util.module_from_spec(spec)
    sys.modules["stealth.schemas"] = schemas
    spec.loader.exec_module(schemas)
    tree = ast.parse((repo / "stealth" / "forge.py").read_text())
    SYSTEM = None
    for node in tree.body:
        if isinstance(node, ast.Assign) and getattr(node.targets[0], "id", "") == "SYSTEM":
            SYSTEM = ast.literal_eval(node.value)
    if SYSTEM is None:
        raise RuntimeError("SYSTEM not found in stealth/forge.py")

    def build_prompt(schema, question, style="ddl"):
        return [
            {"role": "system", "content": SYSTEM},
            {"role": "user",
             "content": f"Schema:\n{schema.prompt_text(style)}\n\nQuestion: {question}"},
        ]

    return SYSTEM, build_prompt, schemas.CATALOG, schemas.TRAIN_KEYS, \
        schemas.Schema, schemas.Table, schemas.Column


# --------------------------------------------------------------------------
# custom fixtures (DDL + seeds travel inside _verify)
# --------------------------------------------------------------------------

def custom_schemas(Schema, Table, Column):
    def C(n, t, note=""):
        return Column(n, t, note)
    asof = Schema(
        key="temporal:asof", domain="temporal fixture (as-of / event time)",
        tables=[Table("customer_addresses", [
            C("id", "serial PRIMARY KEY"), C("customer_id", "int"),
            C("address", "text"), C("valid_from", "timestamptz"),
            C("valid_to", "timestamptz"), C("recorded_at", "timestamptz"),
        ], [
            (7, "12 Oak St", "2025-01-05T00:00:00Z", "2025-08-01T00:00:00Z",
             "2025-01-06T00:00:00Z"),
            (7, "48 Pine Ave", "2025-08-01T00:00:00Z", None,
             "2025-10-03T00:00:00Z"),  # backdated correction: recorded later
            (19, "9 Elm Rd", "2025-03-01T00:00:00Z", "2026-01-15T00:00:00Z",
             "2025-03-02T00:00:00Z"),
            (19, "221 Birch Ln", "2026-01-15T00:00:00Z", None,
             "2026-01-16T00:00:00Z"),
            (23, "5 Cedar Ct", "2025-12-01T00:00:00Z", None,
             "2025-12-02T00:00:00Z"),
            (31, "77 Maple Dr", "2025-05-20T00:00:00Z", "2025-12-20T00:00:00Z",
             "2025-05-21T00:00:00Z"),
            (31, "3 Walnut Way", "2025-12-20T00:00:00Z", None,
             "2026-01-10T00:00:00Z"),
        ])],
    )
    datecol = Schema(
        key="temporal:datecol", domain="temporal fixture (DATE column)",
        tables=[Table("invoices", [
            C("id", "serial PRIMARY KEY"), C("customer_id", "int"),
            C("amount", "numeric(10,2)"), C("invoice_date", "date"),
            C("issued_at", "timestamptz"),
        ], [
            (7, 120.00, "2026-04-14", "2026-04-14T13:00:00Z"),
            (19, 85.50, "2026-04-14", "2026-04-14T18:30:00Z"),
            (23, 42.00, "2026-04-15", "2026-04-15T09:00:00Z"),
            (7, 210.75, "2025-07-22", "2025-07-22T15:00:00Z"),
            (31, 64.25, "2026-09-09", "2026-09-09T11:00:00Z"),
        ])],
    )
    return {"temporal:asof": asof, "temporal:datecol": datecol}


def fixture_verify(schema) -> dict:
    rows = {}
    for t in schema.tables:
        nonserial = [c.name for c in t.columns if "serial" not in c.type.lower()]
        rows[t.name] = [dict(zip(nonserial, r)) for r in t.rows]
    return {"fixture_ddl": schema.ddl(), "fixture_rows": rows}


# --------------------------------------------------------------------------
# bound math (zoneinfo, session zone)
# --------------------------------------------------------------------------

def _midnight(d: date, zone: str) -> datetime:
    return datetime.combine(d, time.min, tzinfo=ZoneInfo(zone))


def _month_shift(d: date, months: int) -> date:
    m = d.month - 1 + months
    y = d.year + m // 12
    m = m % 12 + 1
    return date(y, m, min(d.day, _cal.monthrange(y, m)[1]))


def _quarter_start(d: date) -> date:
    return date(d.year, 3 * ((d.month - 1) // 3) + 1, 1)


def bounds_for(family: str, local: date, zone: str):
    """[start, end) local midnights for bounded calendar families."""
    M = lambda dd: _midnight(dd, zone).isoformat()  # noqa: E731
    if family == "prev_quarter":
        qs = _quarter_start(local)
        return [M(_month_shift(qs, -3)), M(qs)]
    if family == "quarter_to_date_ordinary":
        return [M(_quarter_start(local)), M(local + timedelta(days=1))]
    if family == "year_to_date":
        return [M(date(local.year, 1, 1)), M(local + timedelta(days=1))]
    if family == "previous_year":
        return [M(date(local.year - 1, 1, 1)), M(date(local.year, 1, 1))]
    if family == "this_week":  # Monday start, like date_trunc('week', ...)
        monday = local - timedelta(days=local.weekday())
        return [M(monday), M(local + timedelta(days=1))]
    if family == "this_month":
        return [M(date(local.year, local.month, 1)),
                M(local + timedelta(days=1))]
    if family in ("month_end_rollover",):
        first = date(local.year, local.month, 1)
        return [M(_month_shift(first, -1)), M(first)]
    raise ValueError(family)


def window_start(family: str, now_utc: datetime, zone: str) -> str:
    if family == "past_24_hours":
        return (now_utc - timedelta(hours=24)).isoformat()
    if family == "last_7_days":
        return (now_utc - timedelta(days=7)).isoformat()
    if family == "trailing_12_months":  # calendar months in the session zone
        local = now_utc.astimezone(ZoneInfo(zone))
        start_local = local.replace(
            year=_month_shift(local.date(), -12).year,
            month=_month_shift(local.date(), -12).month,
            day=_month_shift(local.date(), -12).day)
        return start_local.astimezone(timezone.utc).isoformat()
    raise ValueError(family)


# --------------------------------------------------------------------------
# catalog targets: (schema_key, table, time column, numeric col, filter col)
# --------------------------------------------------------------------------

TARGETS = [
    ("shop", "orders", "placed_at", "total", "status"),
    ("clinic", "appointments", "scheduled_for", "duration_min", "state"),
    ("fleet", "routes", '"runDate"', "total", "region"),
    ("library", "members", "joined", "fines_owed", None),
    ("audit", "activities", "occurred_at", None, "category"),
]

PREDICATES = {  # family -> (lower, upper) SQL fragments over {tc}
    "prev_quarter": (
        "date_trunc('quarter', CURRENT_DATE) - INTERVAL '3 months'",
        "date_trunc('quarter', CURRENT_DATE)"),
    "quarter_to_date_ordinary": (
        "date_trunc('quarter', CURRENT_DATE)", "CURRENT_DATE + INTERVAL '1 day'"),
    "year_to_date": (
        "date_trunc('year', CURRENT_DATE)", "CURRENT_DATE + INTERVAL '1 day'"),
    "previous_year": (
        "date_trunc('year', CURRENT_DATE) - INTERVAL '1 year'",
        "date_trunc('year', CURRENT_DATE)"),
    "this_week": (
        "date_trunc('week', CURRENT_DATE)", "CURRENT_DATE + INTERVAL '1 day'"),
    "this_month": (
        "date_trunc('month', CURRENT_DATE)", "CURRENT_DATE + INTERVAL '1 day'"),
    "month_end_rollover": (
        "date_trunc('month', CURRENT_DATE) - INTERVAL '1 month'",
        "date_trunc('month', CURRENT_DATE)"),
    "last_7_days": ("CURRENT_TIMESTAMP - INTERVAL '7 days'", None),
    "past_24_hours": ("CURRENT_TIMESTAMP - INTERVAL '24 hours'", None),
    "trailing_12_months": ("CURRENT_TIMESTAMP - INTERVAL '12 months'", None),
}

PHRASES = {  # family -> question fragments (never state the clock's date)
    "prev_quarter": ["in the previous quarter", "during the prior quarter",
                     "for the quarter before this one"],
    "quarter_to_date_ordinary": ["so far this quarter", "quarter to date",
                                 "since the start of this quarter"],
    "year_to_date": ["year to date", "so far this year",
                     "since the start of this year"],
    "previous_year": ["last year", "in the previous year", "during the prior year"],
    "this_week": ["this week", "so far this week", "since Monday"],
    "this_month": ["this month", "so far this month", "since the start of this month"],
    "last_7_days": ["in the last 7 days", "over the past 7 days",
                    "in the last seven days"],
    "past_24_hours": ["in the past 24 hours", "over the last 24 hours",
                      "during the past day"],
    "trailing_12_months": ["over the trailing 12 months", "in the last 12 months",
                           "over the past twelve months"],
    "month_end_rollover": ["last month", "in the previous month", "during last month"],
}


def seed_values(CATALOG, schema_key, table_name, col_name):
    schema = CATALOG[schema_key]
    t = next(t for t in schema.tables if t.name == table_name)
    nonserial = [c for c in t.columns if "serial" not in c.type.lower()]
    idx = [c.name for c in nonserial].index(col_name)
    vals = []
    for r in t.rows:
        if r[idx] is not None and r[idx] not in vals:
            vals.append(r[idx])
    return vals


# --------------------------------------------------------------------------
# row builders
# --------------------------------------------------------------------------

class Builder:
    def __init__(self, repo: Path, rng):
        (self.SYSTEM, self.build_prompt, self.CATALOG, self.TRAIN_KEYS,
         Schema, Table, Column) = load_repo(repo)
        self.custom = custom_schemas(Schema, Table, Column)
        self.rng = rng
        self._queues: dict = {}

    def _next(self, key, items):
        """Deterministic without-replacement draw: guarantees unique
        (case, target, phrasing) combinations within a run."""
        if key not in self._queues or not self._queues[key]:
            pool = list(items)
            self.rng.shuffle(pool)
            self._queues[key] = pool
        return self._queues[key].pop()

    def schema_for(self, key):
        return self.custom.get(key) or self.CATALOG[key]

    def row(self, schema_key, question, sql, reference_sql, family, verify):
        if _norm(sql) in GOLDEN_SQL or _norm(reference_sql) in GOLDEN_SQL:
            raise ValueError(f"golden SQL collision in family {family}: {sql}")
        if _norm(question) in EVAL_PROMPTS:
            raise ValueError(f"eval prompt collision in family {family}: {question}")
        schema = self.schema_for(schema_key)
        msgs = self.build_prompt(schema, question)
        msgs = [dict(m) for m in msgs]
        msgs.append({"role": "assistant",
                     "content": f"<<<SQL>>>\n{sql}\n<<<END>>>"})
        v = {"schema_key": schema_key, "reference_sql": reference_sql}
        v.update(verify)
        if schema_key in self.custom:
            v.update(fixture_verify(schema))
        return {"messages": msgs,
                "meta": {"kind": "temporal", "family": family,
                         "schema_key": schema_key},
                "_verify": v}

    # -- generic catalog families -------------------------------------------
    def catalog_row(self, family, clock):
        rng = self.rng
        now_utc_s, zone = clock
        now_utc = datetime.fromisoformat(now_utc_s.replace("Z", "+00:00"))
        local = now_utc.astimezone(ZoneInfo(zone)).date()
        combos = [(tg, ph, md, st)
                  for tg in TARGETS for ph in PHRASES[family]
                  for md in (("count", "star")
                             + (("sum",) if tg[3] else ())
                             + (("group",) if tg[4] else ()))
                  for st in (0, 1)]
        (skey, table, tcol, numcol, fcol), phrase, mode, style = \
            self._next(f"cat:{family}", combos)
        if mode == "sum" and not numcol:
            mode = "count"
        if mode == "group" and not fcol:
            mode = "star"
        schema = self.CATALOG[skey]
        t = next(t for t in schema.tables if t.name == table)
        tq = t.quoted
        lo, hi = PREDICATES[family]
        pred = f"{tcol} >= {lo}" + (f" AND {tcol} < {hi}" if hi else "")
        filt_sql, filt_q = "", ""
        if fcol and mode != "group" and rng.random() < 0.45:
            val = rng.choice(seed_values(self.CATALOG, skey, table, fcol))
            filt_sql = f" AND {fcol} = '{val}'"
            filt_q = f" where {fcol} is {val}"
        if mode == "count":
            proj = "COUNT(*)"
            q = (f"How many {table} have {tcol} {phrase}{filt_q}?" if style == 0
                 else f"Count the {table} with {tcol} {phrase}{filt_q}.")
        elif mode == "sum":
            proj = f"SUM({numcol}) AS total_{numcol}"
            q = (f"What is the total {numcol} for {table} with {tcol} {phrase}{filt_q}?"
                 if style == 0 else
                 f"Sum the {numcol} of {table} with {tcol} {phrase}{filt_q}.")
        elif mode == "group" and fcol:
            proj = f"{fcol}, COUNT(*)"
            q = (f"For {table} with {tcol} {phrase}, how many are there "
                 f"for each {fcol}?" if style == 0 else
                 f"Break down {table} with {tcol} {phrase} by {fcol}, "
                 f"with counts.")
        else:
            proj = "*"
            q = (f"Show {table} with {tcol} {phrase}{filt_q}." if style == 0
                 else f"List the {table} with {tcol} {phrase}{filt_q}.")
        sql = f"SELECT {proj} FROM {tq} WHERE {pred}{filt_sql}"
        if mode == "group":
            sql += f" GROUP BY {fcol}"
        sql += ";"
        ref = (f"SELECT {proj} FROM {tq} WHERE {filt_sql[5:] + ' AND ' if filt_sql else ''}"
               f"{pred};") if filt_sql else sql
        if mode == "group":
            ref = sql
        verify = {"now_utc": now_utc_s, "session_zone": zone}
        if family in ROLLING_FAMILIES:
            verify["expected_bounds"] = None
            verify["expected_window_start"] = window_start(family, now_utc, zone)
        else:
            verify["expected_bounds"] = bounds_for(family, local, zone)
        return self.row(skey, q, sql, ref, family, verify)

    # -- named month / range --------------------------------------------------
    def named_row(self, family, clock):
        rng = self.rng
        now_utc_s, zone = clock
        cases = NAMED_MONTHS if family == "named_month_nonleap" else NAMED_RANGES
        case, (skey, table, tcol, numcol, fcol) = self._next(
            f"named:{family}", [(c, tg) for c in cases for tg in TARGETS])
        schema = self.CATALOG[skey]
        t = next(t for t in schema.tables if t.name == table)
        is_date_col = "timestamp" not in next(
            c.type for c in t.columns if c.name.strip('"') == tcol.strip('"'))
        if family == "named_month_nonleap":
            y, m = case
            start, end = date(y, m, 1), _month_shift(date(y, m, 1), 1)
            label = f"{_cal.month_name[m]} {y}"
            q = f"Show all {table} with {tcol} in {label}."
        else:
            start, end_incl = case
            end = end_incl + timedelta(days=1)
            q = (f"Show all {table} with {tcol} from {start.isoformat()} "
                 f"to {end_incl.isoformat()}.")
        if is_date_col:
            lo = f"DATE '{start.isoformat()}'"
            hi = f"DATE '{end.isoformat()}'"
        else:
            lo = f"TIMESTAMPTZ '{start.isoformat()} 00:00:00 {zone}'"
            hi = f"TIMESTAMPTZ '{end.isoformat()} 00:00:00 {zone}'"
        sql = f"SELECT * FROM {t.quoted} WHERE {tcol} >= {lo} AND {tcol} < {hi};"
        verify = {"now_utc": now_utc_s, "session_zone": zone,
                  "expected_bounds": [_midnight(start, zone).isoformat(),
                                      _midnight(end, zone).isoformat()]}
        return self.row(skey, q, sql, sql, family, verify)

    # -- as-of (custom fixture) ----------------------------------------------
    def asof_row(self, clock):
        rng = self.rng
        now_utc_s, zone = clock
        cust, day = self._next("asof", ASOF_CASES)
        lit = f"TIMESTAMPTZ '{day.isoformat()} 00:00:00 {zone}'"
        sql = (f"SELECT address FROM customer_addresses WHERE customer_id = {cust} "
               f"AND valid_from <= {lit} AND (valid_to IS NULL OR valid_to > {lit}) "
               f"ORDER BY valid_from DESC LIMIT 1;")
        q = (f"What was customer {cust}'s address on "
             f"{_cal.month_name[day.month]} {day.day}, {day.year}?")
        verify = {"now_utc": now_utc_s, "session_zone": zone,
                  "expected_bounds": None,
                  "expected_asof": _midnight(day, zone).isoformat()}
        return self.row("temporal:asof", q, sql, sql, "asof_event_time", verify)

    # -- whole-day on a DATE column -------------------------------------------
    def datecol_row(self, clock):
        rng = self.rng
        now_utc_s, zone = clock
        day, variant = self._next(
            "datecol", [(d, v) for d in NAMED_DAYS for v in (0, 1)])
        label = f"{_cal.month_name[day.month]} {day.day}, {day.year}"
        verify = {"now_utc": now_utc_s, "session_zone": zone,
                  "expected_bounds": [_midnight(day, zone).isoformat(),
                                      _midnight(day + timedelta(days=1),
                                                zone).isoformat()]}
        if variant == 0:  # catalog: shop.orders.placed_at is DATE
            sql = f"SELECT * FROM orders WHERE placed_at = DATE '{day.isoformat()}';"
            q = f"Show orders with placed_at on {label}."
            return self.row("shop", q, sql, sql, "date_column_whole_day", verify)
        sql = f"SELECT * FROM invoices WHERE invoice_date = DATE '{day.isoformat()}';"
        q = f"Show invoices with invoice_date on {label}."
        return self.row("temporal:datecol", q, sql, sql,
                        "date_column_whole_day", verify)

    def make(self, family, i):
        if family == "month_end_rollover":
            return self.catalog_row(family, ROLLOVER_CLOCKS[i % len(ROLLOVER_CLOCKS)])
        clock = CLOCKS[i % len(CLOCKS)]
        if family in ("named_month_nonleap", "named_date_range"):
            return self.named_row(family, clock)
        if family == "asof_event_time":
            return self.asof_row(clock)
        if family == "date_column_whole_day":
            return self.datecol_row(clock)
        return self.catalog_row(family, clock)


# --------------------------------------------------------------------------
# self-check
# --------------------------------------------------------------------------

_LITERAL = re.compile(
    r"(?:DATE|TIMESTAMP|TIMESTAMPTZ)\s*'\d{4}-\d{2}-\d{2}|'\d{4}-\d{2}-\d{2}", re.I)
_BLOCK = re.compile(r"<<<SQL>>>\n(.*)\n<<<END>>>", re.S)


def self_check(rows) -> list[str]:
    errs = []
    seen_q = set()
    for i, r in enumerate(rows):
        fam = r["meta"]["family"]
        msgs = r["messages"]
        asst = [m for m in msgs if m["role"] == "assistant"]
        if len(asst) != 1:
            errs.append(f"row {i}: {len(asst)} assistant turns")
            continue
        content = asst[0]["content"]
        m = _BLOCK.fullmatch(content.strip())
        if not m or content.count("<<<SQL>>>") != 1 or content.count("<<<END>>>") != 1:
            errs.append(f"row {i}: not exactly one END-terminated <<<SQL>>> block")
            continue
        sql = m.group(1)
        if _norm(sql) in GOLDEN_SQL:
            errs.append(f"row {i}: golden SQL verbatim")
        if "BETWEEN" in sql.upper() or "23:59:59" in sql:
            errs.append(f"row {i}: non-half-open bound")
        if fam == "asof_event_time" and re.search(r"recorded_at\s*(<=|>=|<|>|=)", sql):
            errs.append(f"row {i}: asof predicates on recorded_at")
        if fam not in LITERAL_FAMILIES and _LITERAL.search(sql):
            errs.append(f"row {i}: calendar literal in relative family {fam}")
        if fam not in LITERAL_FAMILIES and fam not in ROLLING_FAMILIES \
                and not re.search(r">=\s*\S+.*<\s*\S+", sql, re.S):
            errs.append(f"row {i}: bounded family without >= and <")
        user = next(m2["content"] for m2 in msgs if m2["role"] == "user")
        q = user.split("Question: ", 1)[-1]
        if _norm(q) in EVAL_PROMPTS:
            errs.append(f"row {i}: prompt collides with eval fixture")
        if _norm(q) in seen_q:
            errs.append(f"row {i}: duplicate normalized prompt")
        seen_q.add(_norm(q))
        v = r["_verify"]
        if v["now_utc"] in EVAL_CLOCKS:
            errs.append(f"row {i}: clock collides with N1..N8")
        if fam in ROLLING_FAMILIES and not v.get("expected_window_start"):
            errs.append(f"row {i}: rolling row without expected_window_start")
        if fam not in ROLLING_FAMILIES and fam != "asof_event_time" \
                and not v.get("expected_bounds"):
            errs.append(f"row {i}: bounded row without expected_bounds")
    return errs


# --------------------------------------------------------------------------

def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--count", type=int, default=300)
    ap.add_argument("--seed", type=int, default=29)
    ap.add_argument("--out", default="-")
    ap.add_argument("--repo", default=str(Path.home() / "workspace" / "sukuna-imagine"))
    ap.add_argument("--self-check", action="store_true")
    a = ap.parse_args()
    import random
    rng = random.Random(a.seed)
    b = Builder(Path(a.repo), rng)
    rows, per = [], {f: 0 for f in FAMILIES}
    for i in range(a.count):
        fam = FAMILIES[i % len(FAMILIES)]
        rows.append(b.make(fam, per[fam]))
        per[fam] += 1
    rng.shuffle(rows)
    text = "".join(json.dumps(r) + "\n" for r in rows)
    if a.out == "-":
        sys.stdout.write(text)
    else:
        Path(a.out).write_text(text)
    if a.self_check:
        errs = self_check(rows)
        tally = {}
        for r in rows:
            tally[r["meta"]["family"]] = tally.get(r["meta"]["family"], 0) + 1
        print(f"self-check: {'PASS' if not errs else 'FAIL'} "
              f"({len(rows)} rows)", file=sys.stderr)
        for f in FAMILIES:
            print(f"  {f}: {tally.get(f, 0)}", file=sys.stderr)
        for e in errs[:20]:
            print(f"  ERROR {e}", file=sys.stderr)
        return 1 if errs else 0
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
