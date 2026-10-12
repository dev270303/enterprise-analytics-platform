package com.eap.ai;

import com.eap.auth.CurrentUser;
import com.eap.integration.DataServiceClient;
import com.fasterxml.jackson.databind.JsonNode;
import jakarta.validation.Valid;
import jakarta.validation.constraints.NotBlank;
import jakarta.validation.constraints.Size;
import org.springframework.http.HttpStatus;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.security.core.annotation.AuthenticationPrincipal;
import org.springframework.security.oauth2.jwt.Jwt;
import org.springframework.web.bind.annotation.*;
import org.springframework.web.multipart.MultipartFile;
import org.springframework.web.server.ResponseStatusException;

import java.io.IOException;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

/**
 * AI analyst endpoints (ADMIN, ANALYST, MANAGER via SecurityConfig).
 * Natural-language questions are answered by the Python service: text-to-SQL over curated views
 * for data questions, retrieval-augmented generation over policy documents for policy questions.
 */
@RestController
@RequestMapping("/api/ai")
public class AiController {

    public record AskRequest(@NotBlank @Size(max = 500) String question) {
    }

    private final DataServiceClient dataService;
    private final RateLimiter rateLimiter;
    private final JdbcTemplate jdbc;

    public AiController(DataServiceClient dataService, RateLimiter rateLimiter, JdbcTemplate jdbc) {
        this.dataService = dataService;
        this.rateLimiter = rateLimiter;
        this.jdbc = jdbc;
    }

    @PostMapping("/ask")
    public JsonNode ask(@Valid @RequestBody AskRequest request, @AuthenticationPrincipal Jwt jwt) {
        CurrentUser user = CurrentUser.from(jwt);
        long wait = rateLimiter.tryAcquire(user.username(), user.demo());
        if (wait > 0) {
            throw new ResponseStatusException(HttpStatus.TOO_MANY_REQUESTS,
                    "Demo limit reached: " + rateLimiter.limitFor(user.demo()) + " AI questions per hour. Try again in "
                            + Math.max(1, wait / 60) + " minute(s).");
        }
        return dataService.postJson("/ai/ask", null, Map.of("question", request.question(), "user", user.username()));
    }

    /** Also used by the dashboard to wake the (free-tier) service before the user asks anything. */
    @GetMapping("/status")
    public Object status() {
        if (!dataService.configured()) {
            return Map.of("configured", false);
        }
        return dataService.get("/status", null);
    }

    @GetMapping("/documents")
    public JsonNode documents() {
        return dataService.get("/rag/documents", null);
    }

    @PostMapping("/documents")
    public JsonNode uploadDocument(@RequestParam("file") MultipartFile file, @AuthenticationPrincipal Jwt jwt) throws IOException {
        CurrentUser user = CurrentUser.from(jwt);
        if (!user.isOwner()) {
            throw new ResponseStatusException(HttpStatus.FORBIDDEN,
                    "Uploading documents changes the shared knowledge base, so only the owner account can do it.");
        }
        if (file.isEmpty()) {
            throw new ResponseStatusException(HttpStatus.BAD_REQUEST, "empty file");
        }
        String name = file.getOriginalFilename() == null ? "document.md" : file.getOriginalFilename();
        return dataService.postFile("/rag/documents", name, file.getBytes(), Map.of("uploaded_by", user.username()));
    }

    /** Recent questions (audit log). Analysts and admins only. */
    @GetMapping("/history")
    public List<Map<String, Object>> history(@AuthenticationPrincipal Jwt jwt) {
        CurrentUser user = CurrentUser.from(jwt);
        boolean privileged = user.role().name().equals("ADMIN") || user.role().name().equals("ANALYST");
        String sql = """
                SELECT id, asked_by, question, route, status, row_count, attempts, latency_ms, created_at
                FROM ai_queries %s ORDER BY id DESC LIMIT 30""".formatted(privileged ? "" : "WHERE asked_by = ?");
        List<Map<String, Object>> rows = privileged ? jdbc.queryForList(sql) : jdbc.queryForList(sql, user.username());
        return rows.stream().map(AiController::isoDates).toList();
    }

    static Map<String, Object> isoDates(Map<String, Object> row) {
        Map<String, Object> out = new LinkedHashMap<>();
        row.forEach((k, v) -> out.put(k, v instanceof java.sql.Timestamp t ? t.toLocalDateTime() :
                v instanceof java.sql.Date d ? d.toLocalDate() : v));
        return out;
    }
}
