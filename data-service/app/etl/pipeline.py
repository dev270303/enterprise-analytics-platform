"""CSV -> validate -> clean -> transform -> PostgreSQL.

Design
------
* Extract   : read as strings only (never let pandas guess types), normalise headers, map aliases.
* Validate  : file-level (required columns), row-level (types, enums, ranges), cross-field
              (timelines), referential (business keys must exist in the warehouse).
* Clean     : standardise formats (dates, money, %, enums, city synonyms, names, e-mails).
* Dedupe    : exact duplicate rows are dropped; duplicate business keys keep the last occurrence.
* Outliers  : flagged as WARNINGs (kept): price far from list price, extreme quantities.
* Load      : COPY into a temp staging table, then one set-based INSERT ... ON CONFLICT DO UPDATE
              per entity. Re-running the same file is idempotent.
* Audit     : every run and every issue is written to etl_runs / etl_issues on a separate
              autocommit connection, so DRY_RUN reports survive the rollback.

Severity
--------
ERROR   row rejected            WARNING  row loaded, flagged            INFO  informational
"""
from __future__ import annotations

import io
import time
from collections import Counter
from dataclasses import dataclass, field, asdict
from datetime import date, datetime
from statistics import median
from typing import Iterable

import pandas as pd
import psycopg

from app.db import connect
from app.etl import cleaning as C
from app.etl.specs import SPECS, SLA_HOURS, EntitySpec, detect_entity, resolve_alias

MAX_FILE_ROWS = 100_000
MAX_STORED_ISSUES = 2_000
PRICE_DEVIATION_LIMIT = 0.40     # unit price more than 40% away from list price -> outlier warning


@dataclass
class Issue:
    line: int | None
    severity: str
    rule: str
    column: str | None
    raw_value: str | None
    message: str


@dataclass
class RunResult:
    run_id: int | None
    entity: str | None
    file_name: str
    mode: str
    status: str = "RUNNING"
    rows_in: int = 0
    rows_loaded: int = 0
    rows_inserted: int = 0
    rows_updated: int = 0
    rows_rejected: int = 0
    rows_duplicate: int = 0
    rows_flagged: int = 0
    quality_score: float | None = None
    duration_ms: int = 0
    error: str | None = None
    issues: list[Issue] = field(default_factory=list)

    def to_dict(self, max_issues: int = 200) -> dict:
        data = asdict(self)
        data["issues"] = [asdict(i) for i in self.issues[:max_issues]]
        data["issue_summary"] = summarize_issues(self.issues)
        data["issues_truncated"] = len(self.issues) > max_issues
        return data


def summarize_issues(issues: Iterable[Issue]) -> list[dict]:
    counts = Counter((i.severity, i.rule, i.column) for i in issues)
    order = {"ERROR": 0, "WARNING": 1, "INFO": 2}
    return [{"severity": s, "rule": r, "column": c, "count": n}
            for (s, r, c), n in sorted(counts.items(), key=lambda kv: (order[kv[0][0]], -kv[1]))]


# --------------------------------------------------------------------------- extract

def read_csv(content: bytes) -> pd.DataFrame:
    for encoding in ("utf-8-sig", "cp1252", "latin-1"):
        try:
            text = content.decode(encoding)
            break
        except UnicodeDecodeError:
            continue
    df = pd.read_csv(io.StringIO(text), dtype=str, keep_default_na=False, skipinitialspace=True,
                     skip_blank_lines=True)
    df.columns = [C.normalize_header(c) for c in df.columns]
    return df


# --------------------------------------------------------------------------- main entry points

def run_file(file_name: str, content: bytes, *, entity: str | None = None, mode: str = "DRY_RUN",
             source: str = "upload", triggered_by: str | None = None) -> RunResult:
    """Process one file in its own transaction."""
    return run_batch([(file_name, content, entity)], mode=mode, source=source, triggered_by=triggered_by)[0]


