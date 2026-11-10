-- T04: database foundation only. T08 owns authentication/provisioning services.
DO $guard$
BEGIN
    IF session_user <> 'mimir_migrator' OR current_user <> 'mimir_migrator' THEN
        RAISE EXCEPTION 'V002 requires the dedicated migration login';
    END IF;
END
$guard$;

SET ROLE mimir_owner;

CREATE TABLE archive.users (
    user_id uuid PRIMARY KEY,
    display_name text,
    status text NOT NULL CHECK (status IN ('active', 'disabled')),
    created_at timestamptz NOT NULL DEFAULT CURRENT_TIMESTAMP
        CHECK (isfinite(created_at))
);

CREATE TABLE auth.identities (
    identity_id uuid PRIMARY KEY,
    user_id uuid NOT NULL REFERENCES archive.users (user_id),
    -- Explicit deterministic collation: exact case-sensitive login identity.
    username text COLLATE "C" NOT NULL UNIQUE CHECK (length(username) > 0),
    -- PHC-encoded Argon2id v19 verifier, including salt and work parameters.
    -- This storage-shape check does not replace T08's Argon2 verification/policy.
    password_hash text NOT NULL CHECK (
        password_hash ~ '^\$argon2id\$v=19\$m=[1-9][0-9]*,t=[1-9][0-9]*,p=[1-9][0-9]*\$[A-Za-z0-9+/]+\$[A-Za-z0-9+/]+$'
    ),
    created_at timestamptz NOT NULL DEFAULT CURRENT_TIMESTAMP
        CHECK (isfinite(created_at)),
    UNIQUE (user_id, identity_id)
);

CREATE TABLE auth.sessions (
    -- Only the SHA-256 credential hash is persisted; no plaintext/refresh token.
    token_hash bytea PRIMARY KEY CHECK (octet_length(token_hash) = 32),
    user_id uuid NOT NULL,
    identity_id uuid NOT NULL,
    client_type text NOT NULL CHECK (client_type IN ('cli', 'web')),
    created_at timestamptz NOT NULL DEFAULT CURRENT_TIMESTAMP,
    last_used_at timestamptz NOT NULL DEFAULT CURRENT_TIMESTAMP,
    expires_at timestamptz NOT NULL,
    absolute_expires_at timestamptz NOT NULL,
    revoked_at timestamptz,
    UNIQUE (user_id, token_hash),
    FOREIGN KEY (user_id, identity_id)
        REFERENCES auth.identities (user_id, identity_id),
    CHECK (isfinite(created_at) AND isfinite(last_used_at)
        AND isfinite(expires_at) AND isfinite(absolute_expires_at)),
    CHECK (created_at <= last_used_at AND last_used_at < expires_at
        AND expires_at <= absolute_expires_at),
    CHECK (revoked_at IS NULL OR
        (isfinite(revoked_at) AND revoked_at >= created_at))
);

CREATE INDEX sessions_owner_identity ON auth.sessions (user_id, identity_id);

-- Protect stable ownership and session identity even in administrative updates.
CREATE FUNCTION archive.guard_user_update() RETURNS trigger
LANGUAGE plpgsql
SET search_path = pg_catalog, pg_temp
AS $function$
BEGIN
    IF NEW.user_id IS DISTINCT FROM OLD.user_id
        OR NEW.created_at IS DISTINCT FROM OLD.created_at THEN
        RAISE EXCEPTION 'user identity and creation fields are immutable'
            USING ERRCODE = '23514';
    END IF;
    RETURN NEW;
END
$function$;

CREATE TRIGGER user_update_guard BEFORE UPDATE ON archive.users
    FOR EACH ROW EXECUTE FUNCTION archive.guard_user_update();

CREATE FUNCTION auth.guard_identity_update() RETURNS trigger
LANGUAGE plpgsql
SET search_path = pg_catalog, pg_temp
AS $function$
BEGIN
    IF NEW.user_id IS DISTINCT FROM OLD.user_id
        OR NEW.identity_id IS DISTINCT FROM OLD.identity_id
        OR NEW.created_at IS DISTINCT FROM OLD.created_at THEN
        RAISE EXCEPTION 'identity ownership and creation fields are immutable'
            USING ERRCODE = '23514';
    END IF;
    RETURN NEW;
END
$function$;

CREATE TRIGGER identity_update_guard BEFORE UPDATE ON auth.identities
    FOR EACH ROW EXECUTE FUNCTION auth.guard_identity_update();

