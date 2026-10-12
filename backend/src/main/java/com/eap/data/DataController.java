package com.eap.data;

import com.eap.auth.CurrentUser;
import com.eap.common.CacheService;
import com.eap.integration.DataServiceClient;
import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.node.ObjectNode;
import org.springframework.http.HttpHeaders;
import org.springframework.http.HttpStatus;
import org.springframework.http.MediaType;
import org.springframework.http.ResponseEntity;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.security.core.annotation.AuthenticationPrincipal;
import org.springframework.security.oauth2.jwt.Jwt;
import org.springframework.web.bind.annotation.*;
import org.springframework.web.multipart.MultipartFile;
import org.springframework.web.server.ResponseStatusException;

import java.io.IOException;
import java.util.HashMap;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.Set;

/**
 * Data ingestion (Phase 2). Uploads go to the Python ETL pipeline; run history and data-quality
 * issues are read straight from the audit tables.
 *
 * Public demo accounts always run in DRY_RUN mode (validate + full report, then rollback),
 * so the shared demo database cannot be changed by visitors. Only the owner can COMMIT.
 */
@RestController
@RequestMapping("/api/data")
public class DataController {

    private static final Set<String> ENTITIES = Set.of("customers", "products", "employees", "orders", "order_items", "tickets");

    private final DataServiceClient dataService;
    private final JdbcTemplate jdbc;
    private final CacheService cache;

    public DataController(DataServiceClient dataService, JdbcTemplate jdbc, CacheService cache) {
        this.dataService = dataService;
        this.jdbc = jdbc;
        this.cache = cache;
    }

    @GetMapping("/runs")
    public List<Map<String, Object>> runs() {
        return jdbc.queryForList("""
                SELECT id, entity, file_name, source, triggered_by, mode, status, rows_in, rows_loaded, rows_inserted,
                       rows_updated, rows_rejected, rows_duplicate, rows_flagged, quality_score, duration_ms, started_at
                FROM etl_runs ORDER BY id DESC LIMIT 40""").stream().map(DataController::iso).toList();
    }

    @GetMapping("/runs/{id}")
    public Map<String, Object> run(@PathVariable long id) {
        List<Map<String, Object>> run = jdbc.queryForList("SELECT * FROM etl_runs WHERE id = ?", id);
        if (run.isEmpty()) throw new ResponseStatusException(HttpStatus.NOT_FOUND, "run not found");
        Map<String, Object> body = new LinkedHashMap<>(iso(run.get(0)));
        body.put("issue_summary", jdbc.queryForList("""
                SELECT severity, rule, column_name, count(*) AS count FROM etl_issues WHERE run_id = ?
                GROUP BY severity, rule, column_name
                ORDER BY CASE severity WHEN 'ERROR' THEN 0 WHEN 'WARNING' THEN 1 ELSE 2 END, count(*) DESC""", id));
        body.put("issues", jdbc.queryForList("""
                SELECT row_number, severity, rule, column_name, raw_value, message FROM etl_issues WHERE run_id = ?
                ORDER BY CASE severity WHEN 'ERROR' THEN 0 WHEN 'WARNING' THEN 1 ELSE 2 END, row_number NULLS FIRST
                LIMIT 300""", id));
        return body;
    }

    @PostMapping("/upload")
    public JsonNode upload(@RequestParam("file") MultipartFile file,
                           @RequestParam(required = false) String entity,
                           @RequestParam(defaultValue = "false") boolean commit,
                           @AuthenticationPrincipal Jwt jwt) throws IOException {
        CurrentUser user = CurrentUser.from(jwt);
        if (file.isEmpty()) throw new ResponseStatusException(HttpStatus.BAD_REQUEST, "empty file");
        String name = file.getOriginalFilename() == null ? "upload.csv" : file.getOriginalFilename();
        if (!name.toLowerCase().endsWith(".csv")) throw new ResponseStatusException(HttpStatus.BAD_REQUEST, "please upload a .csv file");
        if (entity != null && !entity.isBlank() && !ENTITIES.contains(entity)) {
            throw new ResponseStatusException(HttpStatus.BAD_REQUEST, "entity must be one of " + ENTITIES);
        }
        boolean reallyCommit = commit && user.isOwner();
        Map<String, Object> query = new HashMap<>();
        query.put("file_name", name);
        query.put("entity", entity == null || entity.isBlank() ? null : entity);
        query.put("commit", reallyCommit);
        query.put("user", user.username());
        JsonNode result = dataService.postBytes("/etl/upload", query, file.getBytes(), MediaType.TEXT_PLAIN);
        return afterRun(result, commit, reallyCommit);
    }

    @GetMapping("/samples")
    public JsonNode samples(@RequestParam(defaultValue = "false") boolean refresh) {
        return dataService.get("/etl/samples", Map.of("refresh", refresh));
    }

    @GetMapping("/samples/{name}")
    public ResponseEntity<byte[]> sampleFile(@PathVariable String name) {
        if (!name.matches("[a-z_]+\\.csv")) throw new ResponseStatusException(HttpStatus.BAD_REQUEST, "bad file name");
        byte[] body = dataService.download("/etl/samples/" + name);
        return ResponseEntity.ok()
                .header(HttpHeaders.CONTENT_DISPOSITION, "attachment; filename=\"" + name + "\"")
                .contentType(MediaType.parseMediaType("text/csv"))
                .body(body);
    }

    @PostMapping("/samples/run")
    public JsonNode runSamples(@RequestParam(defaultValue = "false") boolean commit, @AuthenticationPrincipal Jwt jwt) {
        CurrentUser user = CurrentUser.from(jwt);
        boolean reallyCommit = commit && user.isOwner();
        JsonNode result = dataService.postJson("/etl/samples/run", Map.of("commit", reallyCommit, "user", user.username()), null);
        return afterRun(result, commit, reallyCommit);
    }

    private JsonNode afterRun(JsonNode result, boolean requested, boolean committed) {
        if (committed) {
            cache.clearAll();
        }
        if (result instanceof ObjectNode obj) {
            obj.put("forced_dry_run", requested && !committed);
        }
        return result;
    }

    static Map<String, Object> iso(Map<String, Object> row) {
        Map<String, Object> out = new LinkedHashMap<>();
        row.forEach((k, v) -> out.put(k, v instanceof java.sql.Timestamp t ? t.toLocalDateTime() :
                v instanceof java.sql.Date d ? d.toLocalDate() : v));
        return out;
    }
}
