package com.eap.insights;

import com.eap.auth.CurrentUser;
import com.eap.integration.DataServiceClient;
import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import org.springframework.http.HttpStatus;
import org.springframework.http.MediaType;
import org.springframework.http.ResponseEntity;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.security.access.prepost.PreAuthorize;
import org.springframework.security.core.annotation.AuthenticationPrincipal;
import org.springframework.security.oauth2.jwt.Jwt;
import org.springframework.web.bind.annotation.*;
import org.springframework.web.server.ResponseStatusException;

import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

/** Automated daily insight reports (Phase 4): produced by a scheduled job, read here. */
@RestController
@RequestMapping("/api/insights")
public class InsightsController {

    private final JdbcTemplate jdbc;
    private final ObjectMapper json;
    private final DataServiceClient dataService;

    public InsightsController(JdbcTemplate jdbc, ObjectMapper json, DataServiceClient dataService) {
        this.jdbc = jdbc;
        this.json = json;
        this.dataService = dataService;
    }

    @GetMapping
    public List<Map<String, Object>> list() {
        return jdbc.queryForList("""
                SELECT id, as_of_date, generated_at, trigger, headline, email_status
                FROM insight_reports ORDER BY id DESC LIMIT 30""").stream().map(InsightsController::iso).toList();
    }

    @GetMapping("/latest")
    public Map<String, Object> latest() {
        List<Map<String, Object>> rows = jdbc.queryForList("SELECT id FROM insight_reports ORDER BY id DESC LIMIT 1");
        if (rows.isEmpty()) return Map.of();
        return report(((Number) rows.get(0).get("id")).longValue());
    }

    @GetMapping("/{id}")
    public Map<String, Object> report(@PathVariable long id) {
        List<Map<String, Object>> rows = jdbc.queryForList("""
                SELECT id, as_of_date, generated_at, trigger, headline, summary, payload::text AS payload, email_status
                FROM insight_reports WHERE id = ?""", id);
        if (rows.isEmpty()) throw new ResponseStatusException(HttpStatus.NOT_FOUND, "report not found");
        Map<String, Object> row = iso(rows.get(0));
        try {
            row.put("payload", json.readTree((String) row.get("payload")));
        } catch (Exception e) {
            row.put("payload", null);
        }
        return row;
    }

    /** The exact HTML e-mail that was (or would be) sent. */
    @GetMapping(value = "/{id}/email", produces = MediaType.TEXT_HTML_VALUE)
    public ResponseEntity<String> email(@PathVariable long id) {
        List<String> html = jdbc.queryForList("SELECT html FROM insight_reports WHERE id = ?", String.class, id);
        if (html.isEmpty()) throw new ResponseStatusException(HttpStatus.NOT_FOUND, "report not found");
        return ResponseEntity.ok()
                .header("Content-Security-Policy", "default-src 'none'; style-src 'unsafe-inline'")
                .body(html.get(0));
    }

    /** Generate a report now. Only the owner may also e-mail it. */
    @PostMapping("/run")
    @PreAuthorize("hasRole('ADMIN')")
    public JsonNode run(@RequestParam(defaultValue = "false") boolean email, @AuthenticationPrincipal Jwt jwt) {
        CurrentUser user = CurrentUser.from(jwt);
        return dataService.postJson("/insights/run", Map.of("send_email", email && user.isOwner(), "trigger", "manual"), null);
    }

    static Map<String, Object> iso(Map<String, Object> row) {
        Map<String, Object> out = new LinkedHashMap<>();
        row.forEach((k, v) -> out.put(k, v instanceof java.sql.Timestamp t ? t.toLocalDateTime() :
                v instanceof java.sql.Date d ? d.toLocalDate() : v));
        return out;
    }
}
