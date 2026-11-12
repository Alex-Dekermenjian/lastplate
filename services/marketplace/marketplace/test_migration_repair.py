"""Migration histories a real database can have. See docs/migration-integration-notes.md."""

from pathlib import Path

from django.conf import settings
from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.db.migrations.loader import MigrationLoader
from django.test import SimpleTestCase, TransactionTestCase

APP = "marketplace"
LISTING_0002 = "0002_listing_allergens_listing_ingredients_listing_photo"
LATEST = "0012_allocation"


def migrate_to(*names):
    executor = MigrationExecutor(connection)
    executor.loader.build_graph()
    executor.migrate([(APP, name) for name in names] or [(APP, None)])


def tables():
    return set(connection.introspection.table_names())


def columns(table):
    with connection.cursor() as cursor:
        return {c.name for c in connection.introspection.get_table_description(cursor, table)}


def drop_table(table):
    with connection.schema_editor() as editor:
        editor.execute(f"DROP TABLE {editor.quote_name(table)}")


class MigrationHistoryTests(TransactionTestCase):
    def setUp(self):
        migrate_to()  # no Marketplace tables
        self.addCleanup(migrate_to, LATEST)

    def assert_final_schema(self):
        self.assertEqual(
            MigrationExecutor(connection).migration_plan([(APP, LATEST)]),
            [],
            "every migration should be recorded",
        )
        MigrationLoader(connection).check_consistent_history(connection)
        self.assertLessEqual(
            {"marketplace_shop", "marketplace_listing", "marketplace_stockadjustment"},
            tables(),
        )
        self.assertLessEqual(
            {"photo", "ingredients", "allergens", "kind", "typical_contents"},
            columns("marketplace_listing"),
        )
        self.assertLessEqual(
            {
                "address", "reviewed_by_id", "reviewed_by_user_id", "rejection_reason",
                "category", "photo", "logo", "conditions_accepted_at",
            },
            columns("marketplace_shop"),
        )

    def test_fresh_database(self):
        migrate_to(LATEST)
        self.assert_final_schema()

    def test_database_with_0001_recorded_but_no_listing_table(self):
        """The LP-7 to LP-9 history: shops exist and listings were never created."""
        migrate_to("0003_shop_review")
        drop_table("marketplace_listing")
        with connection.cursor() as cursor:
            cursor.execute(
                "INSERT INTO marketplace_shop (operator_user_id, name, address, area, public_contact, "
                "approval_status, rejection_reason, created_at, updated_at) "
                "VALUES (41, 'Kept shop', '1 Kept Street', 'Hamra', '+96112345', 'REJECTED', 'Kept reason', now(), now())"
            )

        migrate_to(LATEST)

        self.assert_final_schema()
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT operator_user_id, name, address, approval_status, rejection_reason FROM marketplace_shop"
            )
            self.assertEqual(cursor.fetchall(), [(41, "Kept shop", "1 Kept Street", "REJECTED", "Kept reason")])
            cursor.execute("SELECT count(*) FROM marketplace_listing")
            self.assertEqual(cursor.fetchone(), (0,))

    def test_database_with_0001_recorded_but_no_shop_table(self):
        """The LP-10 and LP-11 history: listings exist and shops were never created."""
        migrate_to("0003_alter_listing_photo")
        drop_table("marketplace_shop")
        with connection.cursor() as cursor:
            cursor.execute(
                "INSERT INTO marketplace_listing (shop_id, title, description, category, total_quantity, "
                "pickup_address, status, terms_revision, created_at, updated_at, ingredients, allergens) "
                "VALUES (9, 'Kept offer', '', '', 0, '', 'DRAFT', 1, now(), now(), '', '')"
            )

        migrate_to(LATEST)

        self.assert_final_schema()
        with connection.cursor() as cursor:
            cursor.execute("SELECT title FROM marketplace_listing")
            self.assertEqual(cursor.fetchall(), [("Kept offer",)])

    def test_database_already_on_the_newer_chain_has_nothing_to_run(self):
        migrate_to(LATEST)
        with connection.cursor() as cursor:
            cursor.execute(
                "INSERT INTO marketplace_listing (shop_id, title, description, category, total_quantity, "
                "pickup_address, status, terms_revision, created_at, updated_at, ingredients, allergens) "
                "VALUES (9, 'Kept offer', '', '', 0, '', 'DRAFT', 1, now(), now(), '', '')"
            )
        self.assertEqual(MigrationExecutor(connection).migration_plan([(APP, LATEST)]), [])

        migrate_to(LATEST)

        with connection.cursor() as cursor:
            cursor.execute("SELECT title FROM marketplace_listing")
            self.assertEqual(cursor.fetchall(), [("Kept offer",)])

    def test_migrations_reverse_cleanly(self):
        migrate_to(LATEST)
        migrate_to()
        self.assertFalse({"marketplace_shop", "marketplace_listing"} & tables())


class MigrationGraphTests(SimpleTestCase):
    def test_guarded_migrations_keep_their_original_dependency(self):
        """A new dependency here would make Django refuse any database that has
        already applied these migrations (InconsistentMigrationHistory)."""
        graph = MigrationLoader(None).graph
        for name in (LISTING_0002, "0002_shop_profile"):
            with self.subTest(migration=name):
                self.assertEqual(graph.node_map[(APP, name)].key, (APP, name))
                self.assertEqual(
                    {parent.key for parent in graph.node_map[(APP, name)].parents},
                    {(APP, "0001_initial")},
                )

    def test_one_leaf_migration(self):
        self.assertEqual(MigrationLoader(None).graph.leaf_nodes(APP), [(APP, LATEST)])


class TestMediaIsolationTests(SimpleTestCase):
    def test_tests_never_use_the_real_media_folder(self):
        self.assertNotEqual(Path(settings.MEDIA_ROOT), settings.BASE_DIR / "media")
