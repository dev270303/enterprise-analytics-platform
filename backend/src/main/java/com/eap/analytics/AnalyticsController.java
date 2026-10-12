package com.eap.analytics;

import org.springframework.http.HttpStatus;
import org.springframework.security.access.prepost.PreAuthorize;
import org.springframework.web.bind.annotation.*;
import org.springframework.web.server.ResponseStatusException;

import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

/**
 * Read-only analytics API behind the dashboard. Every endpoint maps to one file in resources/sql.
 * Region-aware endpoints accept ?region=North|South|East|West (omit for all regions).
 */
@RestController
@RequestMapping("/api")
public class AnalyticsController {

    private final AnalyticsService analytics;
    private final SqlCatalog catalog;

    public AnalyticsController(AnalyticsService analytics, SqlCatalog catalog) {
        this.analytics = analytics;
        this.catalog = catalog;
    }

    // ---- Overview -------------------------------------------------------------------------

    @GetMapping("/dashboard/summary")
    public Map<String, Object> summary(@RequestParam(required = false) String region) {
        String r = validRegion(region);
        Map<String, Object> body = new LinkedHashMap<>();
        body.put("sales", first(analytics.query("kpi_summary", r)));
        body.put("support", first(analytics.query("support_kpis", null)));
        return body;
    }

    @GetMapping("/analytics/revenue-trend")
    public List<Map<String, Object>> revenueTrend(@RequestParam(required = false) String region) {
        return analytics.query("revenue_trend", validRegion(region));
    }

    @GetMapping("/analytics/regions")
    public List<Map<String, Object>> regions() {
        return analytics.query("region_performance", null);
    }

    // ---- Sales ----------------------------------------------------------------------------

    @GetMapping("/analytics/top-products")
    public List<Map<String, Object>> topProducts(@RequestParam(required = false) String region) {
        return analytics.query("top_products", validRegion(region));
    }

    @GetMapping("/analytics/categories")
    public List<Map<String, Object>> categories(@RequestParam(required = false) String region) {
        return analytics.query("category_mix", validRegion(region));
    }

    @GetMapping("/analytics/sales-reps")
    public List<Map<String, Object>> salesReps() {
        return analytics.query("rep_leaderboard", null);
    }

    // ---- Customers ------------------------------------------------------------------------

    @GetMapping("/analytics/top-customers")
    public List<Map<String, Object>> topCustomers(@RequestParam(required = false) String region) {
        return analytics.query("top_customers", validRegion(region));
    }

    @GetMapping("/analytics/customer-growth")
    public List<Map<String, Object>> customerGrowth(@RequestParam(required = false) String region) {
        return analytics.query("customer_growth", validRegion(region));
    }

    @GetMapping("/analytics/cohorts")
    public List<Map<String, Object>> cohorts(@RequestParam(required = false) String region) {
        return analytics.query("cohort_retention", validRegion(region));
    }

    @GetMapping("/analytics/segments")
    public List<Map<String, Object>> segments(@RequestParam(required = false) String region) {
        return analytics.query("segment_summary", validRegion(region));
    }

    @GetMapping("/analytics/rfm-segments")
    public Map<String, Object> rfmSegments(@RequestParam(required = false) String region) {
        String r = validRegion(region);
        Map<String, Object> body = new LinkedHashMap<>();
        body.put("segments", analytics.query("rfm_segments", r));
        body.put("watchlist", analytics.query("churn_watchlist", r));
        return body;
    }

    // ---- Support (every role, including EMPLOYEE) ------------------------------------------

    @GetMapping("/analytics/support/summary")
    public Map<String, Object> supportSummary() {
        return first(analytics.query("support_kpis", null));
    }

    @GetMapping("/analytics/support/sla")
    public List<Map<String, Object>> ticketSla() {
        return analytics.query("ticket_sla", null);
    }

    @GetMapping("/analytics/support/trend")
    public List<Map<String, Object>> ticketTrend() {
        return analytics.query("ticket_trend", null);
    }

    @GetMapping("/analytics/support/agents")
    public List<Map<String, Object>> agents() {
        return analytics.query("agent_workload", null);
    }

    // ---- SQL transparency (role-restricted) -----------------------------------------------

    @GetMapping("/analytics/sql/{name}")
    @PreAuthorize("hasAnyRole('ANALYST','ADMIN')")
    public Map<String, String> sql(@PathVariable String name) {
        String text = catalog.find(name)
                .orElseThrow(() -> new ResponseStatusException(HttpStatus.NOT_FOUND, "Unknown query"));
        return Map.of("name", name, "sql", text);
    }

    // called on the proxied service so the cache applies (no self-invocation)
    private static Map<String, Object> first(List<Map<String, Object>> rows) {
        return rows.isEmpty() ? Map.of() : rows.get(0);
    }

    private static String validRegion(String region) {
        if (region == null || region.isBlank() || region.equalsIgnoreCase("all")) return null;
        String normalized = region.substring(0, 1).toUpperCase() + region.substring(1).toLowerCase();
        if (!AnalyticsService.REGIONS.contains(normalized)) {
            throw new ResponseStatusException(HttpStatus.BAD_REQUEST, "region must be one of " + AnalyticsService.REGIONS);
        }
        return normalized;
    }
}