def run_batch(files: list[tuple[str, bytes, str | None]], *, mode: str = "DRY_RUN", source: str = "upload",
              triggered_by: str | None = None) -> list[RunResult]:
    """Process several files atomically, in dependency order (customers before orders, ...).

    COMMIT  : everything is committed together at the end (or nothing, on an unexpected error).
    DRY_RUN : identical processing, then rolled back. Reports are still stored.
    """
    if mode not in ("COMMIT", "DRY_RUN"):
        raise ValueError("mode must be COMMIT or DRY_RUN")

    prepared = []
    for name, content, entity in files:
        df = None
        detected = entity
        error = None
        try:
            df = read_csv(content)
            detected = entity or detect_entity(list(df.columns), name)
            if detected not in SPECS:
                error = "Could not tell which kind of data this file holds. Name it e.g. orders.csv or pick the type."
        except Exception as exc:  # unreadable file
            error = f"Could not read CSV: {exc}"
        prepared.append((name, df, detected, error))
    prepared.sort(key=lambda p: SPECS[p[2]].load_order if p[2] in SPECS else 99)

    results: list[RunResult] = []
    with connect() as conn:
        try:
            # Open the outer transaction explicitly: the per-file conn.transaction() blocks below
            # then become SAVEPOINTs, and only this function decides COMMIT vs ROLLBACK.
            conn.execute("SELECT 1")
            for name, df, entity, error in prepared:
                result = RunResult(run_id=None, entity=entity, file_name=name, mode=mode)
                started = time.monotonic()
                result.run_id = _audit_start(result, source, triggered_by)
                if error:
                    result.status, result.error = "FAILED", error
                    result.issues.append(Issue(None, "ERROR", "unreadable_file", None, None, error))
                else:
                    try:
                        with conn.transaction():          # savepoint per file
                            _process(conn, SPECS[entity], df, result)
                    except Exception as exc:
                        result.status, result.error = "FAILED", f"{type(exc).__name__}: {exc}"
                        result.issues.append(Issue(None, "ERROR", "load_failed", None, None, str(exc)[:500]))
                result.duration_ms = int((time.monotonic() - started) * 1000)
                results.append(result)
            if mode == "COMMIT":
                conn.commit()
            else:
                conn.rollback()
        except Exception:
            conn.rollback()
            raise
        finally:
            for r in results:
                _audit_finish(r)
    return results


# --------------------------------------------------------------------------- processing

def _process(conn: psycopg.Connection, spec: EntitySpec, df: pd.DataFrame, result: RunResult) -> None:
    issues = result.issues

    # --- headers
    rename = {}
    for header in df.columns:
        canonical = resolve_alias(spec, header)
        if canonical in {c.name for c in spec.columns}:
            rename[header] = canonical
        else:
            issues.append(Issue(None, "INFO", "ignored_column", header, None, f"column '{header}' is not used"))
    df = df.rename(columns=rename)
    df = df.loc[:, ~df.columns.duplicated()]
    missing_cols = [c.name for c in spec.columns if c.required and c.name not in df.columns]
    if missing_cols:
        msg = f"missing required column(s): {', '.join(missing_cols)}"
        issues.append(Issue(None, "ERROR", "missing_column", ",".join(missing_cols), None, msg))
        result.status, result.error = "FAILED", msg
        return
    if len(df) > MAX_FILE_ROWS:
        raise ValueError(f"file has {len(df)} rows; the limit is {MAX_FILE_ROWS}")

    # --- drop blank rows, keep spreadsheet line numbers (header = line 1)
    df = df.assign(_line=range(2, len(df) + 2))
    data_cols = [c.name for c in spec.columns if c.name in df.columns]
    blank = df[data_cols].apply(lambda col: col.map(C.is_missing)).all(axis=1)
    df = df[~blank]
    result.rows_in = len(df)

    # --- exact duplicates (all business columns equal)
    dup_mask = df.duplicated(subset=data_cols, keep="first")
    for line in df.loc[dup_mask, "_line"]:
        issues.append(Issue(int(line), "INFO", "duplicate_row", None, None, "exact duplicate of an earlier row; skipped"))
    result.rows_duplicate += int(dup_mask.sum())
    df = df[~dup_mask]

    # --- clean / type every value
    lookups = _load_lookups(conn, spec, df)
    rows: list[dict] = []
    rejected_lines: set[int] = set()
    flagged_lines: set[int] = set()
    for record in df.to_dict(orient="records"):
        line = int(record["_line"])
        row = {"_line": line}
        for col in spec.columns:
            raw = record.get(col.name)
            if raw is None or C.is_missing(raw):
                row[col.name] = None
                if col.required:
                    issues.append(Issue(line, "ERROR", "missing_required", col.name, None, f"{col.name} is required"))
                    rejected_lines.add(line)
                continue
            try:
                row[col.name] = _clean_value(col, raw, lookups)
            except C.CleanError as err:
                row[col.name] = None
                severity = "ERROR" if col.required else "WARNING"
                issues.append(Issue(line, severity, err.rule, col.name, str(raw)[:200], str(err)))
                (rejected_lines if col.required else flagged_lines).add(line)
        rows.append(row)

    # --- entity rules + references
    checker = _RULES[spec.name]
    for row in rows:
        if row["_line"] in rejected_lines:
            continue
        for severity, rule, column, raw, message in checker(row, lookups):
            issues.append(Issue(row["_line"], severity, rule, column, raw, message))
            if severity == "ERROR":
                rejected_lines.add(row["_line"])
            elif severity == "WARNING":
                flagged_lines.add(row["_line"])

    valid = [r for r in rows if r["_line"] not in rejected_lines]

    # --- duplicate business keys: last occurrence wins
    seen: dict[tuple, dict] = {}
    for row in valid:
        key = tuple(row[k] for k in spec.key)
        if key in seen:
            prev = seen[key]
            issues.append(Issue(prev["_line"], "WARNING", "duplicate_key", ",".join(spec.key), str(key),
                                f"same {'/'.join(spec.key)} appears again on line {row['_line']}; later row kept"))
            result.rows_duplicate += 1
        seen[key] = row
    valid = list(seen.values())

    # --- statistical outliers (batch level)
    for line, column, raw, message in _OUTLIERS.get(spec.name, lambda rows, lk: [])(valid, lookups):
        issues.append(Issue(line, "WARNING", "outlier", column, raw, message))
        flagged_lines.add(line)

    result.rows_rejected = len(rejected_lines)
    valid_lines = {r["_line"] for r in valid}
    result.rows_flagged = len(flagged_lines & valid_lines)

    # --- load
    if valid:
        inserted, updated = _LOADERS[spec.name](conn, valid, lookups, result.run_id)
        result.rows_inserted, result.rows_updated = inserted, updated
        result.rows_loaded = inserted + updated
    clean_rows = len(valid_lines - flagged_lines)
    result.quality_score = round(100.0 * clean_rows / result.rows_in, 2) if result.rows_in else None
    result.status = "SUCCESS" if result.rows_rejected == 0 and result.rows_loaded == len(valid) else "PARTIAL"
    if result.rows_in and not valid:
        result.status = "FAILED"
        result.error = "no valid rows"


