"""LP-68: restaurants and offers from before branches keep working."""

from django.db import connection
from django.test import TransactionTestCase

from .test_migration_repair import LATEST, migrate_to

BEFORE = "0009_listing_kind_typical_contents"


class BranchesMigrationTests(TransactionTestCase):
    def setUp(self):
        migrate_to(BEFORE)
        self.addCleanup(migrate_to, LATEST)

    def rows(self, sql, params=()):
        with connection.cursor() as cursor:
            cursor.execute(sql, params)
            return cursor.fetchall()

    def add_shop(self, operator, name, address, area):
        return self.rows(
            "INSERT INTO marketplace_shop (operator_user_id, name, address, area, public_contact, category, "
            "approval_status, rejection_reason, created_at, updated_at) "
            "VALUES (%s, %s, %s, %s, '+961 1 234 567', 'Bakery', 'APPROVED', '', now(), now()) RETURNING id",
            (operator, name, address, area),
        )[0][0]

    def add_listing(self, shop_id, title):
        return self.rows(
            "INSERT INTO marketplace_listing (shop_id, title, description, category, total_quantity, pickup_address, "
            "status, terms_revision, created_at, updated_at, ingredients, allergens, kind, typical_contents) "
            "VALUES (%s, %s, '', '', 3, 'old address', 'PUBLISHED', 1, now(), now(), '', '', 'ITEM', '') RETURNING id",
            (shop_id, title),
        )[0][0]

    def test_each_restaurant_gets_branch_1_and_its_offers_move_there(self):
        cedar = self.add_shop(1, "Cedar", "12 Bliss Street", "Hamra")
        empty = self.add_shop(2, "New place", "", "")
        offers = [self.add_listing(cedar, "Bread"), self.add_listing(cedar, "Cake")]

        migrate_to(LATEST)

        branches = self.rows("SELECT shop_id, name, address, area, position FROM marketplace_branch ORDER BY shop_id")
        self.assertEqual(branches, [
            (cedar, "Hamra", "12 Bliss Street", "Hamra", 1),
            (empty, "Main branch", "", "", 1),
        ])
        linked = self.rows(
            "SELECT l.id, b.shop_id FROM marketplace_listing l JOIN marketplace_branch b ON b.id = l.branch_id "
            "ORDER BY l.id"
        )
        self.assertEqual(linked, [(offers[0], cedar), (offers[1], cedar)])
        # The pickup address customers already saw is unchanged.
        self.assertEqual(self.rows("SELECT DISTINCT pickup_address FROM marketplace_listing"), [("old address",)])

    def test_going_back_keeps_restaurants_and_offers(self):
        cedar = self.add_shop(1, "Cedar", "12 Bliss Street", "Hamra")
        self.add_listing(cedar, "Bread")
        migrate_to(LATEST)

        migrate_to(BEFORE)

        self.assertEqual(self.rows("SELECT count(*) FROM marketplace_shop"), [(1,)])
        self.assertEqual(self.rows("SELECT title FROM marketplace_listing"), [("Bread",)])

    def test_running_it_again_adds_no_second_branch(self):
        cedar = self.add_shop(1, "Cedar", "12 Bliss Street", "Hamra")
        migrate_to(LATEST)
        migrate_to(BEFORE)
        migrate_to(LATEST)

        self.assertEqual(self.rows("SELECT count(*) FROM marketplace_branch WHERE shop_id = %s", (cedar,)), [(1,)])
