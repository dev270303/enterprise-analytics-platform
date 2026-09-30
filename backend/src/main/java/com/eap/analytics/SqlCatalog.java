package com.eap.analytics;

import org.springframework.core.io.Resource;
import org.springframework.core.io.support.PathMatchingResourcePatternResolver;
import org.springframework.stereotype.Component;

import java.io.IOException;
import java.io.UncheckedIOException;
import java.nio.charset.StandardCharsets;
import java.util.Collections;
import java.util.Map;
import java.util.Optional;
import java.util.TreeMap;

/**
 * Loads every analytical query from classpath:sql/*.sql once at startup.
 * Keeping SQL in versioned .sql files (instead of Java strings) makes it reviewable,
 * runnable in psql as-is, and lets the dashboard show the exact query behind each chart.
 */
@Component
public class SqlCatalog {

    private final Map<String, String> queries;

    public SqlCatalog() {
        Map<String, String> loaded = new TreeMap<>();
        try {
            Resource[] resources = new PathMatchingResourcePatternResolver().getResources("classpath:sql/*.sql");
            for (Resource r : resources) {
                String filename = r.getFilename();
                if (filename == null) continue;
                String name = filename.substring(0, filename.length() - ".sql".length());
                loaded.put(name, r.getContentAsString(StandardCharsets.UTF_8));
            }
        } catch (IOException e) {
            throw new UncheckedIOException("Could not load analytical SQL files", e);
        }
        if (loaded.isEmpty()) {
            throw new IllegalStateException("No SQL files found under classpath:sql/");
        }
        this.queries = Collections.unmodifiableMap(loaded);
    }

    public String get(String name) {
        String sql = queries.get(name);
        if (sql == null) throw new IllegalArgumentException("Unknown query: " + name);
        return sql;
    }

    public Optional<String> find(String name) {
        return Optional.ofNullable(queries.get(name));
    }

    public Map<String, String> all() {
        return queries;
    }
}
