package com.eap.auth;

import jakarta.validation.constraints.NotBlank;
import jakarta.validation.constraints.Size;

public final class AuthDtos {
    private AuthDtos() {
    }

    public record LoginRequest(
            @NotBlank @Size(max = 40) String username,
            @NotBlank @Size(max = 100) String password) {
    }

    public record LoginResponse(String token, long expiresInSeconds, String username, String displayName,
                                Role role, boolean demo) {
    }
}