def _clean_value(col, raw, lookups):
    kind = col.kind
    if kind == "text":
        return C.clean_text(raw)
    if kind == "name":
        return C.clean_name(raw)
    if kind == "email":
        return C.clean_email(raw)
    if kind == "code":
        return C.clean_code(raw, col.pattern)
    if kind == "date":
        return C.parse_date(raw)
    if kind == "datetime":
        return C.parse_datetime(raw)
    if kind == "money":
        return C.parse_money(raw)
    if kind == "int":
        return C.parse_int(raw)
    if kind == "fraction":
        return C.parse_fraction(raw)
    if kind == "bool":
        return C.parse_bool(raw)
    if kind == "enum":
        return C.normalize_enum(raw, col.enum)
    if kind == "city":
        return C.canonical_city(raw, set(lookups["cities"]))
    raise ValueError(f"unknown column kind {kind}")


# --------------------------------------------------------------------------- lookups

def _values(df: pd.DataFrame, column: str, transform) -> list:
    if column not in df.columns:
        return []
    out = set()
    for v in df[column]:
        if not C.is_missing(v):
            try:
                out.add(transform(v))
            except C.CleanError:
                pass
    return sorted(out)


def _load_lookups(conn, spec: EntitySpec, df: pd.DataFrame) -> dict:
    """Fetch only the reference keys this file mentions (keeps queries small)."""
    cur = conn.cursor()
    lk: dict = {}
    cur.execute("SELECT id, name FROM cities")
    lk["cities"] = {r["name"]: r["id"] for r in cur.fetchall()}
    if "customer_email" in df.columns and spec.name != "customers":
        emails = _values(df, "customer_email", C.clean_email)
        cur.execute("SELECT id, email, signup_date FROM customers WHERE email = ANY(%s)", (emails,))
        lk["customers"] = {r["email"]: r for r in cur.fetchall()}
    for col in ("sales_rep_code", "assignee_code"):
        if col in df.columns:
            codes = _values(df, col, C.clean_code)
            cur.execute("SELECT id, employee_code FROM employees WHERE employee_code = ANY(%s)", (codes,))
            lk.setdefault("employees", {}).update({r["employee_code"]: r["id"] for r in cur.fetchall()})
    if "product_sku" in df.columns:
        skus = _values(df, "product_sku", C.clean_code)
        cur.execute("SELECT id, sku, unit_price FROM products WHERE sku = ANY(%s)", (skus,))
        lk["products"] = {r["sku"]: r for r in cur.fetchall()}
    if "order_no" in df.columns and spec.name != "orders":
        nos = _values(df, "order_no", C.clean_code)
        cur.execute("SELECT id, order_no, order_date FROM orders WHERE order_no = ANY(%s)", (nos,))
        lk["orders"] = {r["order_no"]: r for r in cur.fetchall()}
    return lk


