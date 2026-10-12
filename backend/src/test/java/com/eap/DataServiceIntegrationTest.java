package com.eap;

import com.fasterxml.jackson.databind.ObjectMapper;
import com.sun.net.httpserver.HttpServer;
import org.junit.jupiter.api.AfterAll;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.autoconfigure.web.servlet.AutoConfigureMockMvc;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.http.MediaType;
import org.springframework.mock.web.MockMultipartFile;
import org.springframework.test.context.DynamicPropertyRegistry;
import org.springframework.test.context.DynamicPropertySource;
import org.springframework.test.web.servlet.MockMvc;

import java.io.IOException;
import java.io.OutputStream;
import java.net.InetSocketAddress;
import java.nio.charset.StandardCharsets;
import java.util.List;
import java.util.concurrent.CopyOnWriteArrayList;

import static org.assertj.core.api.Assertions.assertThat;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.get;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.multipart;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.post;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.jsonPath;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.status;

/**
 * Backend <-> data-service contract, against an in-process fake of the Python service
 * (JDK HttpServer): token forwarding, rate limiting, and demo accounts forced to dry-run.
 */
@SpringBootTest(properties = {
        "app.ai.demo-requests-per-hour=3",
        "app.owner.username=owner",
        "app.owner.password=owner-secret-123"
})
@AutoConfigureMockMvc
class DataServiceIntegrationTest {

    static final List<String> requests = new CopyOnWriteArrayList<>();
    static final HttpServer fake;

    static {
        try {
            fake = HttpServer.create(new InetSocketAddress("127.0.0.1", 0), 0);
        } catch (IOException e) {
            throw new IllegalStateException(e);
        }
        fake.createContext("/", exchange -> {
            exchange.getRequestBody().readAllBytes();
            String token = exchange.getRequestHeaders().getFirst("X-Internal-Token");
            String uri = exchange.getRequestURI().toString();
            requests.add(exchange.getRequestMethod() + " " + uri + " token=" + token);
            String body;
            int code = 200;
            if (!"fake-token".equals(token)) {
                code = 401;
                body = "{\"detail\":\"invalid internal token\"}";
            } else if (uri.startsWith("/ai/ask")) {
                body = "{\"route\":\"sql\",\"status\":\"OK\",\"answer\":\"South leads revenue.\",\"rows\":[[\"South\",1]],\"columns\":[\"region\",\"revenue\"]}";
            } else if (uri.startsWith("/etl/upload")) {
                body = "{\"entity\":\"orders\",\"status\":\"SUCCESS\",\"mode\":\"" + (uri.contains("commit=true") ? "COMMIT" : "DRY_RUN") + "\"}";
            } else if (uri.startsWith("/rag/documents")) {
                body = "{\"status\":\"created\"}";
            } else {
                body = "{\"ok\":true}";
            }
            byte[] bytes = body.getBytes(StandardCharsets.UTF_8);
            exchange.getResponseHeaders().add("Content-Type", "application/json");
            exchange.sendResponseHeaders(code, bytes.length);
            try (OutputStream os = exchange.getResponseBody()) {
                os.write(bytes);
            }
        });
        fake.start();
    }

    @DynamicPropertySource
    static void dataService(DynamicPropertyRegistry registry) {
        registry.add("app.data-service.url", () -> "http://127.0.0.1:" + fake.getAddress().getPort());
        registry.add("app.data-service.token", () -> "fake-token");
    }

    @AfterAll
    static void stop() {
        fake.stop(0);
    }

    @Autowired MockMvc mvc;
    @Autowired ObjectMapper json;

    @BeforeEach
    void clear() {
        requests.clear();
    }

    private String login(String user, String password) throws Exception {
        String body = mvc.perform(post("/api/auth/login").contentType(MediaType.APPLICATION_JSON)
                        .content("{\"username\":\"" + user + "\",\"password\":\"" + password + "\"}"))
                .andExpect(status().isOk()).andReturn().getResponse().getContentAsString();
        return "Bearer " + json.readTree(body).get("token").asText();
    }

    @Test
    void askIsForwardedWithTheInternalTokenAndRateLimited() throws Exception {
        String manager = login("manager", "demo123");
        for (int i = 0; i < 3; i++) {
            mvc.perform(post("/api/ai/ask").header("Authorization", manager).contentType(MediaType.APPLICATION_JSON)
                            .content("{\"question\":\"Revenue by region\"}"))
                    .andExpect(status().isOk())
                    .andExpect(jsonPath("$.answer").value("South leads revenue."));
        }
        assertThat(requests).allMatch(r -> r.contains("token=fake-token"));
        mvc.perform(post("/api/ai/ask").header("Authorization", manager).contentType(MediaType.APPLICATION_JSON)
                        .content("{\"question\":\"Revenue by region\"}"))
                .andExpect(status().isTooManyRequests());
    }

    @Test
    void demoAdminUploadsAreForcedToDryRun() throws Exception {
        MockMultipartFile file = new MockMultipartFile("file", "orders.csv", "text/csv", "order_id\n".getBytes());
        mvc.perform(multipart("/api/data/upload").file(file).param("commit", "true")
                        .header("Authorization", login("admin", "demo123")))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.mode").value("DRY_RUN"))
                .andExpect(jsonPath("$.forced_dry_run").value(true));
        assertThat(requests).anyMatch(r -> r.contains("/etl/upload") && r.contains("commit=false"));
    }

    @Test
    void ownerCanCommitAndUploadDocuments() throws Exception {
        String owner = login("owner", "owner-secret-123");
        MockMultipartFile file = new MockMultipartFile("file", "orders.csv", "text/csv", "order_id\n".getBytes());
        mvc.perform(multipart("/api/data/upload").file(file).param("commit", "true").header("Authorization", owner))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.mode").value("COMMIT"))
                .andExpect(jsonPath("$.forced_dry_run").value(false));
        MockMultipartFile doc = new MockMultipartFile("file", "policy.md", "text/markdown", "# Policy\ntext".getBytes());
        mvc.perform(multipart("/api/ai/documents").file(doc).header("Authorization", owner))
                .andExpect(status().isOk());
        mvc.perform(multipart("/api/ai/documents").file(doc).header("Authorization", login("admin", "demo123")))
                .andExpect(status().isForbidden());
    }

    @Test
    void statusIsProxied() throws Exception {
        mvc.perform(get("/api/ai/status").header("Authorization", login("analyst", "demo123")))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.ok").value(true));
    }
}
