"""Declarative description of every file the pipeline accepts.

Adding a new source column = adding one Column(...) line; the pipeline, validation
report and loader are driven entirely by these specs.
"""
from __future__ import annotations

from dataclasses import dataclass, field


def _synonyms(canonical: list[str], extra: dict[str, str] | None = None) -> dict[str, str]:
    mapping = {c.lower().replace("_", " "): c for c in canonical}
    for k, v in (extra or {}).items():
        mapping[k.lower()] = v
    return mapping


SEGMENTS = _synonyms(["Retail", "SMB", "Enterprise"],
                     {"small business": "SMB", "sme": "SMB", "ent": "Enterprise", "corporate": "Enterprise",
                      "consumer": "Retail", "b2c": "Retail"})
CATEGORIES = _synonyms(["Laptops", "Smartphones", "Accessories", "Audio", "Monitors", "Networking",
                        "Office Furniture", "Software"],
                       {"laptop": "Laptops", "smartphone": "Smartphones", "phones": "Smartphones",
                        "mobile": "Smartphones", "accessory": "Accessories", "monitor": "Monitors",
                        "furniture": "Office Furniture"})
DEPARTMENTS = _synonyms(["Sales", "Support", "Logistics", "Finance"], {"customer support": "Support"})
CHANNELS = _synonyms(["Online", "Store", "Partner"],
                     {"web": "Online", "website": "Online", "e commerce": "Online", "retail store": "Store",
                      "offline": "Store", "reseller": "Partner", "distributor": "Partner"})
ORDER_STATUSES = _synonyms(["DELIVERED", "SHIPPED", "CANCELLED", "RETURNED"],
                           {"completed": "DELIVERED", "complete": "DELIVERED", "in transit": "SHIPPED",
                            "dispatched": "SHIPPED", "canceled": "CANCELLED", "refunded": "RETURNED"})
TICKET_CATEGORIES = _synonyms(["Delivery", "Billing", "Product Defect", "Returns", "Account"],
                              {"defect": "Product Defect", "shipping": "Delivery", "payment": "Billing",
                               "refund": "Returns", "login": "Account"})
PRIORITIES = _synonyms(["LOW", "MEDIUM", "HIGH", "CRITICAL"], {"p1": "CRITICAL", "p2": "HIGH", "p3": "MEDIUM",
                                                               "p4": "LOW", "urgent": "CRITICAL", "normal": "MEDIUM"})
TICKET_STATUSES = _synonyms(["OPEN", "IN_PROGRESS", "RESOLVED"],
                            {"in progress": "IN_PROGRESS", "wip": "IN_PROGRESS", "closed": "RESOLVED",
                             "done": "RESOLVED", "new": "OPEN"})

SLA_HOURS = {"LOW": 72, "MEDIUM": 48, "HIGH": 24, "CRITICAL": 6}   # matches the support SLA policy document


@dataclass(frozen=True)
class Column:
    name: str
    kind: str                       # text | name | email | code | date | datetime | money | int | fraction | bool | enum | city
    required: bool = True
    aliases: tuple[str, ...] = ()
    enum: dict[str, str] | None = None
    pattern: str | None = None      # regex for codes


@dataclass(frozen=True)
class EntitySpec:
    name: str
    columns: tuple[Column, ...]
    key: tuple[str, ...]            # business key: duplicates are detected on it
    load_order: int
    description: str = ""
    signature: tuple[str, ...] = field(default=())   # columns that identify this file type

    def column(self, name: str) -> Column:
        return next(c for c in self.columns if c.name == name)


