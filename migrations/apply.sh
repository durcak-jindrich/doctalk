#!/usr/bin/env bash
#
# Applies migrations/*.sql in filename order, exactly once each, recording what
# ran in `schema_migrations`. Runs on the ParadeDB image via psql — it needs no
# Python, so bootstrapping the database never waits on the application image.
#
# The whole run is one psql session in one transaction holding an advisory
# lock, so concurrent runners (multiple replicas starting at once) serialise
# instead of racing, and a failure part-way leaves no half-applied schema.
#
set -euo pipefail
shopt -s nullglob

DB_URL="${DATABASE_URL:?DATABASE_URL is required}"
DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# Arbitrary but stable key, namespacing the lock to DocTalk's migrations.
LOCK_KEY=8027451

psql "$DB_URL" -v ON_ERROR_STOP=1 -q -c "
CREATE TABLE IF NOT EXISTS schema_migrations (
    version    TEXT PRIMARY KEY,
    checksum   TEXT NOT NULL,
    applied_at TIMESTAMPTZ NOT NULL DEFAULT now()
);"

{
    echo "BEGIN;"
    echo "DO \$\$ BEGIN PERFORM pg_advisory_xact_lock($LOCK_KEY); END \$\$;"

    for path in "$DIR"/*.sql; do
        version="$(basename "$path")"
        checksum="$(sha256sum "$path" | cut -d' ' -f1)"

        # Both the "already applied?" check and the apply happen inside the
        # lock, so two runners can never both decide a migration is pending.
        cat <<SQL
DO \$\$ BEGIN
    IF EXISTS (
        SELECT 1 FROM schema_migrations
        WHERE version = '$version' AND checksum <> '$checksum'
    ) THEN
        RAISE EXCEPTION '$version was edited after it was applied';
    END IF;
END \$\$;
SELECT NOT EXISTS (
    SELECT 1 FROM schema_migrations WHERE version = '$version'
) AS pending \gset
\if :pending
\echo 'applying $version'
\i $path
INSERT INTO schema_migrations (version, checksum) VALUES ('$version', '$checksum');
\endif
SQL
    done

    echo "COMMIT;"
} | psql "$DB_URL" -v ON_ERROR_STOP=1 -q -f -

psql "$DB_URL" -q -c "SELECT count(*) || ' migration(s) applied' FROM schema_migrations" -t -A
