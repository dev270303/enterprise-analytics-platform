package com.eap.auth;

public enum Role {
    ADMIN,     // everything: data uploads, user management, cache, scheduled jobs
    ANALYST,   // dashboards + SQL behind every chart + AI analyst + data-quality reports
    MANAGER,   // dashboards + AI analyst + insights
    EMPLOYEE   // operational support data only (no revenue figures)
}
