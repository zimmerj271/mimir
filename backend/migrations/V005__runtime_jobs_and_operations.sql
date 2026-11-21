-- T07 storage prerequisites. T12/T16/T21/T23/T32 own transactional services,
-- execution, cleanup and token-conditional claims/completion; no worker runs here.
DO $guard$
BEGIN
    IF session_user <> 'mimir_migrator' OR current_user <> 'mimir_migrator' THEN
        RAISE EXCEPTION 'V005 requires the dedicated migration login';
    END IF;
END
$guard$;
SET ROLE mimir_owner;

CREATE TABLE runtime.model_runs (
    run_id uuid PRIMARY KEY,
    user_id uuid NOT NULL REFERENCES archive.users (user_id),
    kind text NOT NULL CHECK (kind IN ('conversation', 'standalone')),
    conversation_id uuid,
    input_message_id uuid,
    assistant_message_id uuid,
    model_config_id text NOT NULL CHECK (model_config_id <> ''),
    status text NOT NULL DEFAULT 'pending'
        CHECK (status IN ('pending','running','completed','failed','cancelled','interrupted')),
    cancellation_requested boolean NOT NULL DEFAULT false,
    checkpoint_text text NOT NULL DEFAULT '',
    checkpoint_sequence bigint NOT NULL DEFAULT 0 CHECK (checkpoint_sequence >= 0),
    final_text text CHECK (octet_length(convert_to(final_text, 'UTF8')) <= 1048576),
    -- Temporary standalone prompt, never an archive message or diagnostic.
    -- Architecture section 10 uses encrypted host volumes/backups, without
    -- application field encryption. T21/T23 own input retention and cleanup.
    input_text text CHECK (octet_length(convert_to(input_text, 'UTF8')) <= 1048576),
    error_code text,
    execution_token uuid,
    execution_generation bigint NOT NULL DEFAULT 0 CHECK (execution_generation >= 0),
    lease_expires_at timestamptz CHECK (isfinite(lease_expires_at)),
    created_at timestamptz NOT NULL DEFAULT CURRENT_TIMESTAMP CHECK (isfinite(created_at)),
    started_at timestamptz CHECK (isfinite(started_at) AND started_at >= created_at),
    completed_at timestamptz CHECK (isfinite(completed_at) AND completed_at >= created_at),
    result_expires_at timestamptz CHECK (isfinite(result_expires_at)),
    artifacts_purged_at timestamptz CHECK (isfinite(artifacts_purged_at)),
    UNIQUE (user_id, run_id),
    FOREIGN KEY (user_id, conversation_id)
        REFERENCES archive.conversations (user_id, conversation_id),
    FOREIGN KEY (user_id, input_message_id) REFERENCES archive.messages (user_id, message_id),
    FOREIGN KEY (user_id, assistant_message_id) REFERENCES archive.messages (user_id, message_id),
    CHECK ((kind = 'conversation' AND conversation_id IS NOT NULL AND input_message_id IS NOT NULL
            AND input_text IS NULL AND final_text IS NULL
            AND result_expires_at IS NULL)
        OR (kind = 'standalone' AND conversation_id IS NULL AND input_message_id IS NULL
            AND assistant_message_id IS NULL)),
    CHECK ((status IN ('pending','running')) = (completed_at IS NULL)),
    CHECK (started_at IS NULL OR completed_at IS NULL OR completed_at >= started_at),
    CHECK ((status = 'running' AND execution_token IS NOT NULL AND execution_generation > 0
            AND lease_expires_at IS NOT NULL AND started_at IS NOT NULL)
        OR (status <> 'running' AND execution_token IS NULL AND lease_expires_at IS NULL)),
    CHECK ((kind = 'conversation' AND status = 'completed') = (assistant_message_id IS NOT NULL)),
    CHECK ((kind = 'standalone' AND status = 'completed') =
        (final_text IS NOT NULL OR artifacts_purged_at IS NOT NULL)),
    CHECK ((kind = 'standalone' AND status = 'completed'
                AND result_expires_at IS NOT NULL
                AND result_expires_at = completed_at + interval '168 hours')
        OR (NOT (kind = 'standalone' AND status = 'completed') AND result_expires_at IS NULL)),
    CHECK (artifacts_purged_at IS NULL OR (kind='standalone' AND status='completed'
        AND artifacts_purged_at >= result_expires_at AND final_text IS NULL
        AND input_text IS NULL
        AND checkpoint_text='' AND checkpoint_sequence=0))
);
CREATE UNIQUE INDEX model_runs_one_active_conversation
    ON runtime.model_runs (user_id, conversation_id)
    WHERE conversation_id IS NOT NULL AND status IN ('pending','running');
