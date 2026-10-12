package com.eap.integration;

import org.springframework.boot.context.properties.ConfigurationProperties;

import java.time.Duration;

/**
 * @param url             base URL of the Python data & AI service (empty = not deployed)
 * @param token           shared secret sent as X-Internal-Token
 * @param readTimeout     generous: the free-tier service may need ~60 s to wake up, plus LLM latency
 */
@ConfigurationProperties(prefix = "app.data-service")
public record DataServiceProperties(String url, String token, Duration connectTimeout, Duration readTimeout) {
    public DataServiceProperties {
        if (connectTimeout == null) connectTimeout = Duration.ofSeconds(15);
        if (readTimeout == null) readTimeout = Duration.ofSeconds(150);
    }

    public boolean configured() {
        return url != null && !url.isBlank() && token != null && !token.isBlank();
    }
}
