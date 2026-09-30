package com.eap.analytics;

import org.springframework.jdbc.core.namedparam.MapSqlParameterSource;
import org.springframework.jdbc.core.namedparam.NamedParameterJdbcTemplate;
import org.springframework.stereotype.Repository;

import java.sql.ResultSet;
import java.sql.ResultSetMetaData;
import java.sql.SQLException;
import java.sql.Timestamp;
import java.sql.Types;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

/**
 * Plain JDBC for analytics: aggregation-heavy SQL with CTEs and window functions maps poorly onto
 * JPA entities, so results are returned as ordered column-name -> value rows.
 */
@Repository
public class AnalyticsRepository {

    private final NamedParameterJdbcTemplate jdbc;
    private final SqlCatalog catalog;

    public AnalyticsRepository(NamedParameterJdbcTemplate jdbc, SqlCatalog catalog) {
        this.jdbc = jdbc;
        this.catalog = catalog;
    }

    public List<Map<String, Object>> run(String queryName, String region) {
        MapSqlParameterSource params = new MapSqlParameterSource()
                .addValue("region", region, Types.VARCHAR);
        return jdbc.query(catalog.get(queryName), params, AnalyticsRepository::mapRow);
    }

    private static Map<String, Object> mapRow(ResultSet rs, int rowNum) throws SQLException {
        ResultSetMetaData meta = rs.getMetaData();
        Map<String, Object> row = new LinkedHashMap<>();
        for (int i = 1; i <= meta.getColumnCount(); i++) {
            Object value = rs.getObject(i);
            // java.sql date types -> java.time so Jackson writes ISO-8601 strings
            if (value instanceof java.sql.Date d) value = d.toLocalDate();
            else if (value instanceof Timestamp t) value = t.toLocalDateTime();
            row.put(meta.getColumnLabel(i), value);
        }
        return row;
    }
}
