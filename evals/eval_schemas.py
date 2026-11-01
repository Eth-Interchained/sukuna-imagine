#!/usr/bin/env python3
"""eval_schemas.py -- sukuna-imagine v13 HELD-OUT eval fixture schemas.

SPLIT INTEGRITY (curriculum_taxonomy.md section 8, rule 2): eval uses table
and column names never seen in train. The training discrimination generators
draw schemas ONLY from stealth.schemas.CATALOG (clinic/shop/fleet/library/
audit style keys); the telemetry regression eval uses schema_key "telemetry".
These five schemas are defined here, registered under schema_keys
"eval:bookings", "eval:stylists", "eval:deliveries", "eval:invoices" and
"eval:tickets", and exist nowhere on the training side. No eval builder may
import stealth.schemas or read corpus/eval.jsonl.

Each schema: DDL text (the exact text rendered into prompts) plus small seed
rows as dicts. Seed rows include boundary edge rows where relevant: midnight
00:00:00 rows, 23:59:59 rows, a leap-day row, NULLs, and status variety, so a
harness can plant a fixture DB and execute reference SQL against it.
"""
from __future__ import annotations

# --------------------------------------------------------------------------
# bookings -- salon-style appointments (eval analogue, NOT a training table)
# --------------------------------------------------------------------------
BOOKINGS_DDL = """CREATE TABLE bookings (
  id integer PRIMARY KEY,
  guest_name text NOT NULL,
  stylist_id integer,
  service text,
  starts_at timestamptz,
  booking_date date,
  status text,  -- confirmed|completed|cancelled|no_show
  price numeric(8,2)
);"""

BOOKINGS_ROWS = [
    {"id": 1, "guest_name": "Priya Nair", "stylist_id": 2, "service": "haircut",
     "starts_at": "2026-10-12T00:00:00-04:00", "booking_date": "2026-10-12",
     "status": "completed", "price": "85.00"},   # midnight boundary: starts the day
    {"id": 2, "guest_name": "Tom Reyes", "stylist_id": 1, "service": "colour",
     "starts_at": "2026-10-12T23:59:59-04:00", "booking_date": "2026-10-12",
     "status": "completed", "price": "150.00"},  # last second of the day
    {"id": 3, "guest_name": "Ana Sousa", "stylist_id": 2, "service": "haircut",
     "starts_at": "2026-10-13T00:00:00-04:00", "booking_date": "2026-10-13",
     "status": "confirmed", "price": "85.00"},   # next day's midnight: excluded from Oct 12
    {"id": 4, "guest_name": "Ben Okafor", "stylist_id": 3, "service": "treatment",
     "starts_at": "2024-02-29T12:00:00-05:00", "booking_date": "2024-02-29",
     "status": "completed", "price": "120.00"},  # leap-day row
    {"id": 5, "guest_name": "Cleo March", "stylist_id": 1, "service": "colour",
     "starts_at": "2026-10-10T15:30:00-04:00", "booking_date": "2026-10-10",
     "status": "cancelled", "price": "150.00"},
    {"id": 6, "guest_name": "Dan Whit", "stylist_id": None, "service": "haircut",
     "starts_at": "2026-10-11T09:00:00-04:00", "booking_date": "2026-10-11",
     "status": "no_show", "price": None},        # NULL stylist and NULL price
]

STYLISTS_DDL = """CREATE TABLE stylists (
  id integer PRIMARY KEY,
  full_name text NOT NULL,
  level integer,
  hourly_rate numeric(8,2),
  active boolean
);"""

STYLISTS_ROWS = [
    {"id": 1, "full_name": "Rosa Lima", "level": 8, "hourly_rate": "95.00", "active": True},
    {"id": 2, "full_name": "June Park", "level": 6, "hourly_rate": "75.00", "active": True},
    {"id": 3, "full_name": "Omar Haddad", "level": 4, "hourly_rate": "55.00", "active": True},
    {"id": 4, "full_name": "Ivy Chen", "level": 2, "hourly_rate": "40.00", "active": False},
]

DELIVERIES_DDL = """CREATE TABLE deliveries (
  id integer PRIMARY KEY,
  supplier text NOT NULL,
  due_at timestamptz,
  arrived_at timestamptz,
  delivery_date date,
  status text,  -- delivered|in_transit|lost
  cost numeric(8,2)
);"""

DELIVERIES_ROWS = [
    {"id": 1, "supplier": "Acme Parts", "due_at": "2026-10-12T09:00:00-04:00",
     "arrived_at": "2026-10-12T08:45:00-04:00", "delivery_date": "2026-10-12",
     "status": "delivered", "cost": "240.00"},
    {"id": 2, "supplier": "Acme Parts", "due_at": "2026-10-12T09:00:00-04:00",
     "arrived_at": "2026-10-13T00:00:00-04:00", "delivery_date": "2026-10-13",
     "status": "delivered", "cost": "180.00"},   # arrived exactly at midnight: late, next day
    {"id": 3, "supplier": "Northbeam", "due_at": "2026-10-11T17:00:00-04:00",
     "arrived_at": None, "delivery_date": "2026-10-11",
     "status": "in_transit", "cost": "310.00"},  # NULL arrived_at: not yet late-countable
    {"id": 4, "supplier": "Northbeam", "due_at": "2024-02-29T10:00:00-05:00",
     "arrived_at": "2024-03-01T00:00:00-05:00", "delivery_date": "2024-03-01",
     "status": "delivered", "cost": "95.00"},    # leap-day due, midnight arrival
    {"id": 5, "supplier": "Kite Supply", "due_at": "2026-10-09T12:00:00-04:00",
     "arrived_at": "2026-10-09T11:59:59-04:00", "delivery_date": "2026-10-09",
     "status": "delivered", "cost": "60.00"},
]

