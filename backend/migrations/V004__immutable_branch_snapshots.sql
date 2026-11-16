-- T06 storage only; T10 owns branch services and T11 owns HTTP operations.
DO $guard$
BEGIN
    IF session_user <> 'mimir_migrator' OR current_user <> 'mimir_migrator' THEN
        RAISE EXCEPTION 'V004 requires the dedicated migration login';
    END IF;
END
$guard$;

SET ROLE mimir_owner;

ALTER TABLE archive.conversations
    ADD COLUMN parent_conversation_id uuid,
    ADD COLUMN branch_point_revision_id uuid,
    ADD COLUMN branch_mode text CHECK (branch_mode IN ('after_revision', 'replace_message')),
    ADD COLUMN inherited_revision_count bigint NOT NULL DEFAULT 0
        CHECK (inherited_revision_count >= 0),
    ADD CONSTRAINT branch_link_shape CHECK (
        (parent_conversation_id IS NULL AND branch_point_revision_id IS NULL
            AND branch_mode IS NULL AND inherited_revision_count = 0)
        OR (parent_conversation_id IS NOT NULL AND branch_point_revision_id IS NOT NULL
            AND branch_mode IS NOT NULL AND parent_conversation_id <> conversation_id)),
    ADD CONSTRAINT branch_parent_owner_fk FOREIGN KEY (user_id, parent_conversation_id)
        REFERENCES archive.conversations (user_id, conversation_id);

ALTER TABLE archive.message_revisions ADD CONSTRAINT revision_owner_identity
    UNIQUE (user_id, revision_id);
ALTER TABLE archive.conversations ADD CONSTRAINT branch_point_owner_fk
    FOREIGN KEY (user_id, branch_point_revision_id)
        REFERENCES archive.message_revisions (user_id, revision_id);
CREATE INDEX conversations_owner_parent
    ON archive.conversations (user_id, parent_conversation_id);

CREATE TABLE archive.conversation_branch_revisions (
    user_id uuid NOT NULL,
    conversation_id uuid NOT NULL,
    ordinal bigint NOT NULL CHECK (ordinal > 0),
    message_id uuid NOT NULL,
    revision_id uuid NOT NULL,
    PRIMARY KEY (user_id, conversation_id, ordinal),
    UNIQUE (user_id, conversation_id, revision_id),
    FOREIGN KEY (user_id, conversation_id)
        REFERENCES archive.conversations (user_id, conversation_id),
    FOREIGN KEY (user_id, message_id, revision_id)
        REFERENCES archive.message_revisions (user_id, message_id, revision_id)
);
CREATE INDEX branch_revisions_source
    ON archive.conversation_branch_revisions (user_id, message_id, revision_id);

-- Local message ordinals remain dense from one (V003). Visible ordinals prepend
-- the flattened snapshot. Invoker rights preserve runtime tenant RLS.
CREATE VIEW archive.conversation_visible_revisions WITH (security_invoker = true) AS
    SELECT user_id, conversation_id, ordinal, message_id, revision_id
        FROM archive.conversation_branch_revisions
    UNION ALL
    SELECT m.user_id, m.conversation_id, c.inherited_revision_count + m.ordinal,
        m.message_id, m.current_revision_id
        FROM archive.messages m JOIN archive.conversations c
            USING (user_id, conversation_id);

CREATE FUNCTION archive.prepare_branch_link() RETURNS trigger
LANGUAGE plpgsql SET search_path = pg_catalog, pg_temp
AS $function$
DECLARE
    parent_project uuid;
    point_ordinal bigint;
BEGIN
    IF NEW.parent_conversation_id IS NULL THEN
        RETURN NEW;
    END IF;
    -- This lock shares V003's append/metadata lock and lasts through snapshot
    -- validation at commit. Read history only after acquiring it (READ COMMITTED).
    SELECT project_id INTO parent_project FROM archive.conversations
        WHERE user_id = NEW.user_id AND conversation_id = NEW.parent_conversation_id
        FOR UPDATE;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'branch requires an owned parent' USING ERRCODE = '23503';
    END IF;
    IF NEW.project_id IS DISTINCT FROM parent_project THEN
        RAISE EXCEPTION 'branch must inherit the parent project at creation'
            USING ERRCODE = '23514';
    END IF;
    SELECT ordinal INTO point_ordinal FROM archive.conversation_visible_revisions
        WHERE user_id = NEW.user_id AND conversation_id = NEW.parent_conversation_id
            AND revision_id = NEW.branch_point_revision_id;
    IF point_ordinal IS NULL THEN
        RAISE EXCEPTION 'branch point must be an exact visible parent revision'
            USING ERRCODE = '23514';
    END IF;
    -- The mode has the same prefix boundary as the approved BranchCreate modes.
    NEW.inherited_revision_count := point_ordinal
        - CASE WHEN NEW.branch_mode = 'replace_message' THEN 1 ELSE 0 END;
    RETURN NEW;
