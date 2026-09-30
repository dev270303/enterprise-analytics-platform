package com.eap.config;

import org.springframework.boot.context.properties.ConfigurationProperties;

/**
 * @param secret     HMAC key for HS256, at least 32 characters
 * @param ttlMinutes token lifetime
 */
@ConfigurationProperties(prefix = "app.jwt")
public record JwtProperties(String secret, long ttlMinutes) {
    public JwtProperties {
        if (secret == null || secret.length() < 32) {
            throw new IllegalStateException("app.jwt.secret (JWT_SECRET) must be at least 32 characters");
        }
        if (ttlMinutes <= 0) ttlMinutes = 480;
    }
}
