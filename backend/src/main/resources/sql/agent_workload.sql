-- Support agents: workload, speed and SLA compliance, ranked with DENSE_RANK (trailing 90 days).
WITH anchor AS (SELECT max(opened_at) AS end_ts FROM tickets),
agent_stats AS (
    SELECT e.full_name,
           count(*)                                                     AS handled,
           count(*) FILTER (WHERE t.resolved_at IS NULL)                AS open_now,
           avg(EXTRACT(EPOCH FROM (t.resolved_at - t.opened_at)) / 3600.0) AS avg_hours,
           100.0 * count(*) FILTER (WHERE t.resolved_at <= t.opened_at + make_interval(hours => t.sla_hours))
                 / NULLIF(count(*) FILTER (WHERE t.resolved_at IS NOT NULL), 0) AS sla_met_pct
    FROM tickets t
    JOIN employees e ON e.id = t.assignee_id
    CROSS JOIN anchor a
    WHERE t.opened_at > a.end_ts - INTERVAL '90 days'
    GROUP BY e.full_name
)
SELECT dense_rank() OVER (ORDER BY sla_met_pct DESC, avg_hours) AS rank,
       full_name, handled, open_now,
       round(avg_hours::numeric, 1)   AS avg_resolution_hours,
       round(sla_met_pct::numeric, 1) AS sla_met_pct
FROM agent_stats
WHERE handled >= 5
ORDER BY rank
LIMIT 10;
