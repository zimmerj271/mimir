CREATE TABLE memory.memories (
 memory_id uuid PRIMARY KEY, user_id uuid NOT NULL REFERENCES archive.users(user_id),
 memory_type text NOT NULL CHECK(memory_type IN ('semantic','episodic')),
 status text NOT NULL CHECK(status IN ('active','superseded','retracted')),
 current_revision_id uuid, aggregate_version bigint NOT NULL DEFAULT 1 CHECK(aggregate_version>0),
 created_at timestamptz NOT NULL DEFAULT now(), updated_at timestamptz NOT NULL DEFAULT now(),
 UNIQUE(user_id,memory_id)
);
CREATE TABLE memory.memory_revisions (
 revision_id uuid PRIMARY KEY, user_id uuid NOT NULL, memory_id uuid NOT NULL,
 revision_number integer NOT NULL CHECK(revision_number>0),
 content text NOT NULL CHECK(length(btrim(content))>0),
 assertion_basis text NOT NULL CHECK(assertion_basis IN ('explicit','inferred')),
 certainty text NOT NULL CHECK(certainty IN ('established','tentative')),
 temporality text NOT NULL CHECK(temporality IN ('current','historical','future','unspecified')),
 valid_from timestamptz, valid_to timestamptz,
 temporal_precision text CHECK(temporal_precision IN ('exact','day','month','year','approximate')),
 temporal_expression text,
 aggregate_version bigint NOT NULL CHECK(aggregate_version>0),
 content_hash text NOT NULL, created_at timestamptz NOT NULL DEFAULT now(),
 UNIQUE(user_id,revision_id), UNIQUE(user_id,memory_id,revision_id),
 UNIQUE(memory_id,revision_number), UNIQUE(memory_id,aggregate_version),
 FOREIGN KEY(user_id,memory_id) REFERENCES memory.memories(user_id,memory_id),
 CHECK(valid_from IS NULL OR valid_to IS NULL OR valid_to>valid_from)
);
ALTER TABLE memory.memories ADD CONSTRAINT memory_current_revision_fk
 FOREIGN KEY(user_id,memory_id,current_revision_id)
 REFERENCES memory.memory_revisions(user_id,memory_id,revision_id)
 DEFERRABLE INITIALLY DEFERRED;

CREATE TABLE memory.memory_evidence (
 evidence_id uuid PRIMARY KEY, user_id uuid NOT NULL, memory_id uuid NOT NULL,
 message_revision_id uuid NOT NULL,
 relationship text NOT NULL CHECK(relationship IN ('supports','contradicts','context')),
 status text NOT NULL DEFAULT 'active' CHECK(status IN ('active','withdrawn')),
 created_at timestamptz NOT NULL DEFAULT now(), updated_at timestamptz NOT NULL DEFAULT now(),
 UNIQUE(user_id,evidence_id), UNIQUE(memory_id,message_revision_id),
 FOREIGN KEY(user_id,memory_id) REFERENCES memory.memories(user_id,memory_id),
 FOREIGN KEY(user_id,message_revision_id) REFERENCES archive.message_revisions(user_id,revision_id)
);
CREATE TABLE memory.memory_projects (
 user_id uuid NOT NULL, memory_id uuid NOT NULL, project_id uuid NOT NULL,
 affinity_source text NOT NULL CHECK(affinity_source IN ('source','inferred','explicit')),
 created_at timestamptz NOT NULL DEFAULT now(),
 PRIMARY KEY(memory_id,project_id),
 FOREIGN KEY(user_id,memory_id) REFERENCES memory.memories(user_id,memory_id),
 FOREIGN KEY(user_id,project_id) REFERENCES archive.projects(user_id,project_id)
);
CREATE TABLE memory.memory_relationships (
 relationship_id uuid PRIMARY KEY, user_id uuid NOT NULL,
 source_memory_id uuid NOT NULL, target_memory_id uuid NOT NULL,
 relationship_type text NOT NULL CHECK(relationship_type IN ('supersedes','contradicts','related_to')),
 created_at timestamptz NOT NULL DEFAULT now(),
 UNIQUE(user_id,relationship_id),
 FOREIGN KEY(user_id,source_memory_id) REFERENCES memory.memories(user_id,memory_id),
 FOREIGN KEY(user_id,target_memory_id) REFERENCES memory.memories(user_id,memory_id),
 CHECK(source_memory_id <> target_memory_id),
 CHECK(relationship_type='supersedes' OR source_memory_id < target_memory_id),
 UNIQUE(source_memory_id,target_memory_id,relationship_type)
);
CREATE TABLE memory.memory_events (
 event_id uuid PRIMARY KEY, user_id uuid NOT NULL, memory_id uuid NOT NULL,
 operation_id uuid NOT NULL, aggregate_version bigint NOT NULL CHECK(aggregate_version>0),
 event_ordinal integer NOT NULL CHECK(event_ordinal>0), event_type text NOT NULL,
 event_payload jsonb NOT NULL DEFAULT '{}'::jsonb,
 created_at timestamptz NOT NULL DEFAULT now(),
 UNIQUE(memory_id,aggregate_version,event_ordinal),
 FOREIGN KEY(user_id,memory_id) REFERENCES memory.memories(user_id,memory_id),
 CHECK(jsonb_typeof(event_payload)='object')
);
CREATE INDEX memory_active_by_user_idx ON memory.memories(user_id,status,updated_at DESC);
CREATE INDEX evidence_source_idx ON memory.memory_evidence(user_id,message_revision_id);
CREATE INDEX memory_projects_lookup_idx ON memory.memory_projects(user_id,project_id);
CREATE INDEX memory_relationship_target_idx ON memory.memory_relationships(user_id,target_memory_id);
