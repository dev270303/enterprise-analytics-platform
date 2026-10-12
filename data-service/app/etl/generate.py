"""Generate a realistic *messy* monthly batch of source-system CSV exports.

It continues the business from the day after the last order in the database
(capped at today), references real customers/products/employees from the database,
and deliberately injects the problems real exports have:

  duplicates, missing values, impossible dates, mixed date formats, unknown references,
  inconsistent casing / synonyms, currency-formatted numbers, '10%' discounts,
  price and quantity outliers, negative quantities, broken ticket timelines.

Usage:  python -m app.etl.generate --out sample_batches/latest
"""
from __future__ import annotations

import argparse
import csv
import random
from datetime import date, datetime, timedelta
from pathlib import Path

from app.db import connect

FIRST = ["Aarav", "Vihaan", "Ishita", "Kiara", "Rohit", "Sana", "Kunal", "Meher", "Yash", "Anika",
         "Tushar", "Pallavi", "Rudra", "Nandini", "Harsh", "Jhanvi", "Om", "Trisha", "Laksh", "Ira"]
LAST = ["Arora", "Bhatia", "Chopra", "Dutta", "Ghosh", "Jain", "Kohli", "Luthra", "Naidu", "Oberoi",
        "Prasad", "Qureshi", "Rathore", "Sethi", "Tandon", "Upadhyay", "Vora", "Wadhwa", "Yadav", "Zaveri"]
CITY_SPELLINGS = {"Bengaluru": ["Bengaluru", "Bangalore", "bangalore"], "Mumbai": ["Mumbai", "Bombay", "MUMBAI"],
                  "Gurugram": ["Gurugram", "Gurgaon"], "Kolkata": ["Kolkata", "Calcutta"], "Chennai": ["Chennai", "Madras"]}
DATE_STYLES = ["%Y-%m-%d", "%d/%m/%Y", "%d-%b-%Y"]


def _fmt_date(rng: random.Random, d: date) -> str:
    return d.strftime(rng.choices(DATE_STYLES, weights=[70, 20, 10])[0])


def _money(rng: random.Random, value: float) -> str:
    if rng.random() < 0.15:
        return "₹{:,.2f}".format(value)
    return f"{value:.2f}"


