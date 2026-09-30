package com.eap;

import org.springframework.boot.SpringApplication;
import org.springframework.boot.autoconfigure.SpringBootApplication;
import org.springframework.cache.annotation.EnableCaching;

@SpringBootApplication
@EnableCaching
public class EapApplication {
    public static void main(String[] args) {
        SpringApplication.run(EapApplication.class, args);
    }
}
