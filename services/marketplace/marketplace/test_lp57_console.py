"""LP-57: the admin API behind /console, and the shared review rules it
uses with the Django admin."""

from django.test import override_settings
from django.urls import reverse

from .models import Listing, Shop
from .shop_review import (
    CONFIRM_IDENTITY, INCOMPLETE_PROFILE, NO_RETURN_TO_PENDING, REASON_REQUIRED, review_errors,
)
from .test_browse import make_listing, make_shop
from .test_shop_review import PROFILE, REASON, AdminTestCase
from .test_shops import TEST_PUBLIC_KEY, ShopAPITestCase, make_token

ADMIN_ID = 99
Status = Shop.ApprovalStatus


@override_settings(JWT_PUBLIC_KEY=TEST_PUBLIC_KEY)
class ConsoleAPITestCase(ShopAPITestCase):
    def setUp(self):
        self.as_user(make_token(user_id=ADMIN_ID, role="ADMIN"))
        self.pending = Shop.objects.create(operator_user_id=1, **PROFILE)
        self.incomplete = Shop.objects.create(operator_user_id=2, name="Half Done Bakery")

    def review(self, shop, **data):
        return self.client.post(reverse("admin-shop-review", args=[shop.id]), data, format="json")


class AdminOnlyTests(ConsoleAPITestCase):
    def urls(self):
        return [
            ("get", reverse("admin-summary")),
            ("get", reverse("admin-shops")),
            ("get", reverse("admin-shop-detail", args=[self.pending.id])),
            ("post", reverse("admin-shop-review", args=[self.pending.id])),
        ]

    def test_customers_and_shop_operators_get_403(self):
        for role in ("CUSTOMER", "SHOP_OPERATOR"):
            # A staff flag in the token is not the Admin role.
            self.as_user(make_token(user_id=5, role=role, is_staff=True))
            for method, url in self.urls():
                with self.subTest(role=role, url=url):
                    data = {"decision": "APPROVED", "identity_confirmed": True}
                    response = getattr(self.client, method)(url, data, format="json")
                    self.assertEqual(response.status_code, 403)
        self.pending.refresh_from_db()
        self.assertEqual(self.pending.approval_status, Status.PENDING)

    def test_no_token_or_a_bad_token_gets_401(self):
        for credentials in ({}, {"HTTP_AUTHORIZATION": "Bearer not-a-token"}):
            self.client.credentials(**credentials)
            for method, url in self.urls():
                with self.subTest(credentials=credentials, url=url):
                    self.assertEqual(getattr(self.client, method)(url).status_code, 401)


class SummaryAndListTests(ConsoleAPITestCase):
    def test_summary_counts_shops_by_status_and_live_offers(self):
        approved = make_shop(operator_user_id=3)
        make_shop(operator_user_id=4, approval_status=Status.REJECTED, rejection_reason=REASON)
        make_listing(approved)
        make_listing(approved, status=Listing.Status.DRAFT)
        make_listing(approved, total_quantity=0)

        response = self.client.get(reverse("admin-summary"))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data, {
            "pending_shops": 2, "approved_shops": 1, "rejected_shops": 1, "live_offers": 1,
        })

    def test_list_filters_by_status_oldest_first(self):
        approved = make_shop(operator_user_id=3)

        pending = self.client.get(reverse("admin-shops"))
        self.assertEqual([s["id"] for s in pending.data], [self.pending.id, self.incomplete.id])

        approved_list = self.client.get(reverse("admin-shops"), {"status": "approved"})
        self.assertEqual([s["id"] for s in approved_list.data], [approved.id])

        self.assertEqual(self.client.get(reverse("admin-shops"), {"status": "nope"}).status_code, 400)

    def test_detail_shows_the_full_profile(self):
        response = self.client.get(reverse("admin-shop-detail", args=[self.pending.id]))

        self.assertEqual(response.status_code, 200)
        for field, value in PROFILE.items():
            self.assertEqual(response.data[field], value)
        self.assertIs(response.data["profile_complete"], True)
        self.assertEqual(self.client.get(reverse("admin-shop-detail", args=[999999])).status_code, 404)


