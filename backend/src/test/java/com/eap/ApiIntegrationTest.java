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
                .andExpect(jsonPath("$.length()").value(org.hamcrest.Matchers.greaterThanOrEqualTo(4)));
    }

    @Test
    void employeeSeesSupportButNotRevenue() throws Exception {
        String token = login("employee");
        mvc.perform(get("/api/analytics/support/summary").header("Authorization", "Bearer " + token))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.open_tickets").isNumber());
        mvc.perform(get("/api/analytics/support/sla").header("Authorization", "Bearer " + token))
                .andExpect(status().isOk());
        mvc.perform(get("/api/dashboard/summary").header("Authorization", "Bearer " + token))
                .andExpect(status().isForbidden());
        mvc.perform(post("/api/ai/ask").header("Authorization", "Bearer " + token)
                        .contentType(MediaType.APPLICATION_JSON).content("{\"question\":\"revenue?\"}"))
                .andExpect(status().isForbidden());
    }

    @Test
    void loginTellsTheClientItIsADemoAccount() throws Exception {
        mvc.perform(post("/api/auth/login").contentType(MediaType.APPLICATION_JSON)
                        .content("{\"username\":\"analyst\",\"password\":\"demo123\"}"))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.demo").value(true))
                .andExpect(jsonPath("$.role").value("ANALYST"));
    }

    @Test
    void recordListsArePaginated() throws Exception {
        String token = login("analyst");
        mvc.perform(get("/api/orders?status=delivered&region=South&size=5").header("Authorization", "Bearer " + token))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.content.length()").value(5))
                .andExpect(jsonPath("$.content[0].region").value("South"))
                .andExpect(jsonPath("$.totalElements").isNumber());
        mvc.perform(get("/api/customers?segment=Enterprise&size=3").header("Authorization", "Bearer " + token))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.content[0].segment").value("Enterprise"));
    }

    @Test
    void phaseTwoToFourEndpointsWork() throws Exception {
        String token = login("analyst");
        mvc.perform(get("/api/data/runs").header("Authorization", "Bearer " + token)).andExpect(status().isOk());
        mvc.perform(get("/api/insights").header("Authorization", "Bearer " + token)).andExpect(status().isOk());
        mvc.perform(get("/api/analytics/rfm-segments").header("Authorization", "Bearer " + token))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.segments").isArray());
        // no data service configured in this test context -> friendly 503, not a crash
        mvc.perform(post("/api/ai/ask").header("Authorization", "Bearer " + token)
                        .contentType(MediaType.APPLICATION_JSON).content("{\"question\":\"Revenue by region\"}"))
                .andExpect(status().isServiceUnavailable());
        mvc.perform(get("/api/ai/status").header("Authorization", "Bearer " + token))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.configured").value(false));
        // analysts can read data-quality reports but not load data
        mvc.perform(post("/api/data/samples/run").header("Authorization", "Bearer " + token))
                .andExpect(status().isForbidden());
    }
}