def generate(out_dir: str | Path, seed: int = 2026, scale: float = 1.0) -> dict:
    rng = random.Random(seed)
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)

    with connect() as conn:
        cur = conn.cursor()
        cur.execute("SELECT max(order_date) AS d FROM orders")
        last = cur.fetchone()["d"]
        cur.execute("SELECT email, signup_date FROM customers ORDER BY id")
        customers = [r["email"] for r in cur.fetchall()]
        cur.execute("SELECT sku, unit_price FROM products WHERE active ORDER BY sku")
        products = [(r["sku"], float(r["unit_price"])) for r in cur.fetchall()]
        cur.execute("SELECT employee_code, department FROM employees ORDER BY employee_code")
        emps = cur.fetchall()
        cur.execute("SELECT name FROM cities ORDER BY name")
        cities = [r["name"] for r in cur.fetchall()]
        cur.execute("SELECT count(*) AS n FROM orders WHERE order_no LIKE 'ORD-B%%'")
        batch_no = cur.fetchone()["n"]

    start = last + timedelta(days=1)
    end = min(start + timedelta(days=30), date.today() - timedelta(days=1))
    if end < start:
        raise SystemExit("The data is already up to date; nothing to generate.")
    days = (end - start).days + 1
    tag = start.strftime("%y%m")
    reps = [e["employee_code"] for e in emps if e["department"] == "Sales"]
    agents = [e["employee_code"] for e in emps if e["department"] == "Support"]

    files: dict[str, list[list]] = {}

    # ---------------- customers: new sign-ups this period
    new_customers = []
    rows = [["Email", "Customer Name", "Segment", "City", "Signup Date"]]
    for i in range(int(12 * days * scale)):
        fn, ln = rng.choice(FIRST), rng.choice(LAST)
        email = f"{fn.lower()}.{ln.lower()}.{tag}{i}@example.com"
        city = rng.choice(cities)
        city_out = rng.choice(CITY_SPELLINGS.get(city, [city]))
        seg = rng.choices(["Retail", "retail", "SMB", "small business", "Enterprise", "ENT"],
                          weights=[50, 15, 15, 5, 10, 5])[0]
        signup = start + timedelta(days=rng.randrange(days))
        name = rng.choice([f"{fn} {ln}", f"{fn.lower()} {ln.lower()}", f"  {fn}   {ln} "])
        rows.append([email, name, seg, city_out, _fmt_date(rng, signup)])
        new_customers.append((email, signup))
    # injected problems
    rows.append(["", "Nameless Person", "Retail", "Delhi", start.isoformat()])                       # missing key
    rows.append(["not-an-email", "Broken Email", "SMB", "Pune", start.isoformat()])                    # invalid email
    rows.append([f"atlantis.user.{tag}@example.com", "Atlantis User", "Retail", "Atlantis", start.isoformat()])  # unknown city
    rows.append([f"future.user.{tag}@example.com", "Future User", "Retail", "Delhi", (date.today() + timedelta(days=40)).isoformat()])
    rows.append([f"odd.segment.{tag}@example.com", "Odd Segment", "Government", "Jaipur", start.isoformat()])  # invalid enum
    rows.extend(rows[1:4])                                                                             # exact duplicates
    files["customers.csv"] = rows

    # ---------------- orders
    all_customers = customers + [c for c, _ in new_customers]
    order_rows = [["order_id", "customer_email", "order_date", "channel", "status", "sales_rep"]]
    orders = []
    n = 0
    for d in range(days):
        day = start + timedelta(days=d)
        for _ in range(int(rng.gauss(95, 12) * scale)):
            n += 1
            order_no = f"ORD-B{tag}-{n:05d}"
            if rng.random() < 0.25 and new_customers:
                email, signup = rng.choice(new_customers)
                day_used = max(day, signup)
            else:
                email, day_used = rng.choice(customers), day
            status = rng.choices(["DELIVERED", "delivered", "Completed", "SHIPPED", "In Transit", "CANCELLED", "RETURNED"],
                                 weights=[60, 10, 5, 8, 3, 8, 6])[0]
            channel = rng.choices(["Online", "online", "Web", "Store", "Partner", "Reseller"], weights=[45, 10, 5, 25, 10, 5])[0]
            order_rows.append([order_no, email, _fmt_date(rng, day_used), channel, status, rng.choice(reps)])
            orders.append(order_no)
    order_rows.append(["ORD-B" + tag + "-99001", "ghost.customer@example.com", start.isoformat(), "Online", "DELIVERED", reps[0]])
    order_rows.append(["ORD-B" + tag + "-99002", all_customers[5], "31/09/2026", "Store", "DELIVERED", reps[1]])   # impossible date
    order_rows.append(["", all_customers[6], start.isoformat(), "Store", "DELIVERED", reps[1]])                    # missing key
    order_rows.append(["ORD-B" + tag + "-99003", all_customers[7], start.isoformat(), "Carrier Pigeon", "DELIVERED", reps[2]])
    order_rows.append(["ORD-B" + tag + "-99004", all_customers[8], start.isoformat(), "Online", "DELIVERED", "EMP-9999"])
    order_rows.extend(rng.sample(order_rows[1:-5], 6))                                                            # duplicates
    files["orders.csv"] = order_rows

    # ---------------- order items
    item_rows = [["order_id", "line", "sku", "qty", "price", "discount"]]
    for order_no in orders:
        for line in range(1, 1 + 1 + int(rng.random() ** 2.2 * 4)):
            sku, price = products[min(int(rng.random() ** 1.35 * len(products)), len(products) - 1)]
            qty = 1 + int(rng.random() ** 3 * 4)
            disc = rng.choice(["0", "0", "0", "0.05", "0.1", "10%", "15%", ""])
            item_rows.append([order_no, line, sku, qty, _money(rng, price), disc])
    some = rng.sample(range(1, len(item_rows)), 30)
    for k in some[:6]:
        item_rows[k][4] = _money(rng, float(str(item_rows[k][4]).replace("₹", "").replace(",", "")) * 10)  # price outlier
    for k in some[6:9]:
        item_rows[k][3] = 500                                                                       # quantity outlier
    for k in some[9:12]:
        item_rows[k][3] = -2                                                                        # negative quantity
    for k in some[12:16]:
        item_rows[k][2] = "XYZ-999"                                                                 # unknown product
    for k in some[16:19]:
        item_rows[k][5] = "75%"                                                                     # discount out of range
    for k in some[19:22]:
        item_rows[k][4] = ""                                                                        # missing price -> list price
    item_rows.append(["ORD-B" + tag + "-88888", 1, products[0][0], 1, products[0][1], 0])          # unknown order
    files["order_items.csv"] = item_rows

    # ---------------- tickets
    sla = {"LOW": 72, "MEDIUM": 48, "HIGH": 24, "CRITICAL": 6}
    t_rows = [["ticket_id", "customer_email", "order_id", "assignee", "issue_type", "priority", "status", "created_at", "closed_at"]]
    for i, order_no in enumerate(rng.sample(orders, max(1, int(len(orders) * 0.12)))):
        opened = datetime.combine(start, datetime.min.time()) + timedelta(minutes=rng.randrange(days * 1440))
        pr = rng.choices(list(sla), weights=[40, 35, 19, 6])[0]
        hours = sla[pr] * (0.15 + rng.random() ** 1.6 * 1.5)
        resolved = opened + timedelta(hours=hours)
        open_now = resolved > datetime.combine(end, datetime.max.time()) or rng.random() < 0.05
        cat = rng.choices(["Delivery", "Billing", "Product Defect", "Returns", "Account", "shipping", "refund"],
                          weights=[30, 20, 17, 15, 8, 6, 4])[0]
        cust_email = next(r[1] for r in order_rows if r[0] == order_no) if i < 40 else rng.choice(customers)
        t_rows.append([f"TKT-B{tag}-{i + 1:05d}", cust_email, order_no if i < 40 else "", rng.choice(agents), cat,
                       rng.choice([pr, pr.lower(), pr.title()]),
                       ("Open" if rng.random() < 0.5 else "In Progress") if open_now else rng.choice(["Resolved", "closed"]),
                       opened.strftime("%Y-%m-%d %H:%M"), "" if open_now else resolved.strftime("%Y-%m-%d %H:%M")])
    bad_open = datetime.combine(start, datetime.min.time()) + timedelta(hours=30)
    t_rows.append([f"TKT-B{tag}-99001", customers[1], "", agents[0], "Billing", "HIGH", "Resolved",
                   bad_open.strftime("%Y-%m-%d %H:%M"), (bad_open - timedelta(hours=5)).strftime("%Y-%m-%d %H:%M")])  # resolved before opened
    t_rows.append([f"TKT-B{tag}-99002", customers[2], "", agents[1], "", "LOW", "Open",
                   bad_open.strftime("%Y-%m-%d %H:%M"), ""])                                                         # missing category
    t_rows.append([f"TKT-B{tag}-99003", customers[3], "ORD-NOPE-0001", "EMP-0000", "Delivery", "MEDIUM", "Resolved",
                   bad_open.strftime("%Y-%m-%d %H:%M"), (bad_open + timedelta(hours=20)).strftime("%Y-%m-%d %H:%M")])
    files["tickets.csv"] = t_rows

    for name, data in files.items():
        with open(out / name, "w", newline="", encoding="utf-8") as fh:
            csv.writer(fh).writerows(data)
    return {"period_start": start.isoformat(), "period_end": end.isoformat(),
            "files": {name: len(data) - 1 for name, data in files.items()}, "out_dir": str(out)}


def to_bytes(path: Path) -> bytes:
    return path.read_bytes()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--out", default="sample_batches/latest")
    parser.add_argument("--seed", type=int, default=2026)
    parser.add_argument("--scale", type=float, default=1.0)
    args = parser.parse_args()
    print(generate(args.out, args.seed, args.scale))


if __name__ == "__main__":
    main()
