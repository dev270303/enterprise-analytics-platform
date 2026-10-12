package com.eap.auth;

import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.boot.ApplicationArguments;
import org.springframework.boot.ApplicationRunner;
import org.springframework.security.crypto.password.PasswordEncoder;
import org.springframework.stereotype.Component;
import org.springframework.transaction.annotation.Transactional;

/**
 * On start-up:
 *  - one public demo account per role (password DEMO_PASSWORD) so visitors can try every permission level;
 *  - optionally a private owner account (OWNER_USERNAME / OWNER_PASSWORD, role ADMIN, demo=false) that can
 *    commit data loads, upload documents and e-mail reports. Its password is re-synced from the environment
 *    on every start, so rotating it is just changing the variable.
 * Passwords are hashed with BCrypt at runtime, never committed to a migration.
 */
@Component
public class DemoUserSeeder implements ApplicationRunner {

    private static final Logger log = LoggerFactory.getLogger(DemoUserSeeder.class);

    private final AppUserRepository users;
    private final PasswordEncoder passwordEncoder;
    private final String demoPassword;
    private final String ownerUsername;
    private final String ownerPassword;

    public DemoUserSeeder(AppUserRepository users, PasswordEncoder passwordEncoder,
                          @Value("${app.demo-password}") String demoPassword,
                          @Value("${app.owner.username:}") String ownerUsername,
                          @Value("${app.owner.password:}") String ownerPassword) {
        this.users = users;
        this.passwordEncoder = passwordEncoder;
        this.demoPassword = demoPassword;
        this.ownerUsername = ownerUsername == null ? "" : ownerUsername.trim();
        this.ownerPassword = ownerPassword == null ? "" : ownerPassword;
    }

    @Override
    @Transactional
    public void run(ApplicationArguments args) {
        createDemo("admin", "Demo Admin", Role.ADMIN);
        createDemo("analyst", "Demo Analyst", Role.ANALYST);
        createDemo("manager", "Demo Manager", Role.MANAGER);
        createDemo("employee", "Demo Employee", Role.EMPLOYEE);
        syncOwner();
    }

    private void createDemo(String username, String displayName, Role role) {
        if (!users.existsByUsernameIgnoreCase(username)) {
            users.save(new AppUser(username, passwordEncoder.encode(demoPassword), displayName, role, true));
            log.info("Created demo user '{}' ({})", username, role);
        }
    }

    private void syncOwner() {
        if (ownerUsername.isEmpty() || ownerPassword.isEmpty()) {
            return;
        }
        if (ownerPassword.length() < 10) {
            log.warn("OWNER_PASSWORD is shorter than 10 characters; owner account not created");
            return;
        }
        if (java.util.Set.of("admin", "analyst", "manager", "employee").contains(ownerUsername.toLowerCase())) {
            log.warn("OWNER_USERNAME must not be one of the demo usernames; owner account not created");
            return;
        }
        AppUser owner = users.findByUsernameIgnoreCase(ownerUsername).orElse(null);
        if (owner == null) {
            users.save(new AppUser(ownerUsername, passwordEncoder.encode(ownerPassword), "Owner", Role.ADMIN, false));
            log.info("Created owner account '{}'", ownerUsername);
        } else {
            if (!passwordEncoder.matches(ownerPassword, owner.getPasswordHash())) {
                owner.changePassword(passwordEncoder.encode(ownerPassword));
            }
            owner.changeRole(Role.ADMIN);
            owner.markDemo(false);
            users.save(owner);
        }
    }
}
