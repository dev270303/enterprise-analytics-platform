package com.eap.auth;

import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.boot.ApplicationArguments;
import org.springframework.boot.ApplicationRunner;
import org.springframework.security.crypto.password.PasswordEncoder;
import org.springframework.stereotype.Component;

/**
 * Creates one demo account per role on first start, so visitors can try each permission level.
 * Passwords are hashed with BCrypt at runtime rather than committed to a migration.
 */
@Component
public class DemoUserSeeder implements ApplicationRunner {

    private static final Logger log = LoggerFactory.getLogger(DemoUserSeeder.class);

    private final AppUserRepository users;
    private final PasswordEncoder passwordEncoder;
    private final String demoPassword;

    public DemoUserSeeder(AppUserRepository users, PasswordEncoder passwordEncoder,
                          @Value("${app.demo-password}") String demoPassword) {
        this.users = users;
        this.passwordEncoder = passwordEncoder;
        this.demoPassword = demoPassword;
    }

    @Override
    public void run(ApplicationArguments args) {
        create("admin", "Demo Admin", Role.ADMIN);
        create("analyst", "Demo Analyst", Role.ANALYST);
        create("manager", "Demo Manager", Role.MANAGER);
    }

    private void create(String username, String displayName, Role role) {
        if (!users.existsByUsernameIgnoreCase(username)) {
            users.save(new AppUser(username, passwordEncoder.encode(demoPassword), displayName, role));
            log.info("Created demo user '{}' ({})", username, role);
        }
    }
}
