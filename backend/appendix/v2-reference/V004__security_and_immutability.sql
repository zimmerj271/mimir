-- Provision dedicated migration and runtime roles outside these migrations.
-- The app role must not own these tables, be a superuser, or have BYPASSRLS.
CREATE SCHEMA IF NOT EXISTS app_private;
CREATE FUNCTION app_private.current_user_id() RETURNS uuid LANGUAGE sql STABLE AS $$
 SELECT NULLIF(current_setting('app.current_user_id',true),'')::uuid
$$;

-- One same-owner predicate everywhere, with both read and write checks.
DO $$
DECLARE r record;
BEGIN
 FOR r IN
   SELECT table_schema,table_name FROM information_schema.columns
   WHERE column_name='user_id' AND table_schema IN ('archive','memory','runtime')
   GROUP BY table_schema,table_name
 LOOP
   EXECUTE format('ALTER TABLE %I.%I ENABLE ROW LEVEL SECURITY',r.table_schema,r.table_name);
   EXECUTE format('ALTER TABLE %I.%I FORCE ROW LEVEL SECURITY',r.table_schema,r.table_name);
   EXECUTE format('CREATE POLICY owner_scope ON %I.%I FOR ALL USING (user_id = app_private.current_user_id()) WITH CHECK (user_id = app_private.current_user_id())',r.table_schema,r.table_name);
 END LOOP;
END $$;

-- Committed content cannot be changed by the normal runtime role.
CREATE FUNCTION app_private.reject_immutable_changes() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
 RAISE EXCEPTION 'Immutable canonical records cannot be modified';
END $$;
CREATE TRIGGER message_revisions_immutable BEFORE UPDATE OR DELETE
 ON archive.message_revisions FOR EACH ROW EXECUTE FUNCTION app_private.reject_immutable_changes();
CREATE TRIGGER memory_revisions_immutable BEFORE UPDATE OR DELETE
 ON memory.memory_revisions FOR EACH ROW EXECUTE FUNCTION app_private.reject_immutable_changes();
CREATE TRIGGER memory_events_immutable BEFORE UPDATE OR DELETE
 ON memory.memory_events FOR EACH ROW EXECUTE FUNCTION app_private.reject_immutable_changes();
CREATE TRIGGER branch_snapshot_immutable BEFORE UPDATE OR DELETE
 ON archive.conversation_branch_revisions FOR EACH ROW EXECUTE FUNCTION app_private.reject_immutable_changes();
-- A purge design must provide a narrow privileged path; normal runtime role never disables these triggers.
