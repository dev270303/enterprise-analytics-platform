package com.eap.admin;

import com.eap.auth.AppUserRepository;
import org.springframework.cache.CacheManager;
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
    private final CacheManager cacheManager;

    public AdminController(AppUserRepository users, CacheManager cacheManager) {
        this.users = users;
        this.cacheManager = cacheManager;
    }

    @GetMapping("/users")
    public List<Map<String, Object>> users() {
        return users.findAll().stream()
                .map(u -> Map.<String, Object>of(
                        "id", u.getId(),
                        "username", u.getUsername(),
                        "displayName", u.getDisplayName(),
                        "role", u.getRole().name(),
                        "enabled", u.isEnabled()))
                .toList();
    }

    /** Clears cached analytics results, e.g. after new data has been loaded. */
    @PostMapping("/cache/clear")
    public Map<String, String> clearCache() {
        cacheManager.getCacheNames().forEach(name -> {
            var cache = cacheManager.getCache(name);
            if (cache != null) cache.clear();
        });
        return Map.of("status", "cleared");
    }
}