# --------------------------------------------------------------------------- rules

def _today() -> date:
    return date.today()


def _rules_customers(row, lk):
    if row["signup_date"] and row["signup_date"] > _today():
        yield "ERROR", "future_date", "signup_date", str(row["signup_date"]), "signup date is in the future"
    if row["signup_date"] and row["signup_date"].year < 2000:
        yield "ERROR", "out_of_range", "signup_date", str(row["signup_date"]), "signup date before 2000"


def _rules_products(row, lk):
    if row["unit_price"] is not None and row["unit_price"] <= 0:
        yield "ERROR", "out_of_range", "unit_price", str(row["unit_price"]), "price must be positive"
    if row["unit_cost"] is not None and row["unit_cost"] <= 0:
        yield "ERROR", "out_of_range", "unit_cost", str(row["unit_cost"]), "cost must be positive"
    if row["unit_price"] and row["unit_cost"] and row["unit_cost"] > row["unit_price"]:
        yield "WARNING", "negative_margin", "unit_cost", str(row["unit_cost"]), "cost is higher than price"


def _rules_employees(row, lk):
    if row["hire_date"] and row["hire_date"] > _today():
        yield "ERROR", "future_date", "hire_date", str(row["hire_date"]), "hire date is in the future"


def _rules_orders(row, lk):
    if row["order_date"] and row["order_date"] > _today():
        yield "ERROR", "future_date", "order_date", str(row["order_date"]), "order date is in the future"
    customer = lk.get("customers", {}).get(row["customer_email"])
    if customer is None:
        yield "ERROR", "unknown_reference", "customer_email", row["customer_email"], "customer does not exist"
    if row.get("sales_rep_code") and row["sales_rep_code"] not in lk.get("employees", {}):
        yield "WARNING", "unknown_reference", "sales_rep_code", row["sales_rep_code"], "sales rep not found; left empty"
        row["sales_rep_code"] = None


def _rules_order_items(row, lk):
    if row["order_no"] not in lk.get("orders", {}):
        yield "ERROR", "unknown_reference", "order_no", row["order_no"], "order does not exist"
    product = lk.get("products", {}).get(row["product_sku"])
    if product is None:
        yield "ERROR", "unknown_reference", "product_sku", row["product_sku"], "product does not exist"
    if row["line_no"] is not None and row["line_no"] < 1:
        yield "ERROR", "out_of_range", "line_no", str(row["line_no"]), "line number must be 1 or more"
    if row["quantity"] is not None and row["quantity"] <= 0:
        yield "ERROR", "out_of_range", "quantity", str(row["quantity"]), "quantity must be positive"
    if row.get("discount") is not None and not (0 <= row["discount"] <= 0.5):
        yield "ERROR", "out_of_range", "discount", str(row["discount"]), "discount must be between 0% and 50%"
    if row.get("discount") is None:
        row["discount"] = 0.0
    if product is not None and row.get("unit_price") is None:
        row["unit_price"] = float(product["unit_price"])
        yield "INFO", "filled_default", "unit_price", None, "price missing; list price used"
    elif row.get("unit_price") is not None and row["unit_price"] <= 0:
        yield "ERROR", "out_of_range", "unit_price", str(row["unit_price"]), "price must be positive"


