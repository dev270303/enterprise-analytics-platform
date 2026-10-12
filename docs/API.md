# API reference

All endpoints are served by the Spring Boot backend under `/api`. Authenticate with
`POST /api/auth/login` and send the returned token as `Authorization: Bearer <token>`.

Roles: **A** = ADMIN, **N** = ANALYST, **M** = MANAGER, **E** = EMPLOYEE. *Owner* = the private ADMIN account (`demo=false`).

## Auth & health

| Method | Path | Roles | Notes |
|---|---|---|---|
| POST | `/api/auth/login` | public | `{username, password}` → `{token, expiresInSeconds, username, displayName, role, demo}` |
| GET | `/api/health` | public | liveness; never queries the database |

## Analytics (each maps to one file in `backend/src/main/resources/sql/`)

| Method | Path | Roles | Query |
|---|---|---|---|
| GET | `/api/dashboard/summary?region=` | A N M | `kpi_summary.sql` + `support_kpis.sql` |
| GET | `/api/analytics/revenue-trend?region=` | A N M | `revenue_trend.sql` |
| GET | `/api/analytics/regions` | A N M | `region_performance.sql` |
| GET | `/api/analytics/top-products?region=` | A N M | `top_products.sql` |
| GET | `/api/analytics/categories?region=` | A N M | `category_mix.sql` |
| GET | `/api/analytics/sales-reps` | A N M | `rep_leaderboard.sql` |
| GET | `/api/analytics/top-customers?region=` | A N M | `top_customers.sql` |
| GET | `/api/analytics/customer-growth?region=` | A N M | `customer_growth.sql` |
| GET | `/api/analytics/cohorts?region=` | A N M | `cohort_retention.sql` |
| GET | `/api/analytics/segments?region=` | A N M | `segment_summary.sql` |
| GET | `/api/analytics/rfm-segments?region=` | A N M | `rfm_segments.sql` + `churn_watchlist.sql` (ML output) |
| GET | `/api/analytics/support/summary` | all | `support_kpis.sql` |
| GET | `/api/analytics/support/sla` | all | `ticket_sla.sql` |
| GET | `/api/analytics/support/trend` | all | `ticket_trend.sql` |
| GET | `/api/analytics/support/agents` | all | `agent_workload.sql` |
| GET | `/api/analytics/sql/{name}` | A N | the SQL text behind a chart |

## Records

| Method | Path | Roles | Notes |
|---|---|---|---|
| GET | `/api/customers?search=&segment=&region=&page=&size=` | A N M | paginated, with ML segment |
| GET | `/api/orders?status=&region=&from=&to=&page=&size=` | A N M | paginated, newest first |
| GET | `/api/products?category=&page=&size=` | all | Spring Data JPA, paginated |

## AI analyst (Phase 3)

| Method | Path | Roles | Notes |
|---|---|---|---|
| POST | `/api/ai/ask` | A N M | `{question}` (≤ 500 chars). Rate-limited per user (demo: 15 per hour). Returns `{route: sql\|docs\|unsupported, status, answer, sql, columns, rows, chart, sources, attempts, latency_ms}` |
| GET | `/api/ai/status` | A N M | service, model, role isolation, documents, daily budget. Also wakes the service |
| GET | `/api/ai/documents` | A N M | indexed policy documents |
| POST | `/api/ai/documents` | Owner | multipart `file` (.md / .txt / .pdf, ≤ 5 MB) → chunk, embed, index |
| GET | `/api/ai/history` | A N M | last 30 questions (A/N see everyone's, M sees own) |

## Data ingestion (Phase 2)

| Method | Path | Roles | Notes |
|---|---|---|---|
| POST | `/api/data/upload` | A | multipart `file` (.csv), optional `entity`, `commit` (only the owner can commit; others are forced to dry run) |
| GET | `/api/data/samples?refresh=` | A N | generates a messy sample batch from live keys |
| GET | `/api/data/samples/{name}` | A N | download one sample CSV |
| POST | `/api/data/samples/run?commit=` | A | runs the whole batch atomically |
| GET | `/api/data/runs` | A N | last 40 pipeline runs |
| GET | `/api/data/runs/{id}` | A N | run detail + issue summary + row-level issues |

## Insights (Phase 4)

| Method | Path | Roles | Notes |
|---|---|---|---|
| GET | `/api/insights` | A N M | report history |
| GET | `/api/insights/latest` | A N M | latest report with KPIs, anomalies, SLA |
| GET | `/api/insights/{id}` | A N M | one report |
| GET | `/api/insights/{id}/email` | A N M | the HTML e-mail body |
| POST | `/api/insights/run?email=` | A | generate now (e-mail only for the owner) |

## Admin

| Method | Path | Roles |
|---|---|---|
| GET | `/api/admin/users` | A |
| POST | `/api/admin/cache/clear` | A |
| POST | `/api/admin/segments/run` | A |
| POST | `/api/admin/documents/reindex` | A |

## Errors

Spring Boot error JSON, e.g. `{"status": 429, "error": "Too Many Requests", "message": "Demo limit reached: 15 AI questions per hour…"}`.
`503` means the data & AI service is waking up or not configured; the dashboard shows the message.