SPECS: dict[str, EntitySpec] = {
    "customers": EntitySpec(
        name="customers", load_order=1, key=("customer_email",),
        description="Customer master data",
        signature=("customer_email", "segment"),
        columns=(
            Column("customer_email", "email", aliases=("email", "email_id", "customer_email_id")),
            Column("full_name", "name", aliases=("name", "customer_name")),
            Column("segment", "enum", enum=SEGMENTS, aliases=("customer_segment", "type")),
            Column("city", "city", aliases=("location", "customer_city")),
            Column("signup_date", "date", aliases=("signup", "created_on", "registration_date", "joined")),
        )),
    "products": EntitySpec(
        name="products", load_order=2, key=("sku",),
        description="Product catalogue",
        signature=("sku", "unit_cost"),
        columns=(
            Column("sku", "code", pattern=r"[A-Z]{3}-\d{3,6}", aliases=("product_code", "item_code")),
            Column("name", "text", aliases=("product_name", "title")),
            Column("category", "enum", enum=CATEGORIES),
            Column("unit_price", "money", aliases=("price", "list_price", "mrp")),
            Column("unit_cost", "money", aliases=("cost", "cost_price")),
            Column("active", "bool", required=False),
        )),
    "employees": EntitySpec(
        name="employees", load_order=3, key=("employee_code",),
        description="Employee master data",
        signature=("employee_code", "department"),
        columns=(
            Column("employee_code", "code", pattern=r"EMP-\d{3,6}", aliases=("emp_code", "employee_id", "emp_id")),
            Column("full_name", "name", aliases=("name", "employee_name")),
            Column("department", "enum", enum=DEPARTMENTS, aliases=("dept",)),
            Column("city", "city", aliases=("location", "office")),
            Column("hire_date", "date", aliases=("joining_date", "date_of_joining", "doj")),
        )),
    "orders": EntitySpec(
        name="orders", load_order=4, key=("order_no",),
        description="Order headers",
        signature=("order_no", "order_date"),
        columns=(
            Column("order_no", "code", pattern=r"ORD-[A-Z0-9\-]{4,20}", aliases=("order_id", "order_number")),
            Column("customer_email", "email", aliases=("email", "customer")),
            Column("order_date", "date", aliases=("date", "ordered_on")),
            Column("channel", "enum", enum=CHANNELS, aliases=("sales_channel",)),
            Column("status", "enum", enum=ORDER_STATUSES, aliases=("order_status",)),
            Column("sales_rep_code", "code", required=False, pattern=r"EMP-\d{3,6}", aliases=("sales_rep", "rep_code")),
        )),
    "order_items": EntitySpec(
        name="order_items", load_order=5, key=("order_no", "line_no"),
        description="Order lines",
        signature=("order_no", "product_sku"),
        columns=(
            Column("order_no", "code", pattern=r"ORD-[A-Z0-9\-]{4,20}", aliases=("order_id", "order_number")),
            Column("line_no", "int", aliases=("line", "line_number", "item_no")),
            Column("product_sku", "code", pattern=r"[A-Z]{3}-\d{3,6}", aliases=("sku", "product_code", "item_code")),
            Column("quantity", "int", aliases=("qty", "units")),
            Column("unit_price", "money", required=False, aliases=("price", "rate")),
            Column("discount", "fraction", required=False, aliases=("discount_pct", "disc")),
        )),
    "tickets": EntitySpec(
        name="tickets", load_order=6, key=("ticket_no",),
        description="Support tickets",
        signature=("ticket_no", "priority"),
        columns=(
            Column("ticket_no", "code", pattern=r"TKT-[A-Z0-9\-]{4,20}", aliases=("ticket_id", "ticket_number")),
            Column("customer_email", "email", aliases=("email", "customer")),
            Column("order_no", "code", required=False, pattern=r"ORD-[A-Z0-9\-]{4,20}", aliases=("order_id",)),
            Column("assignee_code", "code", required=False, pattern=r"EMP-\d{3,6}", aliases=("assignee", "agent_code")),
            Column("category", "enum", enum=TICKET_CATEGORIES, aliases=("issue_type",)),
            Column("priority", "enum", enum=PRIORITIES),
            Column("status", "enum", enum=TICKET_STATUSES, aliases=("ticket_status",)),
            Column("opened_at", "datetime", aliases=("created_at", "opened", "raised_at")),
            Column("resolved_at", "datetime", required=False, aliases=("closed_at", "resolved")),
        )),
}


def detect_entity(headers: list[str], file_name: str = "") -> str | None:
    """Guess the entity from the file name first, then from distinctive columns."""
    stem = file_name.lower().rsplit("/", 1)[-1]
    for name in sorted(SPECS, key=len, reverse=True):          # 'order_items' before 'orders'
        if stem.startswith(name) or f"_{name}" in stem or f"-{name}" in stem:
            return name
    normalized = set(headers)
    for spec in sorted(SPECS.values(), key=lambda s: -len(s.signature)):
        resolved = {resolve_alias(spec, h) for h in normalized}
        if all(col in resolved for col in spec.signature):
            return spec.name
    return None


def resolve_alias(spec: EntitySpec, header: str) -> str:
    for col in spec.columns:
        if header == col.name or header in col.aliases:
            return col.name
    return header