END
$function$;

CREATE FUNCTION archive.guard_branch_link_update() RETURNS trigger
LANGUAGE plpgsql SET search_path = pg_catalog, pg_temp
AS $function$
BEGIN
    IF ROW(NEW.parent_conversation_id, NEW.branch_point_revision_id,
            NEW.branch_mode, NEW.inherited_revision_count)
        IS DISTINCT FROM ROW(OLD.parent_conversation_id, OLD.branch_point_revision_id,
            OLD.branch_mode, OLD.inherited_revision_count) THEN
        RAISE EXCEPTION 'branch linkage and prefix boundary are immutable'
            USING ERRCODE = '23514';
    END IF;
    RETURN NEW;
END
$function$;

CREATE FUNCTION archive.reject_branch_revision_update() RETURNS trigger
LANGUAGE plpgsql SET search_path = pg_catalog, pg_temp
AS $function$
BEGIN
    RAISE EXCEPTION 'branch snapshot references are immutable' USING ERRCODE = '23514';
END
$function$;

CREATE FUNCTION archive.require_exact_branch_snapshot() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog, pg_temp
SET row_security = on
AS $function$
DECLARE
    branch archive.conversations%ROWTYPE;
BEGIN
    -- Inspect final rows, including when trusted code has cleared actor context.
    -- A deleted conversation needs no completeness guard (governed purge only).
    SELECT * INTO branch FROM archive.conversations
        WHERE user_id = NEW.user_id AND conversation_id = NEW.conversation_id;
    IF NOT FOUND THEN
        RETURN NULL;
    END IF;
    IF (SELECT count(*) FROM archive.conversation_branch_revisions
        WHERE user_id = branch.user_id AND conversation_id = branch.conversation_id)
            <> branch.inherited_revision_count
        OR EXISTS (
            SELECT FROM archive.conversation_branch_revisions s
            WHERE s.user_id = branch.user_id AND s.conversation_id = branch.conversation_id
                AND (s.ordinal > branch.inherited_revision_count OR NOT EXISTS (
                    SELECT FROM archive.conversation_visible_revisions p
                    WHERE p.user_id = branch.user_id
                        AND p.conversation_id = branch.parent_conversation_id
                        AND p.ordinal = s.ordinal AND p.message_id = s.message_id
                        AND p.revision_id = s.revision_id))) THEN
        RAISE EXCEPTION 'branch snapshot must equal the dense exact parent prefix'
            USING ERRCODE = '23514';
    END IF;
    RETURN NULL;
END
$function$;

CREATE TRIGGER branch_prepare BEFORE INSERT ON archive.conversations
    FOR EACH ROW EXECUTE FUNCTION archive.prepare_branch_link();
CREATE TRIGGER branch_link_update_guard BEFORE UPDATE ON archive.conversations
    FOR EACH ROW EXECUTE FUNCTION archive.guard_branch_link_update();
CREATE TRIGGER branch_revision_update_guard BEFORE UPDATE ON archive.conversation_branch_revisions
    FOR EACH ROW EXECUTE FUNCTION archive.reject_branch_revision_update();
CREATE CONSTRAINT TRIGGER branch_snapshot_required AFTER INSERT ON archive.conversations
    DEFERRABLE INITIALLY DEFERRED FOR EACH ROW
    EXECUTE FUNCTION archive.require_exact_branch_snapshot();
CREATE CONSTRAINT TRIGGER branch_snapshot_exact AFTER INSERT ON archive.conversation_branch_revisions
    DEFERRABLE INITIALLY DEFERRED FOR EACH ROW
    EXECUTE FUNCTION archive.require_exact_branch_snapshot();

ALTER TABLE archive.conversation_branch_revisions ENABLE ROW LEVEL SECURITY;
ALTER TABLE archive.conversation_branch_revisions FORCE ROW LEVEL SECURITY;
CREATE POLICY branch_revisions_tenant ON archive.conversation_branch_revisions
    USING (user_id = nullif(current_setting('app.current_user_id', true), '')::uuid)
    WITH CHECK (user_id = nullif(current_setting('app.current_user_id', true), '')::uuid);
-- SELECT-only owner visibility for the deferred completeness trigger. No owner
-- write bypass or runtime EXECUTE is introduced; V001 revokes default EXECUTE.
CREATE POLICY conversations_branch_commit_guard ON archive.conversations FOR SELECT
    TO mimir_owner USING (true);
CREATE POLICY branch_revisions_commit_guard ON archive.conversation_branch_revisions FOR SELECT
    TO mimir_owner USING (true);

GRANT SELECT ON archive.conversation_branch_revisions,
    archive.conversation_visible_revisions TO mimir_api, mimir_worker;
GRANT INSERT ON archive.conversation_branch_revisions TO mimir_api;
-- No snapshot UPDATE/DELETE/TRUNCATE, worker branch creation or public routines.
RESET ROLE;