CREATE INDEX model_runs_owner_created ON runtime.model_runs (user_id, created_at, run_id);
CREATE INDEX model_runs_pending ON runtime.model_runs (created_at, run_id) WHERE status = 'pending';
CREATE INDEX model_runs_leases ON runtime.model_runs (lease_expires_at, run_id) WHERE status = 'running';
CREATE INDEX model_runs_expiry ON runtime.model_runs (result_expires_at, run_id)
    WHERE result_expires_at IS NOT NULL;

CREATE TABLE runtime.model_run_attempts (
    attempt_id uuid PRIMARY KEY,
    user_id uuid NOT NULL,
    run_id uuid NOT NULL,
    attempt_number integer NOT NULL CHECK (attempt_number > 0),
    execution_generation bigint NOT NULL CHECK (execution_generation > 0),
    execution_token uuid NOT NULL,
    provider_config_id text NOT NULL CHECK (provider_config_id <> ''),
    started_at timestamptz NOT NULL DEFAULT CURRENT_TIMESTAMP CHECK (isfinite(started_at)),
    completed_at timestamptz CHECK (isfinite(completed_at) AND completed_at >= started_at),
    outcome text CHECK (outcome IN ('completed','failed','cancelled','interrupted')),
    output_visible boolean NOT NULL DEFAULT false,
    input_tokens bigint CHECK (input_tokens >= 0),
    output_tokens bigint CHECK (output_tokens >= 0),
    error_code text,
    UNIQUE (user_id, attempt_id),
    UNIQUE (user_id, run_id, attempt_number),
    FOREIGN KEY (user_id, run_id) REFERENCES runtime.model_runs (user_id, run_id),
    CHECK ((outcome IS NULL) = (completed_at IS NULL))
);
CREATE UNIQUE INDEX model_run_attempts_one_open ON runtime.model_run_attempts (user_id, run_id)
    WHERE completed_at IS NULL;

CREATE TABLE runtime.jobs (
    job_id uuid PRIMARY KEY,
    user_id uuid NOT NULL REFERENCES archive.users (user_id),
    job_type text NOT NULL CHECK (job_type <> ''),
    status text NOT NULL DEFAULT 'pending' CHECK (status IN
        ('pending','running','retry_wait','reconciliation_required','completed','failed','cancelled')),
    payload jsonb NOT NULL DEFAULT '{}' CHECK (jsonb_typeof(payload) = 'object'),
    created_at timestamptz NOT NULL DEFAULT CURRENT_TIMESTAMP CHECK (isfinite(created_at)),
    available_at timestamptz NOT NULL DEFAULT CURRENT_TIMESTAMP CHECK (isfinite(available_at)),
    attempt_count integer NOT NULL DEFAULT 0 CHECK (attempt_count BETWEEN 0 AND 5),
    lease_token uuid,
    lease_generation bigint NOT NULL DEFAULT 0 CHECK (lease_generation >= 0),
    lease_expires_at timestamptz CHECK (isfinite(lease_expires_at)),
    heartbeat_at timestamptz CHECK (isfinite(heartbeat_at)),
    completed_at timestamptz CHECK (isfinite(completed_at) AND completed_at >= created_at),
    error_code text,
    UNIQUE (user_id, job_id),
    CHECK ((status = 'running' AND lease_token IS NOT NULL AND lease_generation > 0
            AND lease_expires_at IS NOT NULL AND heartbeat_at IS NOT NULL AND attempt_count > 0
            AND lease_expires_at > heartbeat_at)
        OR (status <> 'running' AND lease_token IS NULL AND lease_expires_at IS NULL
            AND heartbeat_at IS NULL)),
    CHECK ((status IN ('completed','failed','cancelled')) = (completed_at IS NOT NULL))
);
CREATE INDEX jobs_claimable ON runtime.jobs (available_at, created_at, job_id)
    WHERE status IN ('pending','retry_wait');
