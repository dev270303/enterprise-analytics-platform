-- Support SLA by ticket category: volume, mean / P90 resolution time and breach rate (trailing 12 months).
WITH anchor AS (SELECT max(opened_at) AS end_ts FROM tickets),
resolved AS (
    SELECT t.category,
           EXTRACT(EPOCH FROM (t.resolved_at - t.opened_at)) / 3600.0 AS hours,
           t.sla_hours
    FROM tickets t CROSS JOIN anchor a
    WHERE t.resolved_at IS NOT NULL
      AND t.opened_at > a.end_ts - INTERVAL '12 months'
)
SELECT
    category,
    count(*)                                                                  AS tickets,
    round(avg(hours)::numeric, 1)                                             AS avg_resolution_hours,
    round((percentile_cont(0.9) WITHIN GROUP (ORDER BY hours))::numeric, 1)   AS p90_resolution_hours,
    round(100.0 * count(*) FILTER (WHERE hours > sla_hours) / count(*), 1)    AS sla_breach_pct
FROM resolved
GROUP BY category
ORDER BY sla_breach_pct DESC;