CREATE FUNCTION auth.guard_session_update() RETURNS trigger
LANGUAGE plpgsql
SET search_path = pg_catalog, pg_temp
AS $function$
BEGIN
    IF NEW.token_hash IS DISTINCT FROM OLD.token_hash
        OR NEW.user_id IS DISTINCT FROM OLD.user_id
        OR NEW.identity_id IS DISTINCT FROM OLD.identity_id
        OR NEW.client_type IS DISTINCT FROM OLD.client_type
        OR NEW.created_at IS DISTINCT FROM OLD.created_at
        OR NEW.absolute_expires_at IS DISTINCT FROM OLD.absolute_expires_at THEN
        RAISE EXCEPTION 'session identity and absolute expiration are immutable'
            USING ERRCODE = '23514';
    END IF;
    IF OLD.revoked_at IS NOT NULL
        AND NEW.revoked_at IS DISTINCT FROM OLD.revoked_at THEN
        RAISE EXCEPTION 'session revocation is irreversible'
            USING ERRCODE = '23514';
    END IF;
    IF NEW.expires_at < OLD.expires_at OR NEW.last_used_at < OLD.last_used_at THEN
        RAISE EXCEPTION 'session activity fields cannot move backwards'
            USING ERRCODE = '23514';
    END IF;
    IF (NEW.expires_at IS DISTINCT FROM OLD.expires_at
        OR NEW.last_used_at IS DISTINCT FROM OLD.last_used_at)
        AND (OLD.revoked_at IS NOT NULL OR OLD.expires_at <= clock_timestamp()
            OR OLD.absolute_expires_at <= clock_timestamp()) THEN
        RAISE EXCEPTION 'revoked or expired sessions cannot be renewed'
            USING ERRCODE = '23514';
    END IF;
    RETURN NEW;
END
$function$;

CREATE TRIGGER session_update_guard BEFORE UPDATE ON auth.sessions
    FOR EACH ROW EXECUTE FUNCTION auth.guard_session_update();

ALTER TABLE archive.users ENABLE ROW LEVEL SECURITY;
ALTER TABLE archive.users FORCE ROW LEVEL SECURITY;
ALTER TABLE auth.identities ENABLE ROW LEVEL SECURITY;
ALTER TABLE auth.identities FORCE ROW LEVEL SECURITY;
ALTER TABLE auth.sessions ENABLE ROW LEVEL SECURITY;
ALTER TABLE auth.sessions FORCE ROW LEVEL SECURITY;

CREATE POLICY users_tenant ON archive.users
    USING (user_id = nullif(current_setting('app.current_user_id', true), '')::uuid)
    WITH CHECK (user_id = nullif(current_setting('app.current_user_id', true), '')::uuid);
CREATE POLICY identities_tenant ON auth.identities
    USING (user_id = nullif(current_setting('app.current_user_id', true), '')::uuid)
    WITH CHECK (user_id = nullif(current_setting('app.current_user_id', true), '')::uuid);
CREATE POLICY sessions_tenant ON auth.sessions
    USING (user_id = nullif(current_setting('app.current_user_id', true), '')::uuid)
    WITH CHECK (user_id = nullif(current_setting('app.current_user_id', true), '')::uuid);

-- FORCE RLS also applies to the NOLOGIN owner. Its explicit SELECT-only policies
-- support the two fixed lookup bodies below; they grant no caller table access
-- or cross-tenant writes. Normal logins cannot SET ROLE mimir_owner (V001).
CREATE POLICY users_auth_lookup ON archive.users FOR SELECT
    TO mimir_owner USING (true);
CREATE POLICY identities_auth_lookup ON auth.identities FOR SELECT
    TO mimir_owner USING (true);
CREATE POLICY sessions_auth_lookup ON auth.sessions FOR SELECT
    TO mimir_owner USING (true);

CREATE FUNCTION auth.lookup_identity(p_username text)
RETURNS TABLE (
    user_id uuid, identity_id uuid, password_hash text, account_status text
)
LANGUAGE sql STABLE STRICT SECURITY DEFINER
SET search_path = pg_catalog, pg_temp
SET row_security = on
AS $function$
    SELECT i.user_id, i.identity_id, i.password_hash, u.status
    FROM auth.identities AS i
    JOIN archive.users AS u ON u.user_id = i.user_id
    WHERE i.username = p_username COLLATE "C"
$function$;

CREATE FUNCTION auth.lookup_session(p_token_hash bytea)
RETURNS TABLE (
    user_id uuid, identity_id uuid, client_type text,
    last_used_at timestamptz, expires_at timestamptz,
    absolute_expires_at timestamptz, revoked_at timestamptz, account_status text
)
LANGUAGE sql STABLE STRICT SECURITY DEFINER
SET search_path = pg_catalog, pg_temp
SET row_security = on
AS $function$
    SELECT s.user_id, s.identity_id, s.client_type,
        s.last_used_at, s.expires_at, s.absolute_expires_at, s.revoked_at, u.status
    FROM auth.sessions AS s
    JOIN archive.users AS u ON u.user_id = s.user_id
    WHERE octet_length(p_token_hash) = 32 AND s.token_hash = p_token_hash
$function$;

-- Invalid/expired/disabled lookup results are authentication input, not proof of
-- authentication. T08 must validate them before setting transaction-local actor
-- context. Neither routine changes that context or accepts a caller user ID.
REVOKE ALL ON FUNCTION auth.lookup_identity(text) FROM PUBLIC;
REVOKE ALL ON FUNCTION auth.lookup_session(bytea) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION auth.lookup_identity(text), auth.lookup_session(bytea)
    TO mimir_auth;

GRANT SELECT ON archive.users TO mimir_api, mimir_worker;
GRANT UPDATE (display_name) ON archive.users TO mimir_api;
-- No direct credential table access, provisioning/session mutation grant or
-- unrestricted maintenance capability is conferred on runtime logins by T04.
RESET ROLE;