CREATE INDEX jobs_owner_fifo ON runtime.jobs (user_id, created_at, job_id)
    WHERE status NOT IN ('completed','failed','cancelled');
CREATE INDEX jobs_expired_leases ON runtime.jobs (lease_expires_at, job_id) WHERE status = 'running';
CREATE UNIQUE INDEX jobs_one_memory_ingest_per_user ON runtime.jobs (user_id)
    WHERE job_type='memory_ingest' AND status='running';

CREATE TABLE runtime.memory_ingestions (
    ingestion_id uuid PRIMARY KEY,
    user_id uuid NOT NULL,
    job_id uuid NOT NULL,
    run_id uuid NOT NULL,
    conversation_id uuid NOT NULL,
    project_id uuid,
    user_revision_id uuid NOT NULL,
    assistant_revision_id uuid NOT NULL,
    provider_config_id text NOT NULL CHECK (provider_config_id <> ''),
    event_version integer NOT NULL DEFAULT 1 CHECK (event_version = 1),
    committed_at timestamptz NOT NULL CHECK (isfinite(committed_at)),
    UNIQUE (user_id, ingestion_id),
    UNIQUE (user_id, job_id),
    UNIQUE (user_id, user_revision_id, assistant_revision_id, provider_config_id),
    FOREIGN KEY (user_id, job_id) REFERENCES runtime.jobs (user_id, job_id),
    FOREIGN KEY (user_id, run_id) REFERENCES runtime.model_runs (user_id, run_id),
    FOREIGN KEY (user_id, conversation_id) REFERENCES archive.conversations (user_id, conversation_id),
    FOREIGN KEY (user_id, project_id) REFERENCES archive.projects (user_id, project_id),
    FOREIGN KEY (user_id, user_revision_id) REFERENCES archive.message_revisions (user_id, revision_id),
    FOREIGN KEY (user_id, assistant_revision_id) REFERENCES archive.message_revisions (user_id, revision_id)
);
CREATE INDEX memory_ingestions_run ON runtime.memory_ingestions (user_id, run_id);

CREATE TABLE runtime.operation_ledger (
    user_id uuid NOT NULL REFERENCES archive.users (user_id),
    operation_type text NOT NULL CHECK (operation_type IN
        ('conversation_create','message_submit','branch_create','standalone_submit')),
    idempotency_key text NOT NULL CHECK (idempotency_key ~ '^[A-Za-z0-9._-]{8,128}$'),
    request_fingerprint bytea NOT NULL CHECK (octet_length(request_fingerprint) = 32),
    run_id uuid,
    conversation_id uuid,
    message_id uuid,
    conversation_version bigint CHECK (conversation_version > 0),
    status text NOT NULL DEFAULT 'accepted'
        CHECK (status IN ('accepted','completed','failed','cancelled','interrupted')),
    accepted_at timestamptz NOT NULL DEFAULT CURRENT_TIMESTAMP CHECK (isfinite(accepted_at)),
    retained_until timestamptz NOT NULL CHECK (isfinite(retained_until)),
    tombstone_until timestamptz NOT NULL CHECK (isfinite(tombstone_until)),
    -- Unresolved evidence must survive both retention boundaries. T23 owns cleanup.
    resolved_at timestamptz CHECK (isfinite(resolved_at) AND resolved_at >= accepted_at),
    PRIMARY KEY (user_id, operation_type, idempotency_key),
    -- Historical response identities must outlive deletion/expiry of resources.
    -- guard_operation verifies owned source rows at acceptance; immutable IDs
    -- remain evidence, never an authorization substitute for resource lookup.
    CHECK (retained_until = accepted_at + interval '720 hours'),
    CHECK (tombstone_until = retained_until + interval '720 hours'),
    CHECK ((operation_type IN ('conversation_create','branch_create') AND conversation_id IS NOT NULL
            AND run_id IS NULL AND message_id IS NULL AND conversation_version IS NOT NULL)
        OR (operation_type = 'message_submit' AND run_id IS NOT NULL AND conversation_id IS NOT NULL
            AND message_id IS NOT NULL AND conversation_version IS NOT NULL)
        OR (operation_type = 'standalone_submit' AND run_id IS NOT NULL AND conversation_id IS NULL
            AND message_id IS NULL AND conversation_version IS NULL))
);
CREATE INDEX operation_ledger_cleanup ON runtime.operation_ledger (tombstone_until)
    WHERE resolved_at IS NOT NULL;
