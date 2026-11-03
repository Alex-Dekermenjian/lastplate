
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

Before using an existing database, verify that both
Marketplace tables exist.

Do not delete an existing database, fake migrations,
or manually rerun migration 0001 without checking its state.

## Integration requirement

Before merging LP-10/LP-11 into main, establish and test a
shared migration strategy for existing development databases.

The merged migration has been tested locally with both
models present, but cross-branch migration compatibility
still requires verification.
