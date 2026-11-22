-- T08, explicitly authorized by the owner: new restricted mutation interfaces.
-- V001–V005 remain immutable. Runtime roles receive EXECUTE, never table DML.
DO $guard$
BEGIN
    IF session_user <> 'mimir_migrator' OR current_user <> 'mimir_migrator' THEN
        RAISE EXCEPTION 'V006 requires the dedicated migration login';
    END IF;
END
$guard$;

SET ROLE mimir_owner;

CREATE FUNCTION auth.provision_user(
    p_user_id uuid, p_identity_id uuid, p_username text,
    p_password_hash text, p_display_name text
) RETURNS void
LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog, pg_temp
SET row_security = on
AS $function$
DECLARE
    previous_actor text := current_setting('app.current_user_id', true);
BEGIN
    -- Only the separately credentialed local administrator may choose a new owner.
    IF p_user_id IS NULL OR p_identity_id IS NULL
        OR p_username IS NULL OR length(p_username) = 0
        OR p_password_hash IS NULL OR length(p_password_hash) > 1024
        OR p_password_hash !~ '^\$argon2id\$v=19\$m=[1-9][0-9]*,t=[1-9][0-9]*,p=[1-9][0-9]*\$[A-Za-z0-9+/]+\$[A-Za-z0-9+/]+$'
    THEN
        -- Avoid constraint diagnostics containing a supplied verifier/row.
        RAISE EXCEPTION 'invalid provisioning parameters' USING ERRCODE = '22023';
    END IF;
    PERFORM set_config('app.current_user_id', p_user_id::text, true);
    INSERT INTO archive.users (user_id, display_name, status)
        VALUES (p_user_id, p_display_name, 'active');
    INSERT INTO auth.identities (identity_id, user_id, username, password_hash)
        VALUES (p_identity_id, p_user_id, p_username, p_password_hash);
    PERFORM set_config('app.current_user_id', coalesce(previous_actor, ''), true);
END
$function$;

CREATE FUNCTION auth.disable_account(p_user_id uuid) RETURNS boolean
LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog, pg_temp
SET row_security = on
AS $function$
DECLARE
    previous_actor text := current_setting('app.current_user_id', true);
BEGIN
    PERFORM set_config('app.current_user_id', p_user_id::text, true);
    -- Every mutation locks account before session. A committed disable wins over
    -- any subsequently issued/renewed session; no routine can reactivate accounts.
    PERFORM 1 FROM archive.users WHERE user_id = p_user_id FOR UPDATE;
    IF NOT FOUND THEN
        PERFORM set_config('app.current_user_id', coalesce(previous_actor, ''), true);
        RETURN false;
    END IF;
    UPDATE archive.users SET status = 'disabled' WHERE user_id = p_user_id;
    UPDATE auth.sessions SET revoked_at = greatest(clock_timestamp(), created_at)
        WHERE user_id = p_user_id AND revoked_at IS NULL;
    PERFORM set_config('app.current_user_id', coalesce(previous_actor, ''), true);
    RETURN true;
END
$function$;

CREATE FUNCTION auth.issue_session(
    p_identity_id uuid, p_verified_hash text, p_token_hash bytea, p_client_type text
) RETURNS TABLE (
    user_id uuid, identity_id uuid, client_type text,
    last_used_at timestamptz, expires_at timestamptz,
    absolute_expires_at timestamptz, revoked_at timestamptz, account_status text
)
LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog, pg_temp
SET row_security = on
SET timezone = 'UTC'
AS $function$
DECLARE
    resolved_user uuid;
    issued_at timestamptz;
    previous_actor text := current_setting('app.current_user_id', true);
