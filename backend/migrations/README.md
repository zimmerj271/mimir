# Flyway migration ownership (approved)

No executable migration SQL is included in this design-only package. T03 through T07 exclusively author, test and submit PostgreSQL 18 Flyway SQL migrations in separate human-reviewed PRs: V001 role/schema, V002 users/auth, V003 archive/messages, V004 immutable branches, V005 runs/jobs. No Alembic. Historical v2 SQL in `../appendix/v2-reference/` is non-normative and must not be executed. Never rename, edit or repair an applied migration without explicit authorization.