def _rules_tickets(row, lk):
    if row["customer_email"] not in lk.get("customers", {}):
        yield "ERROR", "unknown_reference", "customer_email", row["customer_email"], "customer does not exist"
    if row["opened_at"] and row["opened_at"] > datetime.now():
        yield "ERROR", "future_date", "opened_at", str(row["opened_at"]), "ticket opened in the future"
    if row["resolved_at"] and row["opened_at"] and row["resolved_at"] < row["opened_at"]:
        yield "ERROR", "invalid_timeline", "resolved_at", str(row["resolved_at"]), "resolved before it was opened"
    if row.get("order_no") and row["order_no"] not in lk.get("orders", {}):
        yield "WARNING", "unknown_reference", "order_no", row["order_no"], "order not found; link removed"
        row["order_no"] = None
    if row.get("assignee_code") and row["assignee_code"] not in lk.get("employees", {}):
        yield "WARNING", "unknown_reference", "assignee_code", row["assignee_code"], "agent not found; left unassigned"
        row["assignee_code"] = None
    if row["resolved_at"] and row["status"] != "RESOLVED":
        yield "WARNING", "inconsistent_status", "status", row["status"], "has a resolved time; status set to RESOLVED"
        row["status"] = "RESOLVED"
    if row["status"] == "RESOLVED" and not row["resolved_at"]:
        yield "ERROR", "inconsistent_status", "resolved_at", None, "status is RESOLVED but resolved_at is empty"


_RULES = {"customers": _rules_customers, "products": _rules_products, "employees": _rules_employees,
          "orders": _rules_orders, "order_items": _rules_order_items, "tickets": _rules_tickets}


# --------------------------------------------------------------------------- outliers

def _outliers_order_items(rows, lk):
    products = lk.get("products", {})
    for r in rows:
        p = products.get(r["product_sku"])
        if p and r["unit_price"]:
            list_price = float(p["unit_price"])
            deviation = abs(r["unit_price"] - list_price) / list_price
            if deviation > PRICE_DEVIATION_LIMIT:
                yield (r["_line"], "unit_price", str(r["unit_price"]),
                       f"price {r['unit_price']:,.0f} is {deviation:.0%} away from list price {list_price:,.0f}")
    quantities = [r["quantity"] for r in rows if r["quantity"]]
    if len(quantities) >= 20:
        med = median(quantities)
        mad = median(abs(q - med) for q in quantities) or 1
        threshold = max(50, med + 10 * 1.4826 * mad)   # robust z-score > 10
        for r in rows:
            if r["quantity"] and r["quantity"] > threshold:
                yield (r["_line"], "quantity", str(r["quantity"]),
                       f"quantity {r['quantity']} is far above the typical {med:g} (threshold {threshold:.0f})")


def _outliers_tickets(rows, lk):
    for r in rows:
        if r["resolved_at"] and r["opened_at"] and (r["resolved_at"] - r["opened_at"]).days > 30:
            yield (r["_line"], "resolved_at", str(r["resolved_at"]), "took more than 30 days to resolve")


_OUTLIERS = {"order_items": _outliers_order_items, "tickets": _outliers_tickets}


# --------------------------------------------------------------------------- loaders

def _copy_stage(cur, table: str, columns: list[tuple[str, str]], records: list[tuple]) -> None:
    cur.execute(f"DROP TABLE IF EXISTS {table}")
    cur.execute(f"CREATE TEMP TABLE {table} ({', '.join(f'{n} {t}' for n, t in columns)})")
    with cur.copy(f"COPY {table} ({', '.join(n for n, _ in columns)}) FROM STDIN") as copy:
        for rec in records:
            copy.write_row(rec)


def _upsert(cur, sql: str, run_id: int) -> tuple[int, int]:
    cur.execute(sql, {"run": run_id})
    flags = [r["inserted"] for r in cur.fetchall()]
    inserted = sum(1 for f in flags if f)
    return inserted, len(flags) - inserted


def _load_customers(conn, rows, lk, run_id):
    cur = conn.cursor()
    _copy_stage(cur, "stg_customers",
                [("email", "text"), ("full_name", "text"), ("segment", "text"), ("city_id", "smallint"), ("signup_date", "date")],
                [(r["customer_email"], r["full_name"], r["segment"], lk["cities"][r["city"]], r["signup_date"]) for r in rows])
    return _upsert(cur, """
        INSERT INTO customers (email, full_name, segment, city_id, signup_date, etl_run_id)
        SELECT email, full_name, segment, city_id, signup_date, %(run)s FROM stg_customers
        ON CONFLICT (email) DO UPDATE
           SET full_name = EXCLUDED.full_name, segment = EXCLUDED.segment, city_id = EXCLUDED.city_id,
               signup_date = LEAST(customers.signup_date, EXCLUDED.signup_date), etl_run_id = EXCLUDED.etl_run_id
        RETURNING (xmax = 0) AS inserted""", run_id)


