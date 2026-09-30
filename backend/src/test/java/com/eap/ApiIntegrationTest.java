package com.eap;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.autoconfigure.web.servlet.AutoConfigureMockMvc;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.http.MediaType;
import org.springframework.test.web.servlet.MockMvc;

import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.get;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.post;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.jsonPath;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.status;

/**
 * End-to-end API tests against a real PostgreSQL (CI provides one as a service container;
 * locally, point DB_URL / DB_USERNAME / DB_PASSWORD at any empty database).
 * Flyway builds the schema and seed data before the tests run.
 */
@SpringBootTest
@AutoConfigureMockMvc
class ApiIntegrationTest {

    @Autowired MockMvc mvc;
    @Autowired ObjectMapper json;

    private String login(String user) throws Exception {
        String body = mvc.perform(post("/api/auth/login")
                        .contentType(MediaType.APPLICATION_JSON)
                        .content("{\"username\":\"" + user + "\",\"password\":\"demo123\"}"))
                .andExpect(status().isOk())
                .andReturn().getResponse().getContentAsString();
        JsonNode node = json.readTree(body);
        return node.get("token").asText();
    }

    @Test
    void healthIsPublic() throws Exception {
        mvc.perform(get("/api/health")).andExpect(status().isOk()).andExpect(jsonPath("$.status").value("UP"));
    }

    @Test
    void wrongPasswordIsRejected() throws Exception {
        mvc.perform(post("/api/auth/login")
                        .contentType(MediaType.APPLICATION_JSON)
                        .content("{\"username\":\"manager\",\"password\":\"nope\"}"))
                .andExpect(status().isUnauthorized());
    }

    @Test
    void analyticsRequireToken() throws Exception {
        mvc.perform(get("/api/dashboard/summary")).andExpect(status().isUnauthorized());
    }

    @Test
    void managerSeesDashboard() throws Exception {
        String token = login("manager");
        mvc.perform(get("/api/dashboard/summary").header("Authorization", "Bearer " + token))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.sales.revenue").isNumber())
                .andExpect(jsonPath("$.support.open_tickets").isNumber());
        mvc.perform(get("/api/analytics/revenue-trend?region=South").header("Authorization", "Bearer " + token))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.length()").value(24))
                .andExpect(jsonPath("$[0].month").value("2024-10-01"));
    }

    @Test
    void invalidRegionIsBadRequest() throws Exception {
        String token = login("manager");
        mvc.perform(get("/api/analytics/top-products?region=Mars").header("Authorization", "Bearer " + token))
                .andExpect(status().isBadRequest());
    }

    @Test
    void sqlViewIsRoleRestricted() throws Exception {
        mvc.perform(get("/api/analytics/sql/revenue_trend").header("Authorization", "Bearer " + login("manager")))
                .andExpect(status().isForbidden());
        mvc.perform(get("/api/analytics/sql/revenue_trend").header("Authorization", "Bearer " + login("analyst")))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.sql").isString());
    }

    @Test
    void adminEndpointsNeedAdmin() throws Exception {
        mvc.perform(get("/api/admin/users").header("Authorization", "Bearer " + login("analyst")))
                .andExpect(status().isForbidden());
        mvc.perform(get("/api/admin/users").header("Authorization", "Bearer " + login("admin")))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.length()").value(3));
    }
}