CREATE INDEX operation_ledger_run ON runtime.operation_ledger (user_id, run_id) WHERE run_id IS NOT NULL;

CREATE TABLE runtime.outbox_events (
    event_id uuid PRIMARY KEY,
    user_id uuid NOT NULL REFERENCES archive.users (user_id),
    event_type text NOT NULL CHECK (event_type <> ''),
    event_version integer NOT NULL CHECK (event_version > 0),
    payload jsonb NOT NULL CHECK (jsonb_typeof(payload) = 'object'),
    created_at timestamptz NOT NULL DEFAULT CURRENT_TIMESTAMP CHECK (isfinite(created_at)),
    published_at timestamptz CHECK (isfinite(published_at) AND published_at >= created_at),
    UNIQUE (user_id, event_id)
);
CREATE INDEX outbox_events_unpublished ON runtime.outbox_events (created_at, event_id)
    WHERE published_at IS NULL;

CREATE TABLE runtime.context_assembly_records (
    context_id uuid PRIMARY KEY,
    user_id uuid NOT NULL,
    run_id uuid NOT NULL,
    policy_version text NOT NULL CHECK (policy_version <> ''),
    model_policy_version text NOT NULL CHECK (model_policy_version <> ''),
    selected_revision_ids uuid[] NOT NULL DEFAULT '{}',
    selected_memory_ids text[] NOT NULL DEFAULT '{}',
    estimated_input_tokens bigint NOT NULL CHECK (estimated_input_tokens >= 0),
    reserved_output_tokens bigint NOT NULL CHECK (reserved_output_tokens >= 0),
    budget_mode text NOT NULL CHECK (budget_mode <> ''),
    truncation_reasons text[] NOT NULL DEFAULT '{}',
    memory_degraded boolean NOT NULL DEFAULT false,
    created_at timestamptz NOT NULL DEFAULT CURRENT_TIMESTAMP CHECK (isfinite(created_at)),
    UNIQUE (user_id, context_id),
    FOREIGN KEY (user_id, run_id) REFERENCES runtime.model_runs (user_id, run_id)
    -- No assembled prompt or memory content column. Source validation is T14/T34.
);
CREATE INDEX context_assembly_records_run ON runtime.context_assembly_records (user_id, run_id);

-- PostgreSQL control metadata is not the independent restoration journal.
-- T35/T37 own restricted fencing, journal persistence/replay and verified purge.
-- No runtime role can write these controls through direct table grants.
CREATE TABLE runtime.deletion_controls (
    deletion_id uuid PRIMARY KEY,
    user_id uuid NOT NULL REFERENCES archive.users (user_id),
    status text NOT NULL DEFAULT 'requested'
        CHECK (status IN ('requested','fenced','purging','verification_required','complete')),
    fence_token uuid,
    journal_reference text CHECK (journal_reference <> ''),
    requested_at timestamptz NOT NULL DEFAULT CURRENT_TIMESTAMP CHECK (isfinite(requested_at)),
    fenced_at timestamptz CHECK (isfinite(fenced_at) AND fenced_at >= requested_at),
    completed_at timestamptz CHECK (isfinite(completed_at) AND completed_at >= fenced_at),
    UNIQUE (user_id, deletion_id),
    CHECK ((status='requested' AND fence_token IS NULL AND fenced_at IS NULL
            AND journal_reference IS NULL)
        OR (status<>'requested' AND fence_token IS NOT NULL AND fenced_at IS NOT NULL
            AND journal_reference IS NOT NULL)),
    CHECK ((status='complete') = (completed_at IS NOT NULL))
);
CREATE UNIQUE INDEX deletion_controls_one_open ON runtime.deletion_controls (user_id)
    WHERE status<>'complete';

