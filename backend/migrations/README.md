# Flyway SQL migrations

T03 implements `V001__roles_and_schemas.sql`. T04–T07 exclusively own the later
V002 identity/auth, V003 archive, V004 branches and V005 runtime/jobs migrations,
each in a separately reviewed PR. No Alembic or ORM-generated production DDL.
Historical `../appendix/v2-reference/` SQL is non-normative and must not run.

The [database operator guide](../../deployment/database/README.md) documents
provisioning, pinned Flyway, protected role credentials, fresh/repeat/upgrade tests
and failure recovery. The migrator connects as `mimir_migrator`; V001 creates
`mimir_owner` NOLOGIN and four separate, non-owner runtime logins. Domain schemas
are owned by `mimir_owner`. Future migrations must explicitly `SET ROLE
mimir_owner` before creating domain objects and `RESET ROLE` before returning.
Grant only required object privileges with each future table or restricted routine;
T03 intentionally grants no table access or default runtime DML/EXECUTE rights.
Every future user-owned table requires FORCE RLS and real role-session tests.

Flyway history lives in `public`, owned by the migrator and inaccessible to
runtime roles. Only the migration login can assume `mimir_owner`; migration
credentials are privileged administrative material and never runtime credentials.

Never edit/rename an applied migration, use clean, baseline an existing schema,
or repair a checksum without separately authorized operator review. PostgreSQL
transactional migration failure rolls back DDL and history; fix only an unapplied
migration after review, then validate and retry explicitly. Successful migrations
are forward-only. No task here authorizes deleting volumes or restoring production.
