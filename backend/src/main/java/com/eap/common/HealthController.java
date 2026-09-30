package com.eap.common;

import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.RestController;

import java.time.Instant;
import java.util.Map;

/**
 * Liveness probe for the hosting platform. Deliberately does NOT touch the database,
 * so health checks never wake the serverless Postgres instance.
 */
@RestController
public class HealthController {

    @GetMapping("/api/health")
    public Map<String, Object> health() {
        return Map.of("status", "UP", "time", Instant.now().toString());
    }
}