CREATE FUNCTION runtime.guard_run() RETURNS trigger
LANGUAGE plpgsql SET search_path = pg_catalog, pg_temp
AS $function$
BEGIN
    IF TG_OP = 'UPDATE' THEN
        IF ROW(NEW.run_id,NEW.user_id,NEW.kind,NEW.conversation_id,NEW.input_message_id,
                NEW.model_config_id,NEW.created_at)
            IS DISTINCT FROM ROW(OLD.run_id,OLD.user_id,OLD.kind,OLD.conversation_id,
                OLD.input_message_id,OLD.model_config_id,OLD.created_at)
            OR (OLD.status IN ('completed','failed','cancelled','interrupted')
                AND NEW IS DISTINCT FROM OLD AND NOT (
                    -- A future restricted T21 cleanup routine may erase expired
                    -- artifacts while retaining immutable operation/run identity.
                    -- No runtime role receives permission to set this marker.
                    OLD.artifacts_purged_at IS NULL AND NEW.artifacts_purged_at IS NOT NULL
                    AND NEW.artifacts_purged_at <= clock_timestamp()
                    AND (to_jsonb(NEW) - ARRAY['artifacts_purged_at','final_text',
                        'input_text','checkpoint_text','checkpoint_sequence'])
                        = (to_jsonb(OLD) - ARRAY['artifacts_purged_at','final_text',
                        'input_text','checkpoint_text','checkpoint_sequence'])))
            OR (NEW.checkpoint_sequence < OLD.checkpoint_sequence AND NEW.artifacts_purged_at IS NULL)
            OR (NEW.checkpoint_text IS DISTINCT FROM OLD.checkpoint_text
                AND NEW.checkpoint_sequence <= OLD.checkpoint_sequence AND NEW.artifacts_purged_at IS NULL)
            OR NEW.execution_generation < OLD.execution_generation THEN
            RAISE EXCEPTION 'run identity, terminal outcome and checkpoint sequence are immutable/monotonic'
                USING ERRCODE = '23514';
        END IF;
        IF NEW.status <> OLD.status AND NOT (
            (OLD.status = 'pending' AND NEW.status IN ('running','cancelled','interrupted','failed'))
            OR (OLD.status = 'running' AND NEW.status IN ('completed','failed','cancelled','interrupted'))) THEN
            RAISE EXCEPTION 'invalid run transition' USING ERRCODE = '23514';
        END IF;
        IF NEW.execution_token IS DISTINCT FROM OLD.execution_token AND NEW.execution_token IS NOT NULL
            AND NEW.execution_generation <= OLD.execution_generation THEN
            RAISE EXCEPTION 'new executor requires a newer generation' USING ERRCODE = '23514';
        END IF;
    ELSIF NEW.status <> 'pending' OR NEW.started_at IS NOT NULL
        OR NEW.execution_generation <> 0 OR NEW.checkpoint_sequence <> 0 OR NEW.checkpoint_text <> '' THEN
        RAISE EXCEPTION 'runs must be submitted pending and unclaimed without output' USING ERRCODE = '23514';
    END IF;
    IF NEW.kind = 'conversation' THEN
        IF NOT EXISTS (SELECT FROM archive.messages WHERE user_id=NEW.user_id
            AND message_id=NEW.input_message_id AND conversation_id=NEW.conversation_id AND role='user')
            OR (NEW.assistant_message_id IS NOT NULL AND NOT EXISTS (SELECT FROM archive.messages
                WHERE user_id=NEW.user_id AND message_id=NEW.assistant_message_id
                    AND conversation_id=NEW.conversation_id AND role='assistant')) THEN
            RAISE EXCEPTION 'run messages must belong to its conversation and have the required roles'
                USING ERRCODE = '23514';
        END IF;
    END IF;
    RETURN NEW;
