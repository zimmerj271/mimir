-- T10 owner-authorized persistent visibility foundation, not a deletion writer.
-- T09 already applied V007; V001--V007 remain unchanged.
DO $guard$
BEGIN
    IF session_user <> 'mimir_migrator' OR current_user <> 'mimir_migrator' THEN
        RAISE EXCEPTION 'V008 requires the dedicated migration login';
    END IF;
END
$guard$;
SET ROLE mimir_owner;

CREATE TABLE runtime.conversation_deletion_fences (
    user_id uuid NOT NULL,
    root_conversation_id uuid NOT NULL,
    fenced_at timestamptz NOT NULL DEFAULT CURRENT_TIMESTAMP CHECK (isfinite(fenced_at)),
    PRIMARY KEY (user_id, root_conversation_id),
    FOREIGN KEY (user_id, root_conversation_id)
        REFERENCES archive.conversations (user_id, conversation_id) ON DELETE RESTRICT
);

CREATE FUNCTION runtime.reject_conversation_fence_update() RETURNS trigger
LANGUAGE plpgsql SET search_path = pg_catalog, pg_temp
AS $function$
BEGIN
    RAISE EXCEPTION 'established conversation fences are immutable' USING ERRCODE = '23514';
END
$function$;
CREATE TRIGGER conversation_fence_update_guard BEFORE UPDATE
    ON runtime.conversation_deletion_fences FOR EACH ROW
    EXECUTE FUNCTION runtime.reject_conversation_fence_update();

ALTER TABLE runtime.conversation_deletion_fences ENABLE ROW LEVEL SECURITY;
ALTER TABLE runtime.conversation_deletion_fences FORCE ROW LEVEL SECURITY;
CREATE POLICY conversation_fences_tenant ON runtime.conversation_deletion_fences
    USING (user_id = nullif(current_setting('app.current_user_id', true), '')::uuid)
    WITH CHECK (user_id = nullif(current_setting('app.current_user_id', true), '')::uuid);
GRANT SELECT ON runtime.conversation_deletion_fences TO mimir_api, mimir_worker;

-- Invoker rights retain RLS on every source, including recursive descendants.
-- The composite PK indexes owner/root probes; V004 indexes owner/parent traversal.
CREATE VIEW archive.visible_conversations WITH (security_invoker = true) AS
    WITH RECURSIVE fenced(user_id, conversation_id) AS (
        SELECT user_id, root_conversation_id FROM runtime.conversation_deletion_fences
            WHERE user_id = nullif(current_setting('app.current_user_id', true), '')::uuid
        UNION
        SELECT c.user_id, c.conversation_id FROM archive.conversations c
            JOIN fenced f ON c.user_id = f.user_id
                AND c.parent_conversation_id = f.conversation_id
    )
    SELECT c.* FROM archive.conversations c
        JOIN archive.users u ON u.user_id = c.user_id AND u.status = 'active'
        WHERE c.user_id = nullif(current_setting('app.current_user_id', true), '')::uuid
            AND NOT EXISTS (SELECT FROM runtime.deletion_controls d
                WHERE d.user_id = c.user_id AND d.fenced_at IS NOT NULL)
            AND NOT EXISTS (SELECT FROM fenced f
                WHERE f.user_id = c.user_id AND f.conversation_id = c.conversation_id);
GRANT SELECT ON archive.visible_conversations TO mimir_api, mimir_worker;
-- No runtime INSERT/UPDATE/DELETE/TRUNCATE or privileged fence writer.
-- T36 must coordinate future establishment/cleanup with all concurrent writers.
RESET ROLE;
