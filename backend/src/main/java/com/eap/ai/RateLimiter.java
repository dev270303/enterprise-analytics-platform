package com.eap.ai;

import org.springframework.beans.factory.annotation.Value;
import org.springframework.stereotype.Component;

import java.time.Clock;
import java.time.Duration;
import java.util.ArrayDeque;
import java.util.Deque;
import java.util.Map;
import java.util.concurrent.ConcurrentHashMap;

/**
 * Sliding-window rate limiter, per user, in memory.
 * The demo credentials are public, so AI calls (which cost model quota) are limited per user here,
 * and globally per day in the data service.
 */
@Component
public class RateLimiter {

    private final Map<String, Deque<Long>> hits = new ConcurrentHashMap<>();
    private final int demoLimit;
    private final int ownerLimit;
    private final Duration window;
    private final Clock clock;

    public RateLimiter(@Value("${app.ai.demo-requests-per-hour:15}") int demoLimit,
                       @Value("${app.ai.owner-requests-per-hour:200}") int ownerLimit) {
        this(demoLimit, ownerLimit, Duration.ofHours(1), Clock.systemUTC());
    }

    RateLimiter(int demoLimit, int ownerLimit, Duration window, Clock clock) {
        this.demoLimit = demoLimit;
        this.ownerLimit = ownerLimit;
        this.window = window;
        this.clock = clock;
    }

    /** @return seconds until the next request is allowed, or 0 if this request is allowed (and recorded). */
    public long tryAcquire(String key, boolean demo) {
        int limit = demo ? demoLimit : ownerLimit;
        long now = clock.millis();
        long cutoff = now - window.toMillis();
        Deque<Long> q = hits.computeIfAbsent(key, k -> new ArrayDeque<>());
        synchronized (q) {
            while (!q.isEmpty() && q.peekFirst() <= cutoff) {
                q.pollFirst();
            }
            if (q.size() >= limit) {
                return Math.max(1, (q.peekFirst() + window.toMillis() - now) / 1000);
            }
            q.addLast(now);
            return 0;
        }
    }

    public int limitFor(boolean demo) {
        return demo ? demoLimit : ownerLimit;
    }
}