END
$function$;
CREATE TRIGGER model_runs_guard BEFORE INSERT OR UPDATE ON runtime.model_runs
    FOR EACH ROW EXECUTE FUNCTION runtime.guard_run();

CREATE FUNCTION runtime.guard_job() RETURNS trigger
LANGUAGE plpgsql SET search_path = pg_catalog, pg_temp
AS $function$
BEGIN
    IF TG_OP = 'INSERT' THEN
        IF NEW.status <> 'pending' OR NEW.attempt_count <> 0 OR NEW.lease_generation <> 0 THEN
            RAISE EXCEPTION 'jobs must be enqueued pending and unclaimed' USING ERRCODE = '23514';
        END IF;
    ELSE
        IF ROW(NEW.job_id,NEW.user_id,NEW.job_type,NEW.payload,NEW.created_at)
            IS DISTINCT FROM ROW(OLD.job_id,OLD.user_id,OLD.job_type,OLD.payload,OLD.created_at)
            OR (OLD.status IN ('completed','failed','cancelled') AND NEW IS DISTINCT FROM OLD)
            OR NEW.attempt_count < OLD.attempt_count OR NEW.lease_generation < OLD.lease_generation THEN
            RAISE EXCEPTION 'job identity, payload and terminal outcome are immutable; counters monotonic'
                USING ERRCODE = '23514';
        END IF;
        IF NEW.status <> OLD.status AND NOT (
            (OLD.status='pending' AND NEW.status IN ('running','cancelled'))
            OR (OLD.status='running' AND NEW.status IN
                ('completed','retry_wait','failed','cancelled','reconciliation_required'))
            OR (OLD.status='retry_wait' AND NEW.status IN ('running','cancelled','failed'))
            OR (OLD.status='reconciliation_required' AND NEW.status IN ('pending','completed','failed','cancelled'))) THEN
            RAISE EXCEPTION 'invalid job transition' USING ERRCODE = '23514';
        END IF;
        IF NEW.lease_token IS DISTINCT FROM OLD.lease_token AND NEW.lease_token IS NOT NULL
            AND (OLD.status NOT IN ('pending','retry_wait')
                OR NEW.lease_generation <= OLD.lease_generation OR NEW.attempt_count <> OLD.attempt_count+1) THEN
            RAISE EXCEPTION 'new job claim requires a newer generation and next attempt' USING ERRCODE = '23514';
        END IF;
    END IF;
    RETURN NEW;
END
$function$;
CREATE TRIGGER jobs_guard BEFORE INSERT OR UPDATE ON runtime.jobs
    FOR EACH ROW EXECUTE FUNCTION runtime.guard_job();

CREATE FUNCTION runtime.require_ingestion_source() RETURNS trigger
LANGUAGE plpgsql SET search_path = pg_catalog, pg_temp
AS $function$
BEGIN
    IF NOT EXISTS (SELECT FROM runtime.jobs j WHERE j.user_id=NEW.user_id
        AND j.job_id=NEW.job_id AND j.job_type='memory_ingest')
        OR NOT EXISTS (SELECT FROM runtime.model_runs r
        JOIN archive.messages u ON u.user_id=r.user_id AND u.message_id=r.input_message_id
        JOIN archive.messages a ON a.user_id=r.user_id AND a.message_id=r.assistant_message_id
        WHERE r.user_id=NEW.user_id AND r.run_id=NEW.run_id AND r.kind='conversation'
            AND r.status='completed' AND r.conversation_id=NEW.conversation_id
            AND u.current_revision_id=NEW.user_revision_id AND a.current_revision_id=NEW.assistant_revision_id) THEN
        RAISE EXCEPTION 'ingestion requires a successful exact conversational revision pair'
            USING ERRCODE = '23514';
    END IF;
    RETURN NEW;
