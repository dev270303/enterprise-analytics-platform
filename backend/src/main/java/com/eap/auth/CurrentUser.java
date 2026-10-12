package com.eap.auth;

import org.springframework.security.oauth2.jwt.Jwt;

/** Facts about the caller taken from the verified JWT. */
public record CurrentUser(String username, Role role, boolean demo) {

    public static CurrentUser from(Jwt jwt) {
        Boolean demo = jwt.getClaimAsBoolean("demo");
        Role role;
        try {
            role = Role.valueOf(jwt.getClaimAsString("role"));
        } catch (RuntimeException e) {
            role = Role.EMPLOYEE;   // least privilege if the claim is missing or unknown
        }
        // tokens issued before the demo claim existed are treated as demo (safe default)
        return new CurrentUser(jwt.getSubject(), role, demo == null || demo);
    }

    /** The private owner account: an ADMIN that is not a public demo login. */
    public boolean isOwner() {
        return role == Role.ADMIN && !demo;
    }
}
