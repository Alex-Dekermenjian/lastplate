from django.urls import reverse

from .models import Shop
from .test_shops import ShopAPITestCase, make_token

PROFILE = {
    "name": "Cedar Crumb Bakery",
    "address": "12 Bliss Street, Beirut",
    "area": "Hamra",
    "public_contact": "+961 1 234 567",
}


class ShopProfileTests(ShopAPITestCase):
    url = reverse("shop-me")

    def setUp(self):
        self.shop = Shop.objects.create(operator_user_id=7, name="Old name")
        self.other = Shop.objects.create(operator_user_id=8, name="Other shop", address="Elsewhere", area="Badaro",
                                         public_contact="other@example.test")

    def patch(self, data, user_id=7, role="SHOP_OPERATOR"):
        self.as_user(make_token(user_id=user_id, role=role))
        return self.client.patch(self.url, data, format="json")

    def test_operator_completes_own_profile(self):
        response = self.patch(PROFILE)

        self.assertEqual(response.status_code, 200)
        self.shop.refresh_from_db()
        for field, value in PROFILE.items():
            self.assertEqual(getattr(self.shop, field), value)
        self.assertTrue(response.data["profile_complete"])
        self.assertEqual(response.data["approval_status"], "PENDING")

    def test_partial_update_changes_only_sent_fields(self):
        self.patch(PROFILE)
        response = self.patch({"area": "  Achrafieh  "})

        self.assertEqual(response.status_code, 200)
        self.shop.refresh_from_db()
        self.assertEqual(self.shop.area, "Achrafieh")
        self.assertEqual(self.shop.address, PROFILE["address"])

    def test_edit_cannot_reach_another_operators_shop(self):
        self.patch(PROFILE, user_id=7)
        self.other.refresh_from_db()
        self.assertEqual((self.other.name, self.other.address, self.other.area), ("Other shop", "Elsewhere", "Badaro"))

        response = self.patch({"name": "Hijacked", "operator_user_id": 8, "id": self.other.id})
        self.assertEqual(response.status_code, 400)
        self.other.refresh_from_db()
        self.assertEqual(self.other.name, "Other shop")

    def test_rejects_approval_ownership_and_other_read_only_fields(self):
        for field, value in (
            ("approval_status", "APPROVED"),
            ("rejection_reason", "none"),
            ("operator_user_id", 99),
            ("id", 1),
            ("profile_complete", True),
            ("created_at", "2026-01-01T00:00:00Z"),
        ):
            with self.subTest(field=field):
                response = self.patch({"name": "Changed", field: value})
                self.assertEqual(response.status_code, 400)
                self.assertEqual(response.data[field], ["This field is not allowed."])
        self.shop.refresh_from_db()
        self.assertEqual((self.shop.name, self.shop.approval_status), ("Old name", "PENDING"))

    def test_validates_each_field(self):
        cases = [
            {"name": ""}, {"name": "   "}, {"name": "x" * 151},
            {"address": ""}, {"address": "x" * 256},
            {"area": " "}, {"area": "x" * 101},
            {"public_contact": ""}, {"public_contact": "x" * 151},
            {"public_contact": "call\x00me"},
        ]
        for data in cases:
            with self.subTest(data=data):
                response = self.patch(data)
                self.assertEqual(response.status_code, 400)
                self.assertIn(next(iter(data)), response.data)
        self.assertEqual(self.patch({}).status_code, 400)
        self.shop.refresh_from_db()
        self.assertEqual(self.shop.name, "Old name")

    def test_public_contact_must_be_a_usable_phone_number(self):
        for value in ("+961 1 234 567", "01-234567", "(03) 123 456", "+44 20 7946 0958", "961.3.123.456"):
            with self.subTest(accepted=value):
                response = self.patch({"public_contact": value})
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.data["public_contact"], value)
        for value in ("666", "12345", "123456", "0000000", "1234567890123456", "call me", "shop@example.test",
                      "961+1234567", "++961 1 234 567", "+961 1 234 567 ext 2"):
            with self.subTest(rejected=value):
                response = self.patch({"public_contact": value})
                self.assertEqual(response.status_code, 400)
                self.assertIn("public_contact", response.data)
        self.shop.refresh_from_db()
        self.assertEqual(self.shop.public_contact, "961.3.123.456")

    def test_rejects_values_too_short_to_be_useful(self):
        for data in ({"name": "A"}, {"address": "abc"}, {"area": "H"}):
            with self.subTest(data=data):
                response = self.patch(data)
                self.assertEqual(response.status_code, 400)
                self.assertIn(next(iter(data)), response.data)

    def test_edit_keeps_approval_decision_and_reason(self):
        self.shop.approval_status = Shop.ApprovalStatus.REJECTED
        self.shop.rejection_reason = "We could not confirm you represent this shop."
        self.shop.save()

        response = self.patch({"address": "New address"})

        self.assertEqual(response.data["approval_status"], "REJECTED")
        self.assertEqual(response.data["rejection_reason"], "We could not confirm you represent this shop.")
        self.shop.refresh_from_db()
        self.assertEqual(self.shop.approval_status, "REJECTED")

    def test_get_returns_profile_status_and_completeness(self):
        self.as_user(make_token(user_id=7))
        data = self.client.get(self.url).data
        self.assertEqual(data["name"], "Old name")
        self.assertEqual((data["address"], data["area"], data["public_contact"]), ("", "", ""))
        self.assertFalse(data["profile_complete"])
        self.assertEqual(data["rejection_reason"], "")

    def test_requires_operator_token_and_an_existing_shop(self):
        self.client.credentials()
        self.assertEqual(self.client.patch(self.url, PROFILE, format="json").status_code, 401)
        self.assertEqual(self.patch(PROFILE, role="CUSTOMER").status_code, 403)
        self.assertEqual(self.patch(PROFILE, user_id=9).status_code, 404)
        self.assertFalse(Shop.objects.filter(operator_user_id=9).exists())