class ReviewTests(ConsoleAPITestCase):
    def test_approve_records_the_admin_and_time(self):
        response = self.review(self.pending, decision="APPROVED", identity_confirmed=True)

        self.assertEqual(response.status_code, 200)
        self.pending.refresh_from_db()
        self.assertEqual(self.pending.approval_status, Status.APPROVED)
        self.assertEqual(self.pending.reviewed_by_user_id, ADMIN_ID)
        self.assertIsNone(self.pending.reviewed_by)
        self.assertIsNotNone(self.pending.reviewed_at)

    def test_approve_needs_the_confirmation_tick(self):
        response = self.review(self.pending, decision="APPROVED")

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.data["fields"], {"identity_confirmed": CONFIRM_IDENTITY})
        self.pending.refresh_from_db()
        self.assertEqual(self.pending.approval_status, Status.PENDING)

    def test_approve_needs_a_complete_profile(self):
        response = self.review(self.incomplete, decision="APPROVED", identity_confirmed=True)

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.data["fields"], {"approval_status": INCOMPLETE_PROFILE})

    def test_reject_needs_a_reason_and_stores_it_trimmed(self):
        for reason in ("", "   "):
            response = self.review(self.pending, decision="REJECTED", reason=reason)
            self.assertEqual(response.status_code, 400)
            self.assertEqual(response.data["fields"], {"rejection_reason": REASON_REQUIRED})

        response = self.review(self.pending, decision="REJECTED", reason=f"  {REASON}  ")

        self.assertEqual(response.status_code, 200)
        self.pending.refresh_from_db()
        self.assertEqual(self.pending.approval_status, Status.REJECTED)
        self.assertEqual(self.pending.rejection_reason, REASON)
        self.assertEqual(self.pending.reviewed_by_user_id, ADMIN_ID)

    def test_pending_is_not_a_decision(self):
        response = self.review(self.pending, decision="PENDING")

        self.assertEqual(response.status_code, 400)
        self.assertIn("decision", response.data)

    def test_forged_fields_are_rejected(self):
        response = self.review(self.pending, decision="APPROVED", identity_confirmed=True, reviewed_by_user_id=1)

        self.assertEqual(response.status_code, 400)
        self.pending.refresh_from_db()
        self.assertEqual(self.pending.approval_status, Status.PENDING)

    def test_approving_clears_an_old_rejection_reason(self):
        self.review(self.pending, decision="REJECTED", reason=REASON)
        response = self.review(self.pending, decision="APPROVED", identity_confirmed=True)

        self.assertEqual(response.status_code, 200)
        self.pending.refresh_from_db()
        self.assertEqual(self.pending.approval_status, Status.APPROVED)
        self.assertEqual(self.pending.rejection_reason, "")

    def test_unknown_shop_is_404(self):
        self.assertEqual(self.client.post(reverse("admin-shop-review", args=[999999]),
                                          {"decision": "REJECTED", "reason": REASON}, format="json").status_code, 404)


class SharedRulesTests(AdminTestCase):
    """The console and the Django admin use the same review_errors() rules."""

    def test_rules(self):
        shop = Shop(**PROFILE)
        self.assertEqual(review_errors(shop, Status.APPROVED, identity_confirmed=True), {})
        self.assertEqual(review_errors(shop, Status.APPROVED), {"identity_confirmed": CONFIRM_IDENTITY})
        self.assertEqual(review_errors(Shop(name="x"), Status.APPROVED, identity_confirmed=True),
                         {"approval_status": INCOMPLETE_PROFILE})
        self.assertEqual(review_errors(shop, Status.REJECTED, "  "), {"rejection_reason": REASON_REQUIRED})
        shop.approval_status = Status.REJECTED
        self.assertEqual(review_errors(shop, Status.PENDING), {"approval_status": NO_RETURN_TO_PENDING})

    def test_django_admin_shows_the_shared_messages(self):
        response = self.decide(approval_status="APPROVED", rejection_reason="")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.errors(response)["identity_confirmed"], [CONFIRM_IDENTITY])