def _load_products(conn, rows, lk, run_id):
    cur = conn.cursor()
    _copy_stage(cur, "stg_products",
                [("sku", "text"), ("name", "text"), ("category", "text"), ("unit_price", "numeric"),
                 ("unit_cost", "numeric"), ("active", "boolean")],
                [(r["sku"], r["name"], r["category"], r["unit_price"], r["unit_cost"],
                  True if r.get("active") is None else r["active"]) for r in rows])
    return _upsert(cur, """
        INSERT INTO products (sku, name, category, unit_price, unit_cost, active, etl_run_id)
        SELECT sku, name, category, round(unit_price, 2), round(unit_cost, 2), active, %(run)s FROM stg_products
        ON CONFLICT (sku) DO UPDATE
           SET name = EXCLUDED.name, category = EXCLUDED.category, unit_price = EXCLUDED.unit_price,
               unit_cost = EXCLUDED.unit_cost, active = EXCLUDED.active, etl_run_id = EXCLUDED.etl_run_id
        RETURNING (xmax = 0) AS inserted""", run_id)


def _load_employees(conn, rows, lk, run_id):
    cur = conn.cursor()
    _copy_stage(cur, "stg_employees",
                [("employee_code", "text"), ("full_name", "text"), ("department", "text"), ("city_id", "smallint"), ("hire_date", "date")],
                [(r["employee_code"], r["full_name"], r["department"], lk["cities"][r["city"]], r["hire_date"]) for r in rows])
    return _upsert(cur, """
        INSERT INTO employees (employee_code, full_name, department, city_id, hire_date, etl_run_id)
        SELECT employee_code, full_name, department, city_id, hire_date, %(run)s FROM stg_employees
        ON CONFLICT (employee_code) DO UPDATE
           SET full_name = EXCLUDED.full_name, department = EXCLUDED.department, city_id = EXCLUDED.city_id,
               hire_date = EXCLUDED.hire_date, etl_run_id = EXCLUDED.etl_run_id
        RETURNING (xmax = 0) AS inserted""", run_id)


def _load_orders(conn, rows, lk, run_id):
    cur = conn.cursor()
    reps = lk.get("employees", {})
    _copy_stage(cur, "stg_orders",
                [("order_no", "text"), ("customer_id", "int"), ("sales_rep_id", "int"), ("order_date", "date"),
                 ("channel", "text"), ("status", "text")],
                [(r["order_no"], lk["customers"][r["customer_email"]]["id"], reps.get(r.get("sales_rep_code")),
                  r["order_date"], r["channel"], r["status"]) for r in rows])
    return _upsert(cur, """
        INSERT INTO orders (order_no, customer_id, sales_rep_id, order_date, channel, status, total_amount, etl_run_id)
        SELECT order_no, customer_id, sales_rep_id, order_date, channel, status, 0, %(run)s FROM stg_orders
        ON CONFLICT (order_no) DO UPDATE
           SET customer_id = EXCLUDED.customer_id, sales_rep_id = EXCLUDED.sales_rep_id,
               order_date = EXCLUDED.order_date, channel = EXCLUDED.channel, status = EXCLUDED.status,
               etl_run_id = EXCLUDED.etl_run_id
        RETURNING (xmax = 0) AS inserted""", run_id)


def _load_order_items(conn, rows, lk, run_id):
    cur = conn.cursor()
    _copy_stage(cur, "stg_order_items",
                [("order_id", "bigint"), ("line_no", "smallint"), ("product_id", "int"), ("quantity", "int"),
                 ("unit_price", "numeric"), ("discount", "numeric")],
                [(lk["orders"][r["order_no"]]["id"], r["line_no"], lk["products"][r["product_sku"]]["id"],
                  r["quantity"], r["unit_price"], r["discount"]) for r in rows])
    result = _upsert(cur, """
        INSERT INTO order_items (order_id, line_no, product_id, quantity, unit_price, discount, etl_run_id)
        SELECT order_id, line_no, product_id, quantity, round(unit_price, 2), round(discount, 3), %(run)s FROM stg_order_items
        ON CONFLICT (order_id, line_no) DO UPDATE
           SET product_id = EXCLUDED.product_id, quantity = EXCLUDED.quantity, unit_price = EXCLUDED.unit_price,
               discount = EXCLUDED.discount, etl_run_id = EXCLUDED.etl_run_id
        RETURNING (xmax = 0) AS inserted""", run_id)
    # keep the denormalised order total in sync with its lines
    cur.execute("""
        UPDATE orders o SET total_amount = t.amount
        FROM (SELECT oi.order_id, round(sum(oi.quantity * oi.unit_price * (1 - oi.discount)), 2) AS amount
              FROM order_items oi WHERE oi.order_id IN (SELECT DISTINCT order_id FROM stg_order_items)
              GROUP BY oi.order_id) t
        WHERE o.id = t.order_id""")
    return result


