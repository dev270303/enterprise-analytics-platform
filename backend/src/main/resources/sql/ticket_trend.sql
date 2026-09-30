-- Monthly ticket volume and SLA breach rate.
SELECT
    date_trunc('month', opened_at)::date                                           AS month,
    count(*)                                                                       AS opened,
    count(*) FILTER (WHERE resolved_at IS NOT NULL)                                AS resolved,
    round(100.0 * count(*) FILTER (WHERE resolved_at > opened_at + make_interval(hours => sla_hours))
          / NULLIF(count(*) FILTER (WHERE resolved_at IS NOT NULL), 0), 1)         AS sla_breach_pct
FROM tickets
GROUP BY 1
ORDER BY 1;
