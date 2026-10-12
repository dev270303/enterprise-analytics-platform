package com.eap.records;

import org.springframework.jdbc.core.namedparam.MapSqlParameterSource;
import org.springframework.jdbc.core.namedparam.NamedParameterJdbcTemplate;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RequestParam;
import org.springframework.web.bind.annotation.RestController;

import java.sql.Types;
import java.time.LocalDate;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

/** Paginated, filterable record lists: GET /api/customers and GET /api/orders. */
@RestController
@RequestMapping("/api")
public class RecordsController {

    private final NamedParameterJdbcTemplate jdbc;

    public RecordsController(NamedParameterJdbcTemplate jdbc) {
        this.jdbc = jdbc;
    }

    @GetMapping("/customers")
    public Map<String, Object> customers(@RequestParam(required = false) String search,
                                         @RequestParam(required = false) String segment,
                                         @RequestParam(required = false) String region,
                                         @RequestParam(defaultValue = "0") int page,
                                         @RequestParam(defaultValue = "20") int size) {
        MapSqlParameterSource p = new MapSqlParameterSource()
                .addValue("search", blank(search) ? null : "%" + search.trim().toLowerCase() + "%", Types.VARCHAR)
                .addValue("segment", blank(segment) ? null : segment, Types.VARCHAR)
                .addValue("region", blank(region) ? null : region, Types.VARCHAR);
        String where = """
                WHERE (CAST(:search AS text) IS NULL OR lower(cu.full_name) LIKE :search)
                  AND (CAST(:segment AS text) IS NULL OR cu.segment = :segment)
                  AND (CAST(:region AS text) IS NULL OR r.name = :region)""";
        String from = " FROM customers cu JOIN cities ci ON ci.id = cu.city_id JOIN regions r ON r.id = ci.region_id ";
        return page(p, page, size,
                "SELECT cu.id, cu.full_name, cu.segment, ci.name AS city, r.name AS region, cu.signup_date, "
                        + "cs.segment_label AS rfm_segment, cs.churn_risk" + from
                        + "LEFT JOIN customer_segments cs ON cs.customer_id = cu.id " + where + " ORDER BY cu.id",
                "SELECT count(*)" + from + where);
    }

    @GetMapping("/orders")
    public Map<String, Object> orders(@RequestParam(required = false) String status,
                                      @RequestParam(required = false) String region,
                                      @RequestParam(required = false) LocalDate from,
                                      @RequestParam(required = false) LocalDate to,
                                      @RequestParam(defaultValue = "0") int page,
                                      @RequestParam(defaultValue = "20") int size) {
        MapSqlParameterSource p = new MapSqlParameterSource()
                .addValue("status", blank(status) ? null : status.toUpperCase(), Types.VARCHAR)
                .addValue("region", blank(region) ? null : region, Types.VARCHAR)
                .addValue("from", from, Types.DATE)
                .addValue("to", to, Types.DATE);
        String where = """
                WHERE (CAST(:status AS text) IS NULL OR o.status = :status)
                  AND (CAST(:region AS text) IS NULL OR r.name = :region)
                  AND (CAST(:from AS date) IS NULL OR o.order_date >= :from)
                  AND (CAST(:to AS date) IS NULL OR o.order_date <= :to)""";
        String fromSql = " FROM orders o JOIN customers cu ON cu.id = o.customer_id JOIN cities ci ON ci.id = cu.city_id "
                + "JOIN regions r ON r.id = ci.region_id ";
        return page(p, page, size,
                "SELECT o.id, o.order_no, o.order_date, o.status, o.channel, o.total_amount, cu.full_name AS customer, "
                        + "ci.name AS city, r.name AS region" + fromSql + where + " ORDER BY o.order_date DESC, o.id DESC",
                "SELECT count(*)" + fromSql + where);
    }

    private Map<String, Object> page(MapSqlParameterSource p, int page, int size, String sql, String countSql) {
        int safeSize = Math.min(Math.max(size, 1), 100);
        int safePage = Math.max(page, 0);
        p.addValue("limit", safeSize).addValue("offset", (long) safePage * safeSize);
        List<Map<String, Object>> rows = jdbc.queryForList(sql + " LIMIT :limit OFFSET :offset", p).stream()
                .map(RecordsController::iso).toList();
        Long total = jdbc.queryForObject(countSql, p, Long.class);
        Map<String, Object> body = new LinkedHashMap<>();
        body.put("content", rows);
        body.put("page", safePage);
        body.put("size", safeSize);
        body.put("totalElements", total);
        body.put("totalPages", total == null ? 0 : (total + safeSize - 1) / safeSize);
        return body;
    }

    private static boolean blank(String s) {
        return s == null || s.isBlank();
    }

    static Map<String, Object> iso(Map<String, Object> row) {
        Map<String, Object> out = new LinkedHashMap<>();
        row.forEach((k, v) -> out.put(k, v instanceof java.sql.Timestamp t ? t.toLocalDateTime() :
                v instanceof java.sql.Date d ? d.toLocalDate() : v));
        return out;
    }
}
