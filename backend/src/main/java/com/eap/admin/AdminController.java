package com.eap.admin;

import com.eap.auth.AppUserRepository;
import com.eap.common.CacheService;
import com.eap.integration.DataServiceClient;
import com.fasterxml.jackson.databind.JsonNode;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RestController;

import java.util.List;
import java.util.Map;

/** ADMIN-only endpoints (enforced in SecurityConfig for /api/admin/**). */
@RestController
@RequestMapping("/api/admin")
public class AdminController {

    private final AppUserRepository users;
    private final CacheService cache;
    private final DataServiceClient dataService;

    public AdminController(AppUserRepository users, CacheService cache, DataServiceClient dataService) {
        this.users = users;
        this.cache = cache;
        this.dataService = dataService;
    }

    @GetMapping("/users")
    public List<Map<String, Object>> users() {
        return users.findAll().stream()
                .map(u -> Map.<String, Object>of(
                        "id", u.getId(),
                        "username", u.getUsername(),
                        "displayName", u.getDisplayName(),
                        "role", u.getRole().name(),
                        "enabled", u.isEnabled(),
                        "demo", u.isDemo()))
                .toList();
    }

    /** Clears cached analytics results, e.g. after new data has been loaded. */
    @PostMapping("/cache/clear")
    public Map<String, String> clearCache() {
        cache.clearAll();
        return Map.of("status", "cleared");
    }

    /** Re-runs the RFM / K-Means segmentation (deterministic; safe for demo admins too). */
    @PostMapping("/segments/run")
    public JsonNode runSegments() {
        JsonNode result = dataService.postJson("/analysis/segments/run", null, null);
        cache.clearAll();
        return result;
    }

    /** Re-indexes the bundled policy documents for the AI assistant. */
    @PostMapping("/documents/reindex")
    public JsonNode reindex() {
        return dataService.postJson("/rag/reindex", null, null);
    }
}
