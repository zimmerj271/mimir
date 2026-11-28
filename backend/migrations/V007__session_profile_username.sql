-- T09: resolve only the current owner's authenticated identity for /users/me.
DO $guard$
BEGIN
    IF session_user <> 'mimir_migrator' OR current_user <> 'mimir_migrator' THEN
        RAISE EXCEPTION 'V007 requires the dedicated migration login';
    END IF;
END
$guard$;

SET ROLE mimir_owner;
CREATE FUNCTION archive.session_username(p_identity_id uuid) RETURNS text
LANGUAGE sql STABLE STRICT SECURITY DEFINER
SET search_path = pg_catalog, pg_temp
SET row_security = on
AS $function$
    SELECT i.username FROM auth.identities AS i
    WHERE i.identity_id = p_identity_id
      AND i.user_id = nullif(current_setting('app.current_user_id', true), '')::uuid
$function$;
REVOKE ALL ON FUNCTION archive.session_username(uuid) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION archive.session_username(uuid) TO mimir_api;
RESET ROLE;
