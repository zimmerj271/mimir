-- T05 archive-local integrity only. Runtime atomicity belongs to T12/T16.
DO $guard$
BEGIN
    IF session_user <> 'mimir_migrator' OR current_user <> 'mimir_migrator' THEN
        RAISE EXCEPTION 'V003 requires the dedicated migration login';
    END IF;
END
$guard$;

SET ROLE mimir_owner;

CREATE TABLE archive.projects (
    project_id uuid PRIMARY KEY,
    user_id uuid NOT NULL REFERENCES archive.users (user_id),
    name text NOT NULL,
    description text,
    instructions text,
    status text NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'archived')),
    created_at timestamptz NOT NULL DEFAULT CURRENT_TIMESTAMP CHECK (isfinite(created_at)),
    UNIQUE (user_id, project_id)
);

CREATE TABLE archive.conversations (
    conversation_id uuid PRIMARY KEY,
    user_id uuid NOT NULL REFERENCES archive.users (user_id),
    project_id uuid,
    title text NOT NULL,
    status text NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'archived')),
    conversation_version bigint NOT NULL DEFAULT 1 CHECK (conversation_version > 0),
    created_at timestamptz NOT NULL DEFAULT CURRENT_TIMESTAMP CHECK (isfinite(created_at)),
    updated_at timestamptz NOT NULL DEFAULT CURRENT_TIMESTAMP
        CHECK (isfinite(updated_at) AND updated_at >= created_at),
    UNIQUE (user_id, conversation_id),
    FOREIGN KEY (user_id, project_id) REFERENCES archive.projects (user_id, project_id)
);

CREATE TABLE archive.messages (
    message_id uuid PRIMARY KEY,
    user_id uuid NOT NULL,
    conversation_id uuid NOT NULL,
    ordinal bigint NOT NULL CHECK (ordinal > 0),
    role text NOT NULL CHECK (role IN ('user', 'assistant', 'system', 'tool')),
    status text NOT NULL DEFAULT 'completed' CHECK (status = 'completed'),
    -- Nullable only during relational insertion ordering, never at commit.
    current_revision_id uuid,
    created_at timestamptz NOT NULL DEFAULT CURRENT_TIMESTAMP CHECK (isfinite(created_at)),
    UNIQUE (user_id, message_id),
    UNIQUE (user_id, conversation_id, ordinal),
    FOREIGN KEY (user_id, conversation_id)
        REFERENCES archive.conversations (user_id, conversation_id)
);

CREATE TABLE archive.message_revisions (
    revision_id uuid PRIMARY KEY,
    user_id uuid NOT NULL,
    message_id uuid NOT NULL,
    revision_number bigint NOT NULL CHECK (revision_number > 0),
    content_format text NOT NULL DEFAULT 'text/v1' CHECK (content_format = 'text/v1'),
    content_text text NOT NULL CHECK (octet_length(convert_to(content_text, 'UTF8')) <= 1048576),
    -- Computed by the insertion trigger. convert_to is STABLE, so this cannot
    -- be a generated column; the check also verifies the derived value.
    content_hash bytea NOT NULL CHECK (content_hash = sha256(convert_to(content_text, 'UTF8'))),
    created_at timestamptz NOT NULL DEFAULT CURRENT_TIMESTAMP CHECK (isfinite(created_at)),
    UNIQUE (user_id, message_id, revision_id),
    UNIQUE (user_id, message_id, revision_number),
    FOREIGN KEY (user_id, message_id) REFERENCES archive.messages (user_id, message_id)
        DEFERRABLE INITIALLY DEFERRED
);

ALTER TABLE archive.messages ADD CONSTRAINT message_current_revision_fk
    FOREIGN KEY (user_id, message_id, current_revision_id)
    REFERENCES archive.message_revisions (user_id, message_id, revision_id)
    DEFERRABLE INITIALLY DEFERRED;

CREATE INDEX projects_owner_created ON archive.projects (user_id, created_at, project_id);
CREATE INDEX conversations_owner_updated
    ON archive.conversations (user_id, updated_at, conversation_id);
CREATE INDEX conversations_owner_project
    ON archive.conversations (user_id, project_id, conversation_id);

CREATE FUNCTION archive.guard_project_update() RETURNS trigger
LANGUAGE plpgsql SET search_path = pg_catalog, pg_temp
AS $function$
BEGIN
    IF ROW(NEW.project_id, NEW.user_id, NEW.created_at)
        IS DISTINCT FROM ROW(OLD.project_id, OLD.user_id, OLD.created_at) THEN
        RAISE EXCEPTION 'project identity and ownership are immutable' USING ERRCODE = '23514';
    END IF;
    RETURN NEW;