END
$function$;
CREATE TRIGGER memory_ingestions_source BEFORE INSERT ON runtime.memory_ingestions
    FOR EACH ROW EXECUTE FUNCTION runtime.require_ingestion_source();

CREATE FUNCTION runtime.guard_attempt() RETURNS trigger
LANGUAGE plpgsql SET search_path = pg_catalog, pg_temp
AS $function$
BEGIN
    IF ROW(NEW.attempt_id,NEW.user_id,NEW.run_id,NEW.attempt_number,NEW.execution_generation,
            NEW.execution_token,NEW.provider_config_id,NEW.started_at)
        IS DISTINCT FROM ROW(OLD.attempt_id,OLD.user_id,OLD.run_id,OLD.attempt_number,
            OLD.execution_generation,OLD.execution_token,OLD.provider_config_id,OLD.started_at)
        OR (OLD.completed_at IS NOT NULL AND NEW IS DISTINCT FROM OLD)
        OR (OLD.output_visible AND NOT NEW.output_visible) THEN
        RAISE EXCEPTION 'attempt identity, visible output evidence and terminal outcome are immutable'
            USING ERRCODE = '23514';
    END IF;
    RETURN NEW;
END
$function$;
CREATE TRIGGER model_run_attempts_guard BEFORE UPDATE ON runtime.model_run_attempts
    FOR EACH ROW EXECUTE FUNCTION runtime.guard_attempt();

CREATE FUNCTION runtime.guard_operation() RETURNS trigger
LANGUAGE plpgsql SET search_path = pg_catalog, pg_temp
AS $function$
BEGIN
    IF TG_OP = 'INSERT' THEN
        IF NEW.conversation_id IS NOT NULL AND NOT EXISTS (SELECT FROM archive.conversations
            WHERE user_id=NEW.user_id AND conversation_id=NEW.conversation_id) THEN
            RAISE EXCEPTION 'operation requires an owned conversation identity'
                USING ERRCODE = '23514';
        END IF;
        IF NEW.run_id IS NOT NULL AND NOT EXISTS (SELECT FROM runtime.model_runs r
            WHERE r.user_id=NEW.user_id AND r.run_id=NEW.run_id
                AND ((NEW.operation_type='standalone_submit' AND r.kind='standalone')
                    OR (NEW.operation_type='message_submit' AND r.kind='conversation'
                        AND r.conversation_id=NEW.conversation_id AND r.input_message_id=NEW.message_id))) THEN
            RAISE EXCEPTION 'operation identity must match its owned run and input'
                USING ERRCODE = '23514';
        END IF;
        RETURN NEW;
    END IF;
    IF ROW(NEW.user_id,NEW.operation_type,NEW.idempotency_key,NEW.request_fingerprint,
            NEW.run_id,NEW.conversation_id,NEW.message_id,NEW.conversation_version,
            NEW.accepted_at,NEW.retained_until,NEW.tombstone_until)
        IS DISTINCT FROM ROW(OLD.user_id,OLD.operation_type,OLD.idempotency_key,OLD.request_fingerprint,
            OLD.run_id,OLD.conversation_id,OLD.message_id,OLD.conversation_version,
            OLD.accepted_at,OLD.retained_until,OLD.tombstone_until)
        OR (OLD.status <> 'accepted' AND NEW.status <> OLD.status)
        OR (OLD.resolved_at IS NOT NULL AND NEW.resolved_at IS DISTINCT FROM OLD.resolved_at) THEN
        RAISE EXCEPTION 'accepted operation identity, fingerprint and resolved evidence are immutable'
            USING ERRCODE = '23514';
    END IF;
    RETURN NEW;
END
$function$;
CREATE TRIGGER operation_ledger_guard BEFORE INSERT OR UPDATE ON runtime.operation_ledger
    FOR EACH ROW EXECUTE FUNCTION runtime.guard_operation();

