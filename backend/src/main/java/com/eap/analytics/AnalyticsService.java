package com.eap.analytics;

import org.springframework.cache.annotation.Cacheable;
import org.springframework.stereotype.Service;

import java.util.List;
import java.util.Map;
import java.util.Set;

@Service
public class AnalyticsService {

    public static final Set<String> REGIONS = Set.of("North", "South", "East", "West");

    private final AnalyticsRepository repository;

    public AnalyticsService(AnalyticsRepository repository) {
        this.repository = repository;
    }

    /**
     * Results are cached per (query, region). The dataset only changes on a data load, and caching
     * keeps the serverless database asleep (and inside free-tier compute limits) between visitors.
     */
    @Cacheable(cacheNames = "analytics", key = "#queryName + ':' + (#region ?: 'ALL')")
    public List<Map<String, Object>> query(String queryName, String region) {
        return repository.run(queryName, region);
    }
}
