# Power BI dashboard (Phase 2)

The database exposes a **star schema for BI tools** in the `bi` schema (migration `V6__bi_star_schema.sql`).
Power BI imports these views instead of the raw transactional tables. Allow about 1 to 2 hours.

```
                 bi.dim_date ─────────┐
 bi.dim_customer ─┐                   │
 bi.dim_product ──┼── bi.fact_sales ──┤        (grain: one order line)
 bi.dim_employee ─┤                   │
 bi.dim_geography ┘   bi.fact_tickets ┘        (grain: one support ticket)
```

| View | Grain / purpose | Key columns |
|---|---|---|
| `bi.fact_sales` | one row per order line | `date_key`, `customer_key`, `product_key`, `city_key`, `employee_key`, `net_revenue`, `cost`, `quantity`, `is_revenue`, `status`, `channel` |
| `bi.fact_tickets` | one row per ticket | `date_key`, `customer_key`, `city_key`, `employee_key`, `priority`, `category`, `resolution_hours`, `sla_breached` |
| `bi.dim_date` | calendar 2023 to 2027 | `date_key`, `month_start`, `month_label`, `quarter`, `fiscal_year` (Indian FY, April to March), `is_weekend` |
| `bi.dim_customer` | customer + ML segment | `customer_key`, `segment`, `rfm_segment`, `churn_risk`, `city_key` |
| `bi.dim_product` | catalogue | `product_key`, `category`, `list_price`, `unit_cost` |
| `bi.dim_geography` | city → state → region | `city_key`, `city`, `state`, `region` |
| `bi.dim_employee` | sales reps and agents | `employee_key`, `department` |

---

## 1. Create a read-only login for Power BI (recommended)

In the **Neon console → SQL Editor**, run this (choose your own password):

```sql
CREATE ROLE bi_reader LOGIN PASSWORD 'choose-a-long-password';
GRANT USAGE ON SCHEMA bi TO bi_reader;
GRANT SELECT ON ALL TABLES IN SCHEMA bi TO bi_reader;
```

`bi_reader` can read the `bi` views and nothing else: no users table, and no way to write. It's least privilege again, and worth mentioning in an interview.

## 2. Connect Power BI Desktop (Windows, free)

1. Install **Power BI Desktop** from the Microsoft Store.
2. **Home → Get data → More… → PostgreSQL database → Connect**.
3. **Server**: your Neon host, e.g. `ep-cool-name-123456.ap-southeast-1.aws.neon.tech`.
   **Database**: `neondb`. **Data connectivity mode**: *Import*.
4. Credentials: choose **Database**, then enter user `bi_reader` and your password.
5. In the Navigator, tick the 7 views under **bi** (`bi.dim_*`, `bi.fact_*`) and click **Load**.

> If you get an SSL or certificate error, update Power BI Desktop (recent versions bundle the right driver).
> Neon always requires encryption, so don't turn it off.

## 3. Model view: relationships

Power BI usually detects these automatically. Check them in **Model view** (all *many-to-one*, single direction):

| From (many) | To (one) |
|---|---|
| `fact_sales[date_key]` | `dim_date[date_key]` |
| `fact_sales[customer_key]` | `dim_customer[customer_key]` |
| `fact_sales[product_key]` | `dim_product[product_key]` |
| `fact_sales[city_key]` | `dim_geography[city_key]` |
| `fact_sales[employee_key]` | `dim_employee[employee_key]` |
| `fact_tickets[date_key]` | `dim_date[date_key]` |
| `fact_tickets[city_key]` | `dim_geography[city_key]` |
| `fact_tickets[employee_key]` | `dim_employee[employee_key]` |

Then right-click `dim_date` → **Mark as date table** → `date_key`.

## 4. DAX measures

Create a blank table called `_Measures` (**Home → Enter data → Load**) and add these with **New measure**:

