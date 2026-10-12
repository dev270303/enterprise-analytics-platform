package com.eap.auth;

import jakarta.persistence.*;

@Entity
@Table(name = "app_users")
public class AppUser {

    @Id
    @GeneratedValue(strategy = GenerationType.IDENTITY)
    private Integer id;

    @Column(nullable = false, unique = true, length = 40)
    private String username;

    @Column(name = "password_hash", nullable = false, length = 100)
    private String passwordHash;

    @Column(name = "display_name", nullable = false, length = 80)
    private String displayName;

    @Enumerated(EnumType.STRING)
    @Column(nullable = false, length = 20)
    private Role role;

    @Column(nullable = false)
    private boolean enabled = true;

    /** Public demo accounts can explore everything but cannot change shared data. */
    @Column(nullable = false)
    private boolean demo = true;

    protected AppUser() {
    }

    public AppUser(String username, String passwordHash, String displayName, Role role, boolean demo) {
        this.username = username;
        this.passwordHash = passwordHash;
        this.displayName = displayName;
        this.role = role;
        this.demo = demo;
    }

    public Integer getId() { return id; }
    public String getUsername() { return username; }
    public String getPasswordHash() { return passwordHash; }
    public String getDisplayName() { return displayName; }
    public Role getRole() { return role; }
    public boolean isEnabled() { return enabled; }
    public boolean isDemo() { return demo; }

    public void changePassword(String newHash) { this.passwordHash = newHash; }
    public void changeRole(Role newRole) { this.role = newRole; }
    public void markDemo(boolean value) { this.demo = value; }
}
