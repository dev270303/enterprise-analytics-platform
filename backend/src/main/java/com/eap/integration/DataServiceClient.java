package com.eap.integration;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.boot.context.properties.EnableConfigurationProperties;
import org.springframework.core.io.ByteArrayResource;
import org.springframework.http.HttpStatus;
import org.springframework.http.MediaType;
import org.springframework.http.client.JdkClientHttpRequestFactory;
import org.springframework.stereotype.Component;
import org.springframework.util.LinkedMultiValueMap;
import org.springframework.util.MultiValueMap;
import org.springframework.web.client.ResourceAccessException;
import org.springframework.web.client.RestClient;
import org.springframework.web.client.RestClientResponseException;
import org.springframework.web.server.ResponseStatusException;
import org.springframework.web.util.UriBuilder;

import java.net.URI;
import java.net.http.HttpClient;
import java.util.Map;

/**
 * HTTP client for the Python data & AI service. The browser never talks to that service directly:
 * this backend authenticates the user, applies role checks and rate limits, then forwards the call
 * with the shared internal token.
 */
@Component
@EnableConfigurationProperties(DataServiceProperties.class)
public class DataServiceClient {

    private static final Logger log = LoggerFactory.getLogger(DataServiceClient.class);

    private final DataServiceProperties props;
    private final ObjectMapper json;
    private final RestClient http;

    public DataServiceClient(DataServiceProperties props, ObjectMapper json) {
        this.props = props;
        this.json = json;
        HttpClient jdk = HttpClient.newBuilder().connectTimeout(props.connectTimeout()).build();
        JdkClientHttpRequestFactory factory = new JdkClientHttpRequestFactory(jdk);
        factory.setReadTimeout(props.readTimeout());
        this.http = RestClient.builder()
                .requestFactory(factory)
                .baseUrl(props.configured() ? props.url() : "http://localhost")
                .defaultHeader("X-Internal-Token", props.token() == null ? "" : props.token())
                .build();
    }

    public boolean configured() {
        return props.configured();
    }

    public JsonNode get(String path, Map<String, ?> query) {
        return call(() -> http.get().uri(b -> uri(b, path, query)).retrieve().body(JsonNode.class));
    }

    public JsonNode postJson(String path, Map<String, ?> query, Object body) {
        return call(() -> http.post().uri(b -> uri(b, path, query)).contentType(MediaType.APPLICATION_JSON)
                .body(body == null ? Map.of() : body).retrieve().body(JsonNode.class));
    }

    public JsonNode postBytes(String path, Map<String, ?> query, byte[] content, MediaType type) {
        return call(() -> http.post().uri(b -> uri(b, path, query)).contentType(type)
                .body(content).retrieve().body(JsonNode.class));
    }

    public JsonNode postFile(String path, String fileName, byte[] content, Map<String, String> fields) {
        MultiValueMap<String, Object> parts = new LinkedMultiValueMap<>();
        parts.add("file", new ByteArrayResource(content) {
            @Override
            public String getFilename() {
                return fileName;
            }
        });
        fields.forEach(parts::add);
        return call(() -> http.post().uri(path).contentType(MediaType.MULTIPART_FORM_DATA)
                .body(parts).retrieve().body(JsonNode.class));
    }

    public byte[] download(String path) {
        return call(() -> http.get().uri(path).retrieve().body(byte[].class));
    }

    private URI uri(UriBuilder builder, String path, Map<String, ?> query) {
        UriBuilder b = builder.path(path);
        if (query != null) {
            query.forEach((k, v) -> {
                if (v != null) b.queryParam(k, v);
            });
        }
        return b.build();
    }

    private <T> T call(java.util.function.Supplier<T> request) {
        if (!props.configured()) {
            throw new ResponseStatusException(HttpStatus.SERVICE_UNAVAILABLE,
                    "The data & AI service is not configured on this deployment (DATA_SERVICE_URL / INTERNAL_API_TOKEN).");
        }
        try {
            return request.get();
        } catch (RestClientResponseException e) {
            // pass the service's status and message through (e.g. 400 bad file, 429 daily AI budget used up)
            throw new ResponseStatusException(HttpStatus.valueOf(e.getStatusCode().value()), detail(e));
        } catch (ResourceAccessException e) {
            log.warn("data service unreachable: {}", e.getMessage());
            throw new ResponseStatusException(HttpStatus.SERVICE_UNAVAILABLE,
                    "The data & AI service is starting up or unreachable. Free-tier services sleep when idle, so please retry in a minute.");
        }
    }

    private String detail(RestClientResponseException e) {
        try {
            JsonNode node = json.readTree(e.getResponseBodyAsString());
            JsonNode d = node.get("detail");
            if (d != null) return d.isTextual() ? d.asText() : d.toString();
        } catch (Exception ignored) {
            // not JSON
        }
        return "Data service error (" + e.getStatusCode().value() + ")";
    }
}
