package com.eap.ai;

import org.junit.jupiter.api.Test;

import java.time.Clock;
import java.time.Duration;
import java.time.Instant;
import java.time.ZoneOffset;

import static org.assertj.core.api.Assertions.assertThat;

class RateLimiterTest {

    static class MutableClock extends Clock {
        Instant now = Instant.parse("2026-10-01T10:00:00Z");
        @Override public ZoneOffset getZone() { return ZoneOffset.UTC; }
        @Override public Clock withZone(java.time.ZoneId zone) { return this; }
        @Override public Instant instant() { return now; }
    }

    @Test
    void slidingWindowPerUser() {
        MutableClock clock = new MutableClock();
        RateLimiter limiter = new RateLimiter(2, 5, Duration.ofHours(1), clock);
        assertThat(limiter.tryAcquire("a", true)).isZero();
        assertThat(limiter.tryAcquire("a", true)).isZero();
        assertThat(limiter.tryAcquire("a", true)).isPositive();      // third within the hour is refused
        assertThat(limiter.tryAcquire("b", true)).isZero();          // other users unaffected
        clock.now = clock.now.plus(Duration.ofMinutes(61));
        assertThat(limiter.tryAcquire("a", true)).isZero();          // window slid
        for (int i = 0; i < 5; i++) assertThat(limiter.tryAcquire("owner", false)).isZero();
        assertThat(limiter.tryAcquire("owner", false)).isPositive();
    }
}