END
$function$;

CREATE FUNCTION archive.guard_conversation_update() RETURNS trigger
LANGUAGE plpgsql SET search_path = pg_catalog, pg_temp
AS $function$
BEGIN
    IF ROW(NEW.conversation_id, NEW.user_id, NEW.created_at)
        IS DISTINCT FROM ROW(OLD.conversation_id, OLD.user_id, OLD.created_at) THEN
        RAISE EXCEPTION 'conversation identity and ownership are immutable' USING ERRCODE = '23514';
    END IF;
    IF NEW.conversation_version < OLD.conversation_version OR NEW.updated_at < OLD.updated_at
        OR (ROW(NEW.project_id, NEW.title, NEW.status)
            IS DISTINCT FROM ROW(OLD.project_id, OLD.title, OLD.status)
            AND NEW.conversation_version <= OLD.conversation_version) THEN
        RAISE EXCEPTION 'visible conversation changes require an increasing version'
            USING ERRCODE = '23514';
    END IF;
    RETURN NEW;
END
$function$;

CREATE FUNCTION archive.guard_message_update() RETURNS trigger
LANGUAGE plpgsql SET search_path = pg_catalog, pg_temp
AS $function$
BEGIN
    IF ROW(NEW.message_id, NEW.user_id, NEW.conversation_id, NEW.ordinal,
            NEW.role, NEW.status, NEW.created_at)
        IS DISTINCT FROM ROW(OLD.message_id, OLD.user_id, OLD.conversation_id,
            OLD.ordinal, OLD.role, OLD.status, OLD.created_at)
        OR (OLD.current_revision_id IS NOT NULL
            AND NEW.current_revision_id IS DISTINCT FROM OLD.current_revision_id) THEN
        RAISE EXCEPTION 'canonical message identity, ordering and selected revision are immutable'
            USING ERRCODE = '23514';
    END IF;
    RETURN NEW;
END
$function$;

CREATE FUNCTION archive.reject_revision_update() RETURNS trigger
LANGUAGE plpgsql SET search_path = pg_catalog, pg_temp
AS $function$
BEGIN
    RAISE EXCEPTION 'message revisions are immutable' USING ERRCODE = '23514';
END
$function$;

CREATE FUNCTION archive.require_message_revision() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog, pg_temp
SET row_security = on
AS $function$
BEGIN
    -- Inspect the final row, not the INSERT event's initially nullable pointer.
    -- Rows deleted in this transaction do not survive commit and need no guard.
    IF EXISTS (SELECT FROM archive.messages
        WHERE user_id = NEW.user_id AND message_id = NEW.message_id
            AND current_revision_id IS NULL) THEN
        RAISE EXCEPTION 'canonical messages require a selected revision at commit'
            USING ERRCODE = '23514';
    END IF;
    RETURN NULL;
END
$function$;

CREATE FUNCTION archive.require_dense_message_ordinal() RETURNS trigger
LANGUAGE plpgsql SET search_path = pg_catalog, pg_temp
AS $function$
DECLARE
    next_ordinal bigint;
BEGIN
    -- Serialize appends in READ COMMITTED. The following query gets a fresh
    -- snapshot after a competing inserter releases this parent lock.
    PERFORM 1 FROM archive.conversations
        WHERE user_id = NEW.user_id AND conversation_id = NEW.conversation_id FOR UPDATE;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'message requires an owned conversation' USING ERRCODE = '23503';
    END IF;
    SELECT coalesce(max(ordinal), 0) + 1 INTO next_ordinal FROM archive.messages
        WHERE user_id = NEW.user_id AND conversation_id = NEW.conversation_id;
    IF NEW.ordinal <> next_ordinal THEN
        RAISE EXCEPTION 'message ordinals must be dense and start at one' USING ERRCODE = '23514';
    END IF;
    RETURN NEW;
END
$function$;

CREATE FUNCTION archive.require_dense_revision_number() RETURNS trigger
LANGUAGE plpgsql SET search_path = pg_catalog, pg_temp
AS $function$
DECLARE
    next_revision bigint;
