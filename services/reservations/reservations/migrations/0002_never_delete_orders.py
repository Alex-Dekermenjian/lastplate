"""LP-17: the database itself refuses to delete orders, items or history.

The models already refuse, but raw SQL or a future bug could skip them. These
PostgreSQL triggers also refuse any edit to an order line (a copy of the offer
when the order was placed) or a history row. Orders themselves can still be
updated, because their status changes; that goes through change_status.
"""

from django.db import migrations

TABLES_NEVER_DELETED = ("reservations_order", "reservations_orderline", "reservations_orderevent")
TABLES_NEVER_EDITED = ("reservations_orderline", "reservations_orderevent")

FUNCTION = """
CREATE OR REPLACE FUNCTION reservations_refuse_change() RETURNS trigger AS $$
BEGIN
    RAISE EXCEPTION 'LastPlate: % on % is not allowed (LP-17: orders and their history are kept)',
        TG_OP, TG_TABLE_NAME USING ERRCODE = 'restrict_violation';
END;
$$ LANGUAGE plpgsql;
"""


def _create_sql():
    sql = [FUNCTION]
    for table in TABLES_NEVER_DELETED:
        sql.append(
            f"CREATE TRIGGER {table}_no_delete BEFORE DELETE ON {table} "
            f"FOR EACH ROW EXECUTE FUNCTION reservations_refuse_change();"
        )
    for table in TABLES_NEVER_EDITED:
        sql.append(
            f"CREATE TRIGGER {table}_no_update BEFORE UPDATE ON {table} "
            f"FOR EACH ROW EXECUTE FUNCTION reservations_refuse_change();"
        )
    return "\n".join(sql)


def _drop_sql():
    sql = [f"DROP TRIGGER IF EXISTS {table}_no_update ON {table};" for table in TABLES_NEVER_EDITED]
    sql += [f"DROP TRIGGER IF EXISTS {table}_no_delete ON {table};" for table in TABLES_NEVER_DELETED]
    sql.append("DROP FUNCTION IF EXISTS reservations_refuse_change();")
    return "\n".join(sql)


class Migration(migrations.Migration):
    dependencies = [("reservations", "0001_order_tables")]

    operations = [migrations.RunSQL(sql=_create_sql(), reverse_sql=_drop_sql())]
