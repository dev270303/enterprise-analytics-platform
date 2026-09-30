-- Support headline numbers: backlog, ageing and last-30-day performance.
WITH anchor AS (SELECT max(opened_at) AS end_ts FROM tickets)
SELECT
    count(*) FILTER (WHERE t.resolved_at IS NULL)                                          AS open_tickets,
    count(*) FILTER (WHERE t.resolved_at IS NULL AND t.priority IN ('HIGH','CRITICAL'))    AS open_high_priority,
    count(*) FILTER (WHERE t.resolved_at IS NULL
                       AND a.end_ts > t.opened_at + make_interval(hours => t.sla_hours))   AS open_overdue,
    count(*) FILTER (WHERE t.opened_at > a.end_ts - INTERVAL '30 days')                    AS opened_30d,
    round(avg(EXTRACT(EPOCH FROM (t.resolved_at - t.opened_at)) / 3600.0)
          FILTER (WHERE t.resolved_at IS NOT NULL AND t.opened_at > a.end_ts - INTERVAL '30 days')::numeric, 1)
                                                                                           AS avg_resolution_hours_30d,
    round(100.0 * count(*) FILTER (WHERE t.opened_at > a.end_ts - INTERVAL '30 days'
                                     AND t.resolved_at > t.opened_at + make_interval(hours => t.sla_hours))
          / NULLIF(count(*) FILTER (WHERE t.opened_at > a.end_ts - INTERVAL '30 days'
                                      AND t.resolved_at IS NOT NULL), 0), 1)               AS sla_breach_pct_30d
FROM tickets t CROSS JOIN anchor a;
