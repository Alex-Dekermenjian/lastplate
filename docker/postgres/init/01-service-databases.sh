#!/bin/sh
# Creates one database and one login role per service. The postgres image runs
# this only when the lastplate-dev_pgdata volume is first initialised; it never
# touches an existing volume.
set -e

create_service_database() {
    name="$1"
    password="$2"
    psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname postgres \
        -v name="$name" -v password="$password" <<'SQL'
-- CREATEDB lets Django create this service's separate test database.
CREATE ROLE :"name" WITH LOGIN CREATEDB PASSWORD :'password';
CREATE DATABASE :"name" OWNER :"name" ENCODING 'UTF8' TEMPLATE template0;
-- Only the owning service (and the admin role) may connect.
REVOKE ALL ON DATABASE :"name" FROM PUBLIC;
SQL
}

create_service_database lastplate_accounts "$ACCOUNTS_DB_PASSWORD"
create_service_database lastplate_marketplace "$MARKETPLACE_DB_PASSWORD"
create_service_database lastplate_reservations "$RESERVATIONS_DB_PASSWORD"
create_service_database lastplate_web "$WEB_DB_PASSWORD"
