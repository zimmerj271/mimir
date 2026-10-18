-- T03 only: no application tables, tenant policies or provider-owned memory.
-- Provisioning supplies the dedicated database and migration login first.
DO $guard$
BEGIN
    IF session_user <> 'mimir_migrator' OR current_user <> 'mimir_migrator' THEN
        RAISE EXCEPTION 'V001 requires the dedicated migration login';
    END IF;
END
$guard$;

CREATE ROLE mimir_owner NOLOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE
    NOINHERIT NOREPLICATION NOBYPASSRLS;
CREATE ROLE mimir_api LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE
    NOINHERIT NOREPLICATION NOBYPASSRLS;
CREATE ROLE mimir_worker LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE
    NOINHERIT NOREPLICATION NOBYPASSRLS;
CREATE ROLE mimir_auth LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE
    NOINHERIT NOREPLICATION NOBYPASSRLS;
CREATE ROLE mimir_maintenance LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE
    NOINHERIT NOREPLICATION NOBYPASSRLS;

-- Only the privileged migration login can assume ownership. PostgreSQL grants
-- the role creator ADMIN, but SET/INHERIT must be chosen explicitly on PG18.
GRANT mimir_owner TO mimir_migrator WITH INHERIT FALSE, SET TRUE;
GRANT CREATE ON DATABASE mimir TO mimir_owner;
SET ROLE mimir_owner;
CREATE SCHEMA archive AUTHORIZATION mimir_owner;
CREATE SCHEMA auth AUTHORIZATION mimir_owner;
CREATE SCHEMA runtime AUTHORIZATION mimir_owner;

-- Future migrations must SET ROLE mimir_owner before creating domain objects.
-- Object-specific grants and FORCE RLS belong with those later tables/routines.
ALTER DEFAULT PRIVILEGES
    REVOKE EXECUTE ON FUNCTIONS FROM PUBLIC;
ALTER DEFAULT PRIVILEGES IN SCHEMA archive, auth, runtime
    REVOKE ALL ON TABLES FROM PUBLIC;
ALTER DEFAULT PRIVILEGES IN SCHEMA archive, auth, runtime
    REVOKE ALL ON SEQUENCES FROM PUBLIC;
GRANT USAGE ON SCHEMA archive, runtime TO mimir_api, mimir_worker;
GRANT USAGE ON SCHEMA archive, auth TO mimir_auth;
GRANT USAGE ON SCHEMA archive, auth, runtime TO mimir_maintenance;
RESET ROLE;
REVOKE CREATE ON DATABASE mimir FROM mimir_owner;

REVOKE ALL ON DATABASE mimir FROM PUBLIC;
GRANT CONNECT ON DATABASE mimir TO mimir_migrator, mimir_api, mimir_worker,
    mimir_auth, mimir_maintenance;
REVOKE ALL ON SCHEMA public FROM PUBLIC;
-- public contains only migrator-owned Flyway history, never application data.
ALTER ROLE mimir_migrator SET search_path = pg_catalog;
ALTER ROLE mimir_api SET search_path = pg_catalog;
ALTER ROLE mimir_worker SET search_path = pg_catalog;
ALTER ROLE mimir_auth SET search_path = pg_catalog;
ALTER ROLE mimir_maintenance SET search_path = pg_catalog;