BEGIN
    SELECT i.user_id INTO resolved_user FROM auth.identities i
        WHERE i.identity_id = p_identity_id;
    IF NOT FOUND THEN RETURN; END IF;
    PERFORM set_config('app.current_user_id', resolved_user::text, true);
    PERFORM 1 FROM archive.users u WHERE u.user_id = resolved_user FOR UPDATE;
    -- The trusted authentication service supplies the verifier it actually
    -- checked. Re-read verifier/status after the account lock: a prior lookup
    -- alone cannot authorize issuance.
    IF NOT EXISTS (
        SELECT FROM auth.identities i JOIN archive.users u USING (user_id)
        WHERE i.identity_id = p_identity_id AND i.password_hash = p_verified_hash
            AND u.status = 'active'
    ) THEN
        PERFORM set_config('app.current_user_id', coalesce(previous_actor, ''), true);
        RETURN;
    END IF;
    issued_at := clock_timestamp();
    IF p_token_hash IS NULL OR octet_length(p_token_hash) <> 32
        OR p_client_type IS NULL OR p_client_type NOT IN ('cli', 'web') THEN
        RAISE EXCEPTION 'invalid session parameters' USING ERRCODE = '22023';
    END IF;
    INSERT INTO auth.sessions (
        token_hash, user_id, identity_id, client_type, created_at, last_used_at,
        expires_at, absolute_expires_at
    ) VALUES (
        p_token_hash, resolved_user, p_identity_id, p_client_type, issued_at, issued_at,
        issued_at + interval '30 days', issued_at + interval '90 days'
    );
    RETURN QUERY SELECT * FROM auth.lookup_session(p_token_hash);
    PERFORM set_config('app.current_user_id', coalesce(previous_actor, ''), true);
END
$function$;

CREATE FUNCTION auth.renew_session(p_token_hash bytea)
RETURNS TABLE (
    user_id uuid, identity_id uuid, client_type text,
    last_used_at timestamptz, expires_at timestamptz,
    absolute_expires_at timestamptz, revoked_at timestamptz, account_status text
)
LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog, pg_temp
SET row_security = on
SET timezone = 'UTC'
AS $function$
DECLARE
    resolved_user uuid;
    activity_at timestamptz;
    previous_actor text := current_setting('app.current_user_id', true);
BEGIN
    SELECT s.user_id INTO resolved_user FROM auth.sessions s
        WHERE s.token_hash = p_token_hash;
    IF NOT FOUND THEN RETURN; END IF;
    PERFORM set_config('app.current_user_id', resolved_user::text, true);
    PERFORM 1 FROM archive.users u WHERE u.user_id = resolved_user FOR UPDATE;
    -- Acquire both locks before observing wall time: waiting never extends an
    -- already expired session. Same lock order as issuance/disable/revocation.
    PERFORM 1 FROM auth.sessions s WHERE s.token_hash = p_token_hash FOR UPDATE;
    activity_at := clock_timestamp();
    UPDATE auth.sessions s SET
        last_used_at = greatest(s.last_used_at, activity_at),
        expires_at = greatest(s.expires_at,
            least(activity_at + interval '30 days', s.absolute_expires_at))
        WHERE s.token_hash = p_token_hash AND s.revoked_at IS NULL
            AND activity_at < s.expires_at AND activity_at < s.absolute_expires_at
            AND EXISTS (SELECT FROM archive.users u
                WHERE u.user_id = resolved_user AND u.status = 'active');
    IF FOUND THEN
        RETURN QUERY SELECT * FROM auth.lookup_session(p_token_hash);
    END IF;
    PERFORM set_config('app.current_user_id', coalesce(previous_actor, ''), true);
END
$function$;

CREATE FUNCTION auth.revoke_session(p_token_hash bytea) RETURNS boolean
LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog, pg_temp
SET row_security = on
AS $function$
DECLARE
    resolved_user uuid;
    previous_actor text := current_setting('app.current_user_id', true);
BEGIN
    SELECT s.user_id INTO resolved_user FROM auth.sessions s
        WHERE s.token_hash = p_token_hash;
    IF NOT FOUND THEN RETURN false; END IF;
    PERFORM set_config('app.current_user_id', resolved_user::text, true);
    PERFORM 1 FROM archive.users u WHERE u.user_id = resolved_user FOR UPDATE;
    UPDATE auth.sessions s SET revoked_at = greatest(clock_timestamp(), s.created_at)
        WHERE s.token_hash = p_token_hash AND s.revoked_at IS NULL;
    PERFORM set_config('app.current_user_id', coalesce(previous_actor, ''), true);
    RETURN true;
END
$function$;

REVOKE ALL ON FUNCTION auth.provision_user(uuid, uuid, text, text, text),
    auth.disable_account(uuid), auth.issue_session(uuid, text, bytea, text),
    auth.renew_session(bytea), auth.revoke_session(bytea) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION auth.provision_user(uuid, uuid, text, text, text),
    auth.disable_account(uuid) TO mimir_maintenance;
GRANT EXECUTE ON FUNCTION auth.issue_session(uuid, text, bytea, text),
    auth.renew_session(bytea), auth.revoke_session(bytea) TO mimir_auth;

RESET ROLE;
