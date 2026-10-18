-- Administrative, non-versioned cluster provisioning; never application DDL.
-- Serialize concurrent provisioning, including CREATE DATABASE outside a txn.
SELECT pg_advisory_lock(1877347689, 3);
-- This is a dedicated Mimir cluster; ordinary logins must not create temporary
-- objects by connecting to the maintenance/template databases.
REVOKE ALL ON DATABASE postgres, template1 FROM PUBLIC;
SELECT 'CREATE ROLE mimir_migrator LOGIN NOSUPERUSER NOCREATEDB CREATEROLE NOINHERIT NOREPLICATION NOBYPASSRLS'
WHERE NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'mimir_migrator')
\gexec
DO $guard$
BEGIN
    IF NOT EXISTS (
        SELECT FROM pg_roles WHERE rolname = 'mimir_migrator'
          AND rolcanlogin AND rolcreaterole AND NOT rolsuper AND NOT rolcreatedb
          AND NOT rolinherit AND NOT rolreplication AND NOT rolbypassrls
    ) THEN
        RAISE EXCEPTION 'Existing migration role is incompatible; operator review required';
    END IF;
END
$guard$;
SELECT 'CREATE DATABASE mimir OWNER mimir_migrator'
WHERE NOT EXISTS (SELECT FROM pg_database WHERE datname = 'mimir')
\gexec
DO $guard$
BEGIN
    IF NOT EXISTS (SELECT FROM pg_database WHERE datname = 'mimir'
                   AND datdba = 'mimir_migrator'::regrole) THEN
        RAISE EXCEPTION 'Existing database owner is incompatible; operator review required';
    END IF;
END
$guard$;
-- Only the administrator creates databases. CREATE is needed for V001 schemas
-- and GRANT OPTION lets the migrator temporarily delegate it to mimir_owner.
GRANT CONNECT, CREATE ON DATABASE mimir TO mimir_migrator WITH GRANT OPTION;
\connect mimir
GRANT USAGE, CREATE ON SCHEMA public TO mimir_migrator;
