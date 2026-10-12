-- =====================================================================
-- Phase 4: fourth role and demo/owner separation
--  * EMPLOYEE: operational (support) data only, no revenue figures
--  * demo flag: public demo accounts can explore everything but cannot
--    change shared data (ETL runs are forced to dry-run, no document uploads)
-- =====================================================================
ALTER TABLE app_users DROP CONSTRAINT IF EXISTS app_users_role_check;
ALTER TABLE app_users ADD CONSTRAINT app_users_role_check
    CHECK (role IN ('ADMIN','ANALYST','MANAGER','EMPLOYEE'));

ALTER TABLE app_users ADD COLUMN demo BOOLEAN NOT NULL DEFAULT TRUE;
