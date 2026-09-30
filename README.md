# Enterprise Analytics Platform

**Operations intelligence for a multi-region business.** A Spring Boot REST API and PostgreSQL analytics layer, with a live dashboard covering revenue, customers, products and support SLAs.

**Live demo:** `https://<your-service>.onrender.com` (click **Analyst** to see the SQL behind every chart)
*Hosted on a free tier, so the first load after a quiet period takes about a minute.*

![Overview dashboard](docs/screenshots/overview.png)

---

## What it does

A synthetic company sells electronics and office equipment across **17 Indian cities in 4 regions**. The dataset covers 24 months: about 8,000 customers, 60,000 orders, 110,000 order lines and 9,500 support tickets, with growth, festive-season spikes and weekday effects built in.

| Dashboard | Highlights |
|---|---|
| **Overview** | Trailing-12-month KPIs vs prior year, monthly revenue with 3-month moving average, regional share, support snapshot |
| **Sales** | Category revenue and margin, top-10 products ranked overall *and* within category, top 3 reps per region |
| **Customers** | New vs returning customers, **monthly cohort retention matrix**, segment lifetime value, top accounts |
| **Support** | SLA breach rate and mean/P90 resolution time by category, ticket trend, agent leaderboard |

Every view can be filtered by region, and every card maps to one versioned `.sql` file.

## Architecture

```
┌──────────────────────────┐        ┌──────────────────────────────────────┐        ┌──────────────┐
│ Dashboard (HTML/JS,      │  JWT   │ Spring Boot 3 (Java 21)              │  JDBC  │ PostgreSQL   │
│ Chart.js, served by the  ├───────▶│  • Spring Security (stateless JWT)   ├───────▶│  (Neon)      │
│ same app)                │  REST  │  • Analytics: NamedParameterJdbc     │  SSL   │  Flyway-     │
└──────────────────────────┘        │  • CRUD: Spring Data JPA             │        │  managed     │
                                    │  • Result cache (Spring Cache)       │        └──────────────┘
                                    └──────────────────────────────────────┘
          Docker image ── GitHub Actions CI (build → migrate → integration tests → image) ── Render
```

### Design decisions

- **JDBC for analytics, JPA for CRUD.** Aggregations with CTEs and window functions don't map onto entities, so the analytics repository runs SQL files directly with `NamedParameterJdbcTemplate`. Simple entity reads (`/api/products`, users) use Spring Data JPA.
- **SQL lives in `.sql` files**, not Java strings (`backend/src/main/resources/sql/`). Each file runs as-is in `psql`, gets reviewed like code, and is shown in the UI to users with the Analyst role.
- **Schema and seed data via Flyway.** A fresh database, whether in CI, locally or in production, reaches the same state on startup. `setseed()` makes the synthetic data deterministic.
- **Role-based access control.** `MANAGER` sees dashboards, `ANALYST` also sees SQL, and `ADMIN` also gets user management and cache control. This is enforced on the server with URL rules and `@PreAuthorize`, not just hidden in the UI.
- **Built for a serverless database.** A caching layer, `minimum-idle: 0` connection pooling and a health check that never touches Postgres let the database scale to zero between visitors.

## SQL showcase

| File | Techniques |
|---|---|
| `kpi_summary.sql` | CTEs, conditional aggregation with `FILTER`, period-over-period growth |
| `revenue_trend.sql` | `AVG() OVER (ROWS BETWEEN 2 PRECEDING …)` moving average, `LAG()` for MoM and YoY |
| `region_performance.sql` | `ROW_NUMBER()` to find each region's top city, share of total with `SUM() OVER ()` |
| `top_products.sql` | `RANK()` overall and `RANK() OVER (PARTITION BY category)`, margin calculation |
| `top_customers.sql` | Lifetime value, recency, `NTILE(4)` value quartiles |
| `customer_growth.sql` | First-purchase detection, new vs returning split |
| `cohort_retention.sql` | Monthly cohorts with `age()` offsets, retention matrix |
| `rep_leaderboard.sql` | Top-N per group with `ROW_NUMBER() OVER (PARTITION BY region)` |
| `ticket_sla.sql` | `percentile_cont(0.9) WITHIN GROUP`, SLA breach rate |
| `agent_workload.sql` | `DENSE_RANK()` with tie-breakers |

## API

| Method | Path | Access |
|---|---|---|
| `POST` | `/api/auth/login` | public: returns a JWT |
| `GET` | `/api/health` | public |
| `GET` | `/api/dashboard/summary?region=` | any role |
| `GET` | `/api/analytics/{revenue-trend, regions, top-products, categories, sales-reps}` | any role |
| `GET` | `/api/analytics/{top-customers, customer-growth, cohorts, segments}` | any role |
| `GET` | `/api/analytics/support/{sla, trend, agents}` | any role |
| `GET` | `/api/analytics/sql/{name}` | ANALYST, ADMIN |
| `GET` | `/api/products?category=&page=&size=` | any role (JPA, paginated) |
| `GET` | `/api/admin/users` · `POST /api/admin/cache/clear` | ADMIN |

Example:

```bash
TOKEN=$(curl -s -X POST localhost:8080/api/auth/login -H 'Content-Type: application/json' \
  -d '{"username":"analyst","password":"demo123"}' | jq -r .token)
curl -s localhost:8080/api/analytics/top-products?region=South -H "Authorization: Bearer $TOKEN" | jq
```

## Run locally

**With Docker (one command):**
```bash
docker compose up --build
# open http://localhost:8080  (demo users: admin / analyst / manager, password demo123)
```

**Without Docker** (Java 21, Maven, and a local PostgreSQL with an empty `eap` database):
```bash
cd backend
export DB_URL=jdbc:postgresql://localhost:5432/eap DB_USERNAME=postgres DB_PASSWORD=postgres
mvn spring-boot:run
mvn verify            # integration tests (need the same database)
```

## Deploy (free)

Neon for PostgreSQL and Render for the Docker container. See **[docs/DEPLOY.md](docs/DEPLOY.md)** for the step-by-step guide.

## Project structure

```
├── backend/
│   ├── pom.xml
│   └── src/main/
│       ├── java/com/eap/          auth · analytics · catalog · admin · config · common
│       └── resources/
│           ├── db/migration/      V1__schema.sql, V2__seed_data.sql (Flyway)
│           ├── sql/               14 analytical queries
│           ├── static/            dashboard (index.html, app.js, styles.css)
│           └── application.yml
├── docs/DEPLOY.md
├── Dockerfile · docker-compose.yml · render.yaml
└── .github/workflows/ci.yml
```

## Roadmap

- [x] **Phase 1:** PostgreSQL schema, analytical SQL, Spring Boot REST API, JWT + RBAC, live dashboard, Docker, CI, free hosting
- [ ] **Phase 2:** Python ETL (pandas): CSV upload, validation and cleaning of deliberately messy data, load into staging tables
- [ ] **Phase 3:** Natural-language analyst: LLM → SQL with a read-only DB role, `SELECT`-only validation, statement timeout and row limits. RAG over policy documents
- [ ] **Phase 4:** Scheduled anomaly detection and a daily insights email

---

*All data is synthetic and generated for demonstration.*
