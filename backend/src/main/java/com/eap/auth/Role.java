package com.eap.auth;

public enum Role {
    ADMIN,    // everything, including user management
    ANALYST,  // dashboards + the SQL behind every chart
    MANAGER   // dashboards only
}