def _load_tickets(conn, rows, lk, run_id):
    cur = conn.cursor()
    emps = lk.get("employees", {})
    orders = lk.get("orders", {})
    _copy_stage(cur, "stg_tickets",
                [("ticket_no", "text"), ("customer_id", "int"), ("order_id", "bigint"), ("assignee_id", "int"),
                 ("category", "text"), ("priority", "text"), ("status", "text"), ("opened_at", "timestamp"),
                 ("resolved_at", "timestamp"), ("sla_hours", "int")],
                [(r["ticket_no"], lk["customers"][r["customer_email"]]["id"],
                  orders[r["order_no"]]["id"] if r.get("order_no") else None,
                  emps.get(r.get("assignee_code")), r["category"], r["priority"], r["status"],
                  r["opened_at"], r["resolved_at"], SLA_HOURS[r["priority"]]) for r in rows])
    return _upsert(cur, """
        INSERT INTO tickets (ticket_no, customer_id, order_id, assignee_id, category, priority, status,
                             opened_at, resolved_at, sla_hours, etl_run_id)
        SELECT ticket_no, customer_id, order_id, assignee_id, category, priority, status,
               opened_at, resolved_at, sla_hours, %(run)s FROM stg_tickets
        ON CONFLICT (ticket_no) DO UPDATE
           SET customer_id = EXCLUDED.customer_id, order_id = EXCLUDED.order_id, assignee_id = EXCLUDED.assignee_id,
               category = EXCLUDED.category, priority = EXCLUDED.priority, status = EXCLUDED.status,
               opened_at = EXCLUDED.opened_at, resolved_at = EXCLUDED.resolved_at, sla_hours = EXCLUDED.sla_hours,
               etl_run_id = EXCLUDED.etl_run_id
        RETURNING (xmax = 0) AS inserted""", run_id)


_LOADERS = {"customers": _load_customers, "products": _load_products, "employees": _load_employees,
            "orders": _load_orders, "order_items": _load_order_items, "tickets": _load_tickets}


# --------------------------------------------------------------------------- audit

def _audit_start(result: RunResult, source: str, triggered_by: str | None) -> int:
    with connect(autocommit=True) as conn:
        row = conn.execute(
            """INSERT INTO etl_runs (entity, file_name, source, triggered_by, mode, status)
               VALUES (%s, %s, %s, %s, %s, 'RUNNING') RETURNING id""",
            (result.entity or "unknown", result.file_name[:200], source, triggered_by, result.mode)).fetchone()
        return row["id"]


def _audit_finish(result: RunResult) -> None:
    if result.status == "RUNNING":
        result.status = "FAILED"
    with connect(autocommit=True) as conn:
        conn.execute(
            """UPDATE etl_runs SET status=%s, rows_in=%s, rows_loaded=%s, rows_inserted=%s, rows_updated=%s,
                      rows_rejected=%s, rows_duplicate=%s, rows_flagged=%s, quality_score=%s, error_message=%s,
                      finished_at=now(), duration_ms=%s
               WHERE id=%s""",
            (result.status, result.rows_in, result.rows_loaded, result.rows_inserted, result.rows_updated,
             result.rows_rejected, result.rows_duplicate, result.rows_flagged, result.quality_score,
             result.error, result.duration_ms, result.run_id))
        if result.issues:
            with conn.cursor() as cur:
                cur.executemany(
                    """INSERT INTO etl_issues (run_id, row_number, severity, rule, column_name, raw_value, message)
                       VALUES (%s, %s, %s, %s, %s, %s, %s)""",
                    [(result.run_id, i.line, i.severity, i.rule, i.column, i.raw_value, i.message[:500])
                     for i in result.issues[:MAX_STORED_ISSUES]])
