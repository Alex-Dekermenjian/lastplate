
# Marketplace Migration Integration Notes

## Background

LP-7 and LP-10 were developed on separate branches.

Both branches independently created a migration named
`marketplace/migrations/0001_initial.py`:

- LP-7 created the Shop model.
- LP-10 created the Listing model.

During integration, the initial migration was combined to
include both models.

## Fresh database setup

On a fresh Marketplace database, running Django migrations
should create both:

- marketplace_shop
- marketplace_listing

## Existing development databases

An existing database may already have migration 0001
recorded as applied while containing only one of those
tables.

Django does not automatically reapply an altered migration.

Do not delete an existing database, fake migrations,
or manually rerun migration 0001.

## The repair

`marketplace/migration_repairs.py` adds a guard at the start of the next
migration in each table's chain: `0002_listing_allergens_...` creates
`marketplace_listing` if it is missing, and `0002_shop_profile` does the same for
`marketplace_shop`. On a database that already has the table, the guard does
nothing. No manual step is needed: `migrate` repairs the database.

The guard sits inside existing migrations on purpose:

- A migration added at the end runs too late, because `0002_listing_...` fails first.
- A new migration placed before it needs a new dependency. Django then refuses every
  database that already applied `0002_listing_...`
  (`InconsistentMigrationHistory`).
- Faking `0001_initial` or `0002_listing_...` would leave the table missing.

A migration is never edited to change the final schema. The guards only recreate
what `0001_initial` already declares, so all histories end with the same schema.

## Which databases this covers

| Database | What `migrate` does |
| --- | --- |
| New | Runs everything; the guards do nothing |
| `0001_initial` recorded, no listing table (LP-7 to LP-9 history) | Creates the table, then runs the rest; existing data is untouched |
| `0001_initial` recorded, no shop table (LP-10 and LP-11 history) | Creates the table, then runs the rest; existing data is untouched |
| Already on `0006_stockadjustment` | Nothing to run |

`marketplace/test_migration_repair.py` tests each of these. Back up the database
before upgrading one you care about:

```powershell
docker compose --env-file compose.env exec -T postgres pg_dump -U lastplate_admin -Fc lastplate_marketplace > marketplace.dump
```
