#!/usr/bin/env bash
set -euo pipefail

MOODLE_ROOT="/var/www/html"
CONFIG="${MOODLE_ROOT}/config.php"
DATAROOT="${MOODLE_DATAROOT:-/var/moodledata}"
DB_HOST="${MOODLE_DB_HOST:-postgres}"
DB_PORT="${MOODLE_DB_PORT:-5432}"

wait_for_tcp() {
  echo "Waiting for PostgreSQL at ${DB_HOST}:${DB_PORT} ..."
  local i=0
  while (( i < 90 )); do
    if timeout 1 bash -c "exec 3<>/dev/tcp/${DB_HOST}/${DB_PORT}" 2>/dev/null; then
      echo "PostgreSQL port is open."
      return 0
    fi
    sleep 2
    (( i += 1 )) || true
  done
  echo "Timed out waiting for PostgreSQL." >&2
  return 1
}

# Returns 0 if Moodle's mdl_config table already exists in the DB.
moodle_db_initialised() {
  PGPASSWORD="${MOODLE_DB_PASSWORD}" psql \
    -h "${DB_HOST}" -p "${DB_PORT}" \
    -U "${MOODLE_DB_USER}" -d "${MOODLE_DB_NAME}" \
    -tAc "SELECT to_regclass('public.mdl_config') IS NOT NULL;" 2>/dev/null \
    | grep -q '^t$'
}

run_install() {
  echo "Running Moodle CLI install (non-interactive) ..."
  php "${MOODLE_ROOT}/admin/cli/install.php" \
    --non-interactive \
    --agree-license \
    --lang=en \
    --wwwroot="${MOODLE_BASE_URL}" \
    --dataroot="${DATAROOT}" \
    --dbtype=pgsql \
    --dbhost="${DB_HOST}" \
    --dbport="${DB_PORT}" \
    --dbname="${MOODLE_DB_NAME}" \
    --dbuser="${MOODLE_DB_USER}" \
    --dbpass="${MOODLE_DB_PASSWORD}" \
    --fullname="${MOODLE_SITE_FULLNAME}" \
    --shortname="${MOODLE_SITE_SHORTNAME}" \
    --adminuser="${MOODLE_ADMIN_USER}" \
    --adminpass="${MOODLE_ADMIN_PASSWORD}" \
    --adminemail="${MOODLE_ADMIN_EMAIL}" \
    --chmod=2770
  chown www-data:www-data "${CONFIG}" || true
  chown -R www-data:www-data "${MOODLE_ROOT}" || true
}

mkdir -p "${DATAROOT}"
chown www-data:www-data "${DATAROOT}"
chmod 0770 "${DATAROOT}" || true

wait_for_tcp

if moodle_db_initialised; then
  if [[ ! -f "${CONFIG}" ]]; then
    echo "DB initialised but config.php missing; cannot recover automatically." >&2
    echo "Drop the moodledata volume OR the moodle DB to start clean." >&2
    exit 1
  fi
  echo "config.php present and DB initialised; running pending upgrades (non-interactive) ..."
  php "${MOODLE_ROOT}/admin/cli/upgrade.php" --non-interactive
else
  if [[ -f "${CONFIG}" ]]; then
    echo "Stale config.php from a previous failed install; removing before reinstall."
    rm -f "${CONFIG}"
  fi
  run_install
fi

exec apache2-foreground
