"""Support for databases created before the LP-7 and LP-10 histories were merged.

Both branches shipped their own marketplace 0001_initial, and the merge combined
them into one file. A database that had already applied one of the originals has
0001_initial recorded but only that branch's table, and Django never re-runs a
recorded migration. See docs/migration-integration-notes.md.

The guard runs inside the next migration of the missing table's chain. It adds
no node to the migration graph, because a new dependency would make Django refuse
any database that had already applied the later migrations.
"""

from django.db import migrations


def ensure_table_exists(model_name):
    """An operation that creates the model's table, as of this point in the
    migration history, only if it is missing. Otherwise it does nothing."""

    def create_if_missing(apps, schema_editor):
        model = apps.get_model("marketplace", model_name)
        existing = schema_editor.connection.introspection.table_names()
        if model._meta.db_table not in existing:
            schema_editor.create_model(model)

    return migrations.RunPython(create_if_missing, migrations.RunPython.noop)