CREATE FUNCTION runtime.guard_deletion_control() RETURNS trigger
LANGUAGE plpgsql SET search_path = pg_catalog, pg_temp
AS $function$
BEGIN
    IF TG_OP='INSERT' THEN
        IF NEW.status<>'requested' THEN
            RAISE EXCEPTION 'deletion controls start requested' USING ERRCODE = '23514';
        END IF;
    ELSE
        IF ROW(NEW.deletion_id,NEW.user_id,NEW.requested_at)
                IS DISTINCT FROM ROW(OLD.deletion_id,OLD.user_id,OLD.requested_at)
            OR (OLD.fence_token IS NOT NULL AND ROW(NEW.fence_token,NEW.fenced_at,NEW.journal_reference)
                IS DISTINCT FROM ROW(OLD.fence_token,OLD.fenced_at,OLD.journal_reference))
            OR (OLD.status='complete' AND NEW IS DISTINCT FROM OLD) THEN
            RAISE EXCEPTION 'deletion identity, established fence and completion are immutable'
                USING ERRCODE = '23514';
        END IF;
        IF NEW.status<>OLD.status AND NOT (
            (OLD.status='requested' AND NEW.status='fenced')
            OR (OLD.status='fenced' AND NEW.status='purging')
            OR (OLD.status='purging' AND NEW.status IN ('complete','verification_required'))
            OR (OLD.status='verification_required' AND NEW.status IN ('purging','complete'))) THEN
            RAISE EXCEPTION 'invalid deletion control transition' USING ERRCODE = '23514';
        END IF;
    END IF;
    RETURN NEW;
END
$function$;
CREATE TRIGGER deletion_controls_guard BEFORE INSERT OR UPDATE ON runtime.deletion_controls
    FOR EACH ROW EXECUTE FUNCTION runtime.guard_deletion_control();

-- Explicit tenant policies on every operational table. No owner write bypass,
-- unscoped discovery, public routine EXECUTE, runtime DDL or unrestricted purge.
DO $policies$
DECLARE table_name text;
BEGIN
    FOREACH table_name IN ARRAY ARRAY['model_runs','model_run_attempts','jobs',
        'memory_ingestions','operation_ledger','outbox_events','context_assembly_records',
        'deletion_controls'] LOOP
        EXECUTE format('ALTER TABLE runtime.%I ENABLE ROW LEVEL SECURITY', table_name);
        EXECUTE format('ALTER TABLE runtime.%I FORCE ROW LEVEL SECURITY', table_name);
        EXECUTE format('CREATE POLICY tenant_scope ON runtime.%I USING '
            || '(user_id = nullif(current_setting(''app.current_user_id'', true), '''')::uuid) '
            || 'WITH CHECK (user_id = nullif(current_setting(''app.current_user_id'', true), '''')::uuid)', table_name);
        EXECUTE format('GRANT SELECT ON runtime.%I TO mimir_api, mimir_worker', table_name);
    END LOOP;
END
$policies$;
GRANT INSERT ON runtime.model_runs, runtime.jobs, runtime.operation_ledger,
    runtime.outbox_events TO mimir_api;
GRANT UPDATE (cancellation_requested) ON runtime.model_runs TO mimir_api;
GRANT INSERT ON runtime.model_run_attempts, runtime.jobs, runtime.memory_ingestions,
    runtime.outbox_events, runtime.context_assembly_records TO mimir_worker;
GRANT UPDATE (status,cancellation_requested,checkpoint_text,checkpoint_sequence,final_text,
    assistant_message_id,error_code,execution_token,execution_generation,lease_expires_at,
    started_at,completed_at,result_expires_at) ON runtime.model_runs TO mimir_worker;
GRANT UPDATE (completed_at,outcome,output_visible,input_tokens,output_tokens,error_code)
    ON runtime.model_run_attempts TO mimir_worker;
GRANT UPDATE (status,available_at,attempt_count,lease_token,lease_generation,lease_expires_at,
    heartbeat_at,completed_at,error_code) ON runtime.jobs TO mimir_worker;
GRANT UPDATE (status,resolved_at) ON runtime.operation_ledger TO mimir_worker;
GRANT UPDATE (published_at) ON runtime.outbox_events TO mimir_worker;
RESET ROLE;