INVOICES_DDL = """CREATE TABLE invoices (
  id integer PRIMARY KEY,
  client_name text NOT NULL,
  issued_at timestamptz,
  due_date date,
  amount numeric(10,2),
  paid boolean,
  status text  -- open|paid|void
);"""

INVOICES_ROWS = [
    {"id": 1, "client_name": "Harbor LLC", "issued_at": "2026-10-01T00:00:00-04:00",
     "due_date": "2026-10-31", "amount": "1200.00", "paid": False, "status": "open"},
    {"id": 2, "client_name": "Harbor LLC", "issued_at": "2026-09-15T10:20:00-04:00",
     "due_date": "2026-10-15", "amount": "450.00", "paid": True, "status": "paid"},
    {"id": 3, "client_name": "Meadow Co", "issued_at": "2026-10-12T23:59:59-04:00",
     "due_date": "2026-11-11", "amount": "275.50", "paid": False, "status": "open"},
    {"id": 4, "client_name": "Meadow Co", "issued_at": "2024-02-29T08:00:00-05:00",
     "due_date": "2024-03-30", "amount": "999.99", "paid": True, "status": "paid"},
    {"id": 5, "client_name": "Quartz Inc", "issued_at": "2026-10-05T14:00:00-04:00",
     "due_date": "2026-11-04", "amount": None, "paid": False, "status": "void"},
]

TICKETS_DDL = """CREATE TABLE tickets (
  id integer PRIMARY KEY,
  requester text NOT NULL,
  subject text,
  opened_at timestamptz,
  priority text,  -- low|normal|high|urgent
  status text,  -- open|pending|closed
  assignee text
);"""

TICKETS_ROWS = [
    {"id": 1, "requester": "nina@example.com", "subject": "Login fails",
     "opened_at": "2026-10-12T00:00:00-04:00", "priority": "high",
     "status": "open", "assignee": "Rosa Lima"},
    {"id": 2, "requester": "ole@example.com", "subject": "Billing question",
     "opened_at": "2026-10-12T23:59:59-04:00", "priority": "normal",
     "status": "pending", "assignee": "June Park"},
    {"id": 3, "requester": "pam@example.com", "subject": "Feature request",
     "opened_at": "2026-10-13T00:00:00-04:00", "priority": "low",
     "status": "open", "assignee": None},        # NULL assignee
    {"id": 4, "requester": "quinn@example.com", "subject": "Outage report",
     "opened_at": "2026-10-08T07:15:00-04:00", "priority": "urgent",
     "status": "closed", "assignee": "Rosa Lima"},
    {"id": 5, "requester": "raj@example.com", "subject": "Password reset",
     "opened_at": "2024-02-29T23:59:59-05:00", "priority": "normal",
     "status": "closed", "assignee": "Omar Haddad"},  # leap-day edge row
]

# --------------------------------------------------------------------------
# registry: schema_key -> {tables, ddl, rows by table, primary table facts}
# --------------------------------------------------------------------------
SCHEMAS = {
    "eval:bookings": {
        "tables": ["bookings"], "ddl": BOOKINGS_DDL,
        "rows": {"bookings": BOOKINGS_ROWS},
        "table": "bookings", "name_col": "guest_name", "group_col": "status",
        "ts_col": "starts_at", "date_col": "booking_date",
        "num_col": "price", "id_col": "id",
    },
    "eval:stylists": {
        "tables": ["stylists"], "ddl": STYLISTS_DDL,
        "rows": {"stylists": STYLISTS_ROWS},
        "table": "stylists", "name_col": "full_name", "group_col": "level",
        "ts_col": None, "date_col": None,
        "num_col": "hourly_rate", "id_col": "id",
    },
    "eval:deliveries": {
        "tables": ["deliveries"], "ddl": DELIVERIES_DDL,
        "rows": {"deliveries": DELIVERIES_ROWS},
        "table": "deliveries", "name_col": "supplier", "group_col": "supplier",
        "ts_col": "due_at", "date_col": "delivery_date",
        "num_col": "cost", "id_col": "id",
    },
    "eval:invoices": {
        "tables": ["invoices"], "ddl": INVOICES_DDL,
        "rows": {"invoices": INVOICES_ROWS},
        "table": "invoices", "name_col": "client_name", "group_col": "status",
        "ts_col": "issued_at", "date_col": "due_date",
        "num_col": "amount", "id_col": "id",
    },
    "eval:tickets": {
        "tables": ["tickets"], "ddl": TICKETS_DDL,
        "rows": {"tickets": TICKETS_ROWS},
        "table": "tickets", "name_col": "requester", "group_col": "priority",
        "ts_col": "opened_at", "date_col": None,
        "num_col": None, "id_col": "id",
    },
}

EVAL_SCHEMA_KEYS = tuple(SCHEMAS)


def ddl_for(schema_key: str) -> str:
    return SCHEMAS[schema_key]["ddl"]


def prompt_with_schema(schema_key: str, question: str) -> str:
    """Training-convention rendering: schema in the prompt, then the question."""
    return "Schema:\n%s\n\nQuestion: %s" % (ddl_for(schema_key), question)


def prompt_without_schema(question: str) -> str:
    return "Question: %s" % question