BEGIN
    PERFORM 1 FROM archive.messages
        WHERE user_id = NEW.user_id AND message_id = NEW.message_id FOR UPDATE;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'revision requires an owned message' USING ERRCODE = '23503';
    END IF;
    SELECT coalesce(max(revision_number), 0) + 1 INTO next_revision
        FROM archive.message_revisions WHERE user_id = NEW.user_id AND message_id = NEW.message_id;
    IF NEW.revision_number <> next_revision THEN
        RAISE EXCEPTION 'revision numbers must be dense and start at one' USING ERRCODE = '23514';
    END IF;
    NEW.content_hash := sha256(convert_to(NEW.content_text, 'UTF8'));
    RETURN NEW;
END
$function$;

CREATE TRIGGER project_update_guard BEFORE UPDATE ON archive.projects
    FOR EACH ROW EXECUTE FUNCTION archive.guard_project_update();
CREATE TRIGGER conversation_update_guard BEFORE UPDATE ON archive.conversations
    FOR EACH ROW EXECUTE FUNCTION archive.guard_conversation_update();
CREATE TRIGGER message_update_guard BEFORE UPDATE ON archive.messages
    FOR EACH ROW EXECUTE FUNCTION archive.guard_message_update();
CREATE TRIGGER revision_update_guard BEFORE UPDATE ON archive.message_revisions
    FOR EACH ROW EXECUTE FUNCTION archive.reject_revision_update();
CREATE CONSTRAINT TRIGGER message_revision_required AFTER INSERT OR UPDATE ON archive.messages
    DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION archive.require_message_revision();
CREATE TRIGGER message_dense_ordinal BEFORE INSERT ON archive.messages
    FOR EACH ROW EXECUTE FUNCTION archive.require_dense_message_ordinal();
CREATE TRIGGER revision_dense_number BEFORE INSERT ON archive.message_revisions
    FOR EACH ROW EXECUTE FUNCTION archive.require_dense_revision_number();

ALTER TABLE archive.projects ENABLE ROW LEVEL SECURITY;
ALTER TABLE archive.projects FORCE ROW LEVEL SECURITY;
ALTER TABLE archive.conversations ENABLE ROW LEVEL SECURITY;
ALTER TABLE archive.conversations FORCE ROW LEVEL SECURITY;
ALTER TABLE archive.messages ENABLE ROW LEVEL SECURITY;
ALTER TABLE archive.messages FORCE ROW LEVEL SECURITY;
ALTER TABLE archive.message_revisions ENABLE ROW LEVEL SECURITY;
ALTER TABLE archive.message_revisions FORCE ROW LEVEL SECURITY;

CREATE POLICY projects_tenant ON archive.projects
    USING (user_id = nullif(current_setting('app.current_user_id', true), '')::uuid)
    WITH CHECK (user_id = nullif(current_setting('app.current_user_id', true), '')::uuid);
CREATE POLICY conversations_tenant ON archive.conversations
    USING (user_id = nullif(current_setting('app.current_user_id', true), '')::uuid)
    WITH CHECK (user_id = nullif(current_setting('app.current_user_id', true), '')::uuid);
CREATE POLICY messages_tenant ON archive.messages
    USING (user_id = nullif(current_setting('app.current_user_id', true), '')::uuid)
    WITH CHECK (user_id = nullif(current_setting('app.current_user_id', true), '')::uuid);
CREATE POLICY revisions_tenant ON archive.message_revisions
    USING (user_id = nullif(current_setting('app.current_user_id', true), '')::uuid)
    WITH CHECK (user_id = nullif(current_setting('app.current_user_id', true), '')::uuid);

-- The deferred commit guard must still see its exact row if trusted code has
-- cleared/switched actor context before COMMIT. Only this fixed trigger runs
-- as the NOLOGIN owner; callers receive no routine EXECUTE or table privilege.
CREATE POLICY messages_commit_guard ON archive.messages FOR SELECT
    TO mimir_owner USING (true);

GRANT SELECT ON archive.projects, archive.conversations, archive.messages,
    archive.message_revisions TO mimir_api, mimir_worker;
GRANT INSERT ON archive.projects, archive.conversations TO mimir_api;
GRANT UPDATE (name, description, instructions, status) ON archive.projects TO mimir_api;
GRANT UPDATE (project_id, title, status, conversation_version, updated_at)
    ON archive.conversations TO mimir_api;
GRANT UPDATE (conversation_version, updated_at) ON archive.conversations TO mimir_worker;
GRANT INSERT ON archive.messages, archive.message_revisions TO mimir_api, mimir_worker;
GRANT UPDATE (current_revision_id) ON archive.messages TO mimir_api, mimir_worker;
-- No runtime DELETE/TRUNCATE, revision UPDATE, credential access or definer
-- routine. T35/T36 must supply restricted deletion interfaces under review.
RESET ROLE;
