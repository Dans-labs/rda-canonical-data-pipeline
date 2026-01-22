#!/usr/bin/env bash
set -euo pipefail

# This script runs during postgres container initialization.
# It attempts to restore the SQL dump by connecting as the configured DB user.
# It no longer tries to connect as the 'postgres' superuser to avoid repeated
# FATAL logs when that role doesn't exist. If the DB user or database is missing
# and cannot be connected to, the script will exit successfully but skip restore.
# It also makes a best-effort attempt to create a simple 'postgres' role using
# the configured DB user if that user has CREATEROLE privileges, to avoid noisy
# FATAL logs from clients attempting to authenticate as 'postgres'.

RESOURCE_DIR=/docker-entrypoint-init-resources
DUMP_FILE="$RESOURCE_DIR/rda_db_dump.sql"

DB_NAME=${POSTGRES_DB:-rda}
DB_USER=${POSTGRES_USER:-rcdp}
DB_PASS=${POSTGRES_PASSWORD:-my_secret_password}

export PGPASSWORD="$DB_PASS"

sleep 1

if [ ! -f "$DUMP_FILE" ]; then
  echo "No dump file found at $DUMP_FILE; skipping restore."
  exit 0
fi

# Try to connect as the configured DB user to the target DB. If this fails,
# we cannot safely create/drop databases without a superuser, so we skip.
if psql -U "$DB_USER" -d "$DB_NAME" -c "SELECT 1;" >/dev/null 2>&1; then
  echo "Connected to database '$DB_NAME' as user '$DB_USER'. Proceeding with restore logic."
else
  echo "Cannot connect to database '$DB_NAME' as user '$DB_USER'."
  echo "Reason may be: role or database is missing, or authentication failed."
  echo "Skipping restore. To perform a restore you can either:"
  echo "  - create the role/database manually in the running cluster, or"
  echo "  - remove the postgres data volume so the container initializes with POSTGRES_USER/POSTGRES_DB and then re-run."
  # Exit 0 so the container init doesn't fail due to this script
  exit 0
fi

# Best-effort: create a simple 'postgres' role (non-superuser) if it doesn't exist,
# using the DB user. This reduces FATAL logs from clients expecting the role to exist.
# If the DB user lacks CREATEROLE, this will fail and we will continue.
if psql -U "$DB_USER" -d "$DB_NAME" -tAc "SELECT 1 FROM pg_roles WHERE rolname='postgres';" | grep -q 1; then
  echo "Role 'postgres' already exists."
else
  echo "Role 'postgres' does not exist. Attempting to create a simple 'postgres' role (best-effort)..."
  if psql -U "$DB_USER" -d "$DB_NAME" -v ON_ERROR_STOP=1 -c "CREATE ROLE postgres WITH LOGIN PASSWORD 'postgres';" >/dev/null 2>&1; then
    echo "Created role 'postgres' (non-superuser)."
  else
    echo "Could not create 'postgres' role as user '$DB_USER' (insufficient privileges). Continuing without role creation."
  fi
fi

# Count tables in public schema and restore or recreate schema as required
TABLE_COUNT=$(psql -U "$DB_USER" -d "$DB_NAME" -Atc "SELECT COUNT(*) FROM information_schema.tables WHERE table_schema='public';" 2>/dev/null || echo "-1")

if [ "$TABLE_COUNT" = "0" ]; then
  echo "Database '$DB_NAME' appears empty (0 tables). Attempting restore from $DUMP_FILE using user '$DB_USER'..."
  if psql -U "$DB_USER" -d "$DB_NAME" -f "$DUMP_FILE"; then
    echo "Restore complete."
  else
    echo "Restore failed using user '$DB_USER'. If you need the dump restored, ensure the role '$DB_USER' exists and has access to the DB, or reinitialize the database."
  fi
elif [ "$TABLE_COUNT" -gt 0 ] 2>/dev/null; then
  echo "Database '$DB_NAME' already has $TABLE_COUNT tables; removing existing objects and restoring dump."
  # Attempt destructive schema recreation using the DB user; this may fail if the user lacks privileges.
  if psql -U "$DB_USER" -d "$DB_NAME" -c "DROP SCHEMA public CASCADE; CREATE SCHEMA public; GRANT ALL ON SCHEMA public TO \"$DB_USER\"; GRANT ALL ON SCHEMA public TO public;"; then
    echo "Schema recreated. Restoring dump..."
    if psql -U "$DB_USER" -d "$DB_NAME" -f "$DUMP_FILE"; then
      echo "Restore complete."
    else
      echo "Restore failed after schema recreation."
    fi
  else
    echo "Failed to drop/recreate schema as user '$DB_USER'. This likely means the user is not a superuser."
    echo "To force a destructive restore you must either run as a superuser or reinitialize the DB volume so POSTGRES_USER/POSTGRES_DB are applied."
  fi
else
  echo "Could not determine database table count (psql returned: $TABLE_COUNT). Skipping restore to avoid accidental data loss."
fi

exit 0
