CREATE TABLE runtime.model_runs (
 run_id uuid PRIMARY KEY, user_id uuid NOT NULL REFERENCES archive.users(user_id),
 conversation_id uuid, input_message_id uuid, output_message_id uuid,
 provider text NOT NULL, model text NOT NULL,
 status text NOT NULL CHECK(status IN ('pending','running','completed','failed','cancelled')),
 input_tokens integer CHECK(input_tokens>=0), output_tokens integer CHECK(output_tokens>=0),
 started_at timestamptz NOT NULL DEFAULT now(), completed_at timestamptz,
 error_code text, metadata jsonb NOT NULL DEFAULT '{}'::jsonb,
 UNIQUE(user_id,run_id),
 FOREIGN KEY(user_id,conversation_id) REFERENCES archive.conversations(user_id,conversation_id),
 FOREIGN KEY(user_id,input_message_id) REFERENCES archive.messages(user_id,message_id),
 FOREIGN KEY(user_id,output_message_id) REFERENCES archive.messages(user_id,message_id)
);
CREATE TABLE runtime.model_run_artifacts (
 artifact_id uuid PRIMARY KEY, user_id uuid NOT NULL, run_id uuid NOT NULL,
 artifact_type text NOT NULL, storage_uri text NOT NULL, sha256 text NOT NULL,
 created_at timestamptz NOT NULL DEFAULT now(),
 FOREIGN KEY(user_id,run_id) REFERENCES runtime.model_runs(user_id,run_id)
);
CREATE TABLE runtime.jobs (
 job_id uuid PRIMARY KEY, user_id uuid NOT NULL REFERENCES archive.users(user_id),
 job_type text NOT NULL, status text NOT NULL CHECK(status IN ('pending','running','completed','failed','cancelled')),
 idempotency_key text, payload jsonb NOT NULL DEFAULT '{}'::jsonb,
 attempt_count integer NOT NULL DEFAULT 0 CHECK(attempt_count>=0),
 next_attempt_at timestamptz, created_at timestamptz NOT NULL DEFAULT now(),
 started_at timestamptz, completed_at timestamptz, last_error text,
 UNIQUE(user_id,job_id), UNIQUE(user_id,idempotency_key)
);
CREATE TABLE runtime.outbox_events (
 event_id uuid PRIMARY KEY, user_id uuid NOT NULL REFERENCES archive.users(user_id),
 event_type text NOT NULL, aggregate_type text NOT NULL, aggregate_id uuid NOT NULL,
 payload jsonb NOT NULL DEFAULT '{}'::jsonb,
 created_at timestamptz NOT NULL DEFAULT now(), published_at timestamptz
);
CREATE TABLE runtime.operation_ledger (
 user_id uuid NOT NULL REFERENCES archive.users(user_id), operation_id uuid NOT NULL,
 operation_type text NOT NULL, status text NOT NULL CHECK(status IN ('committed','rejected')),
 result jsonb NOT NULL DEFAULT '{}'::jsonb, created_at timestamptz NOT NULL DEFAULT now(),
 PRIMARY KEY(user_id,operation_id)
);
CREATE TABLE runtime.retrieval_runs (
 retrieval_run_id uuid PRIMARY KEY, user_id uuid NOT NULL REFERENCES archive.users(user_id),
 query_hash text NOT NULL, project_id uuid,
 created_at timestamptz NOT NULL DEFAULT now(),
 UNIQUE(user_id,retrieval_run_id),
 FOREIGN KEY(user_id,project_id) REFERENCES archive.projects(user_id,project_id)
);
CREATE TABLE runtime.retrieval_results (
 retrieval_result_id uuid PRIMARY KEY, user_id uuid NOT NULL, retrieval_run_id uuid NOT NULL,
 memory_id uuid NOT NULL, revision_id uuid NOT NULL,
 ordinal integer NOT NULL CHECK(ordinal>0), similarity double precision,
 selected boolean NOT NULL DEFAULT false,
 UNIQUE(retrieval_run_id,ordinal),
 FOREIGN KEY(user_id,retrieval_run_id) REFERENCES runtime.retrieval_runs(user_id,retrieval_run_id),
 FOREIGN KEY(user_id,memory_id,revision_id) REFERENCES memory.memory_revisions(user_id,memory_id,revision_id)
);
CREATE TABLE memory.reconciliation_candidates (
 candidate_id uuid PRIMARY KEY, user_id uuid NOT NULL, source_job_id uuid NOT NULL,
 proposed_content text NOT NULL, proposed_action text NOT NULL
 CHECK(proposed_action IN ('CREATE','REINFORCE','REVISE','SUPERSEDE','RETRACT','NO_CHANGE','DEFER')),
 assertion_basis text NOT NULL CHECK(assertion_basis IN ('explicit','inferred')),
 certainty text NOT NULL CHECK(certainty IN ('established','tentative')),
 status text NOT NULL CHECK(status IN ('pending','deferred','accepted','rejected')),
 created_at timestamptz NOT NULL DEFAULT now(), resolved_at timestamptz,
 UNIQUE(user_id,candidate_id),
 FOREIGN KEY(user_id,source_job_id) REFERENCES runtime.jobs(user_id,job_id)
);
CREATE TABLE memory.candidate_targets (
 user_id uuid NOT NULL, candidate_id uuid NOT NULL, memory_id uuid NOT NULL,
 PRIMARY KEY(candidate_id,memory_id),
 FOREIGN KEY(user_id,candidate_id) REFERENCES memory.reconciliation_candidates(user_id,candidate_id),
 FOREIGN KEY(user_id,memory_id) REFERENCES memory.memories(user_id,memory_id)
);
CREATE TABLE memory.candidate_evidence (
 user_id uuid NOT NULL, candidate_id uuid NOT NULL, message_revision_id uuid NOT NULL,
 PRIMARY KEY(candidate_id,message_revision_id),
 FOREIGN KEY(user_id,candidate_id) REFERENCES memory.reconciliation_candidates(user_id,candidate_id),
 FOREIGN KEY(user_id,message_revision_id) REFERENCES archive.message_revisions(user_id,revision_id)
);
CREATE TABLE memory.memory_embeddings (
 embedding_id uuid PRIMARY KEY, user_id uuid NOT NULL, memory_id uuid NOT NULL, revision_id uuid NOT NULL,
 collection_name text NOT NULL, point_id uuid NOT NULL,
 model_id text NOT NULL, model_version text NOT NULL, embedding_config_id text NOT NULL,
 dimensions integer NOT NULL CHECK(dimensions>0), content_hash text NOT NULL,
 desired_state text NOT NULL CHECK(desired_state IN ('present','absent')),
 sync_state text NOT NULL CHECK(sync_state IN ('pending','in_progress','synchronized','failed')),
 last_attempt_at timestamptz, synchronized_at timestamptz,
 UNIQUE(user_id,embedding_id), UNIQUE(collection_name,point_id),
 FOREIGN KEY(user_id,memory_id,revision_id) REFERENCES memory.memory_revisions(user_id,memory_id,revision_id)
);
CREATE TABLE memory.vector_projection_events (
 event_id uuid PRIMARY KEY, user_id uuid NOT NULL, embedding_id uuid NOT NULL,
 operation_id uuid NOT NULL, operation text NOT NULL CHECK(operation IN ('upsert','delete')),
 collection_name text NOT NULL, point_id uuid NOT NULL,
 payload jsonb, payload_hash text, vector_hash text, request_artifact_uri text,
 status text NOT NULL CHECK(status IN ('pending','succeeded','failed')),
 attempted_at timestamptz, completed_at timestamptz, error_details text,
 FOREIGN KEY(user_id,embedding_id) REFERENCES memory.memory_embeddings(user_id,embedding_id)
);
CREATE INDEX jobs_claim_idx ON runtime.jobs(status,next_attempt_at);
CREATE INDEX embedding_reconcile_idx ON memory.memory_embeddings(sync_state,last_attempt_at);
CREATE INDEX outbox_unpublished_idx ON runtime.outbox_events(created_at) WHERE published_at IS NULL;