```DAX
Revenue = CALCULATE ( SUM ( fact_sales[net_revenue] ), fact_sales[is_revenue] = TRUE () )

Orders = CALCULATE ( DISTINCTCOUNT ( fact_sales[order_id] ), fact_sales[is_revenue] = TRUE () )

Avg Order Value = DIVIDE ( [Revenue], [Orders] )

Gross Margin % =
VAR Cost = CALCULATE ( SUM ( fact_sales[cost] ), fact_sales[is_revenue] = TRUE () )
RETURN DIVIDE ( [Revenue] - Cost, [Revenue] )

Active Customers = CALCULATE ( DISTINCTCOUNT ( fact_sales[customer_key] ), fact_sales[is_revenue] = TRUE () )

Revenue LY = CALCULATE ( [Revenue], SAMEPERIODLASTYEAR ( dim_date[date_key] ) )

Revenue YoY % = DIVIDE ( [Revenue] - [Revenue LY], [Revenue LY] )

Revenue 3M Avg =
DIVIDE ( CALCULATE ( [Revenue], DATESINPERIOD ( dim_date[date_key], MAX ( dim_date[date_key] ), -3, MONTH ) ), 3 )

Return Rate % =
DIVIDE (
    CALCULATE ( DISTINCTCOUNT ( fact_sales[order_id] ), fact_sales[status] = "RETURNED" ),
    DISTINCTCOUNT ( fact_sales[order_id] )
)

Tickets = COUNTROWS ( fact_tickets )

SLA Breach % =
DIVIDE (
    CALCULATE ( COUNTROWS ( fact_tickets ), fact_tickets[sla_breached] = TRUE () ),
    CALCULATE ( COUNTROWS ( fact_tickets ), NOT ISBLANK ( fact_tickets[sla_breached] ) )
)

P90 Resolution Hours = PERCENTILEX.INC ( FILTER ( fact_tickets, NOT ISBLANK ( fact_tickets[resolution_hours] ) ), fact_tickets[resolution_hours], 0.9 )

High Churn Customers = CALCULATE ( DISTINCTCOUNT ( dim_customer[customer_key] ), dim_customer[churn_risk] = "HIGH" )
```

Format `Revenue`, `Revenue LY` and `Avg Order Value` as currency (₹, 0 decimals). Format the `%` measures as percentages.

## 5. Report pages

**Page 1: Executive overview**
- 4 cards: Revenue, Orders, Active Customers, Avg Order Value (add `Revenue YoY %` as the card's reference label)
- Line chart: `dim_date[month_start]` × Revenue and Revenue 3M Avg
- Bar chart: `dim_geography[region]` × Revenue
- Slicers: `dim_date[fiscal_year]`, `dim_geography[region]`, `dim_customer[segment]`

**Page 2: Sales**
- Table: `dim_product[product_name]`, Revenue, Gross Margin %, `quantity` (top 10 filter on Revenue)
- Scatter: per `dim_product[category]`, Revenue (x) vs Gross Margin % (y)
- Matrix: region × channel with Revenue

**Page 3: Customers**
- Donut or bar: `dim_customer[rfm_segment]` × Active Customers
- Stacked bar: `rfm_segment` × High Churn Customers vs others
- Table: top customers by Revenue with `churn_risk`

**Page 4: Support**
- Cards: Tickets, SLA Breach %, P90 Resolution Hours
- Bar: `fact_tickets[category]` × SLA Breach %
- Line: month × Tickets

Keep one colour per measure across pages. The dashboard in this repo uses blue `#2a78d6` for the primary measure and orange `#eb6834` for comparisons.

## 6. Put it in the repo

1. **File → Save as** `dashboard/EnterpriseAnalytics.pbix`.
2. Take a screenshot of each page and save them as `docs/powerbi/overview.png`, `sales.png`, `customers.png` and `support.png`.
3. Commit and push:
   ```bash
   git add dashboard docs/powerbi
   git commit -m "Add Power BI dashboard"
   git push
   ```
4. Add the screenshots to the README's Power BI section.

> **Refreshing:** in Import mode, click **Refresh** to pull new data (for example after an ETL load).
> Scheduled refresh needs the Power BI Service and a gateway, which is not needed for a portfolio piece.
