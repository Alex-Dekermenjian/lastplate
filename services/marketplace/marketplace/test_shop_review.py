from django.conf import settings
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.db.models import ProtectedError
from django.test import TestCase
from django.urls import reverse

from .models import Shop
from .test_shops import ShopAPITestCase, make_token

User = get_user_model()
PASSWORD = "Local-test-only!582Orbit"
REASON = "We could not confirm you represent this shop."
PROFILE = {"name": "Cedar Crumb Bakery", "address": "12 Bliss Street, Beirut", "area": "Hamra",
           "public_contact": "+961 1 234 567"}


def make_staff(username, *codenames):
    user = User.objects.create_user(username=username, password=PASSWORD, is_staff=True)
    user.user_permissions.set(Permission.objects.filter(content_type__app_label="marketplace", codename__in=codenames))
    return user


class AdminTestCase(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.reviewer = make_staff("reviewer", "review_shop")
        cls.shop = Shop.objects.create(operator_user_id=7, **PROFILE)
        cls.url = reverse("admin:marketplace_shop_change", args=[cls.shop.id])

    def decide(self, user=None, **data):
        self.client.force_login(user or self.reviewer)
        return self.client.post(self.url, data)

    def errors(self, response):
        return response.context["adminform"].form.errors


class ShopReviewTests(AdminTestCase):
    def test_pending_to_approved_records_reviewer_and_time(self):
        response = self.decide(approval_status="APPROVED", rejection_reason="", identity_confirmed="on")

        self.assertEqual(response.status_code, 302)
        self.shop.refresh_from_db()
        self.assertEqual(self.shop.approval_status, "APPROVED")
        self.assertEqual(self.shop.reviewed_by, self.reviewer)
        self.assertIsNotNone(self.shop.reviewed_at)

    def test_pending_to_rejected_records_reason_reviewer_and_time(self):
        response = self.decide(approval_status="REJECTED", rejection_reason=f"  {REASON}  ")

        self.assertEqual(response.status_code, 302)
        self.shop.refresh_from_db()
        self.assertEqual(self.shop.approval_status, "REJECTED")
        self.assertEqual(self.shop.rejection_reason, REASON)
        self.assertEqual(self.shop.reviewed_by, self.reviewer)
        self.assertIsNotNone(self.shop.reviewed_at)

    def test_rejecting_requires_a_reason(self):
        for reason in ("", "   "):
            with self.subTest(reason=reason):
                response = self.decide(approval_status="REJECTED", rejection_reason=reason)
                self.assertEqual(response.status_code, 200)
                self.assertIn("rejection_reason", self.errors(response))
        self.shop.refresh_from_db()
        self.assertEqual(self.shop.approval_status, "PENDING")
        self.assertIsNone(self.shop.reviewed_at)

    def test_approving_requires_a_complete_profile(self):
        for field in ("address", "area", "public_contact"):
            with self.subTest(missing=field):
                Shop.objects.filter(pk=self.shop.pk).update(**{**PROFILE, field: ""})
                response = self.decide(approval_status="APPROVED", rejection_reason="", identity_confirmed="on")
                self.assertEqual(response.status_code, 200)
                self.assertIn("approval_status", self.errors(response))
                self.shop.refresh_from_db()
                self.assertEqual(self.shop.approval_status, "PENDING")

    def test_approving_requires_confirming_the_applicant(self):
        response = self.decide(approval_status="APPROVED", rejection_reason="")
        self.assertEqual(response.status_code, 200)
        self.assertIn("identity_confirmed", self.errors(response))
        self.shop.refresh_from_db()
        self.assertEqual(self.shop.approval_status, "PENDING")

    def test_rejected_shop_can_be_approved_later_and_the_reason_is_cleared(self):
        self.decide(approval_status="REJECTED", rejection_reason=REASON)
        self.decide(approval_status="APPROVED", rejection_reason=REASON, identity_confirmed="on")
        self.shop.refresh_from_db()
        self.assertEqual(self.shop.approval_status, "APPROVED")
        self.assertEqual(self.shop.rejection_reason, "")

    def test_reviewed_shop_cannot_return_to_pending(self):
        self.decide(approval_status="REJECTED", rejection_reason=REASON)
        response = self.decide(approval_status="PENDING", rejection_reason="")
        self.assertEqual(response.status_code, 200)
        self.assertIn("approval_status", self.errors(response))
        self.shop.refresh_from_db()
        self.assertEqual(self.shop.approval_status, "REJECTED")

    def test_saving_without_a_decision_records_no_review(self):
        response = self.decide(approval_status="PENDING", rejection_reason="")
        self.assertEqual(response.status_code, 302)
        self.shop.refresh_from_db()
        self.assertIsNone(self.shop.reviewed_by)
        self.assertIsNone(self.shop.reviewed_at)

    def test_admin_cannot_change_shop_details_ownership_or_review_record(self):
        other = make_staff("other", "review_shop")
        self.decide(
            approval_status="REJECTED", rejection_reason=REASON, name="Changed", address="Changed", area="Changed",
            public_contact="Changed", operator_user_id=99, reviewed_by=other.id, reviewed_at="2020-01-01 00:00:00",
        )
        self.shop.refresh_from_db()
        for field, value in PROFILE.items():
            self.assertEqual(getattr(self.shop, field), value)
        self.assertEqual(self.shop.operator_user_id, 7)
        self.assertEqual(self.shop.reviewed_by, self.reviewer)
        self.assertGreater(self.shop.reviewed_at.year, 2020)

    def test_shops_cannot_be_added_or_deleted_in_the_admin(self):
        self.client.force_login(User.objects.create_superuser(username="root", password=PASSWORD))
        self.assertEqual(self.client.get(reverse("admin:marketplace_shop_add")).status_code, 403)
        delete_url = reverse("admin:marketplace_shop_delete", args=[self.shop.id])
        self.assertEqual(self.client.post(delete_url, {"post": "yes"}).status_code, 403)
        self.assertTrue(Shop.objects.filter(pk=self.shop.pk).exists())

    def test_superuser_can_review(self):
        root = User.objects.create_superuser(username="root", password=PASSWORD)
        self.decide(root, approval_status="REJECTED", rejection_reason=REASON)
        self.shop.refresh_from_db()
        self.assertEqual(self.shop.reviewed_by, root)

    def test_account_that_reviewed_a_shop_cannot_be_deleted(self):
        self.decide(approval_status="REJECTED", rejection_reason=REASON)
        with self.assertRaises(ProtectedError):
            self.reviewer.delete()


class AdminAccessTests(AdminTestCase):
    def assert_unchanged(self):
        self.shop.refresh_from_db()
        self.assertEqual(self.shop.approval_status, "PENDING")
        self.assertIsNone(self.shop.reviewed_by)

    def test_anonymous_visitor_is_sent_to_login(self):
        response = self.client.post(self.url, {"approval_status": "APPROVED", "identity_confirmed": "on"})
        self.assertEqual(response.status_code, 302)
        self.assertIn(reverse("admin:login"), response["Location"])
        self.assert_unchanged()

    def test_shop_operator_token_gives_no_admin_access(self):
        response = self.client.post(
            self.url, {"approval_status": "APPROVED", "identity_confirmed": "on"},
            headers={"Authorization": f"Bearer {make_token(user_id=7)}"},
        )
        self.assertEqual(response.status_code, 302)
        self.assertIn(reverse("admin:login"), response["Location"])
        self.assert_unchanged()

    def test_non_staff_account_cannot_sign_in_to_the_admin(self):
        User.objects.create_user(username="ordinary", password=PASSWORD)
        response = self.client.post(reverse("admin:login"), {"username": "ordinary", "password": PASSWORD})
        self.assertEqual(response.status_code, 200)
        self.assertNotIn(settings.SESSION_COOKIE_NAME, response.cookies)

    def test_staff_without_review_permission_cannot_see_or_change_shops(self):
        staff = make_staff("staff")
        response = self.decide(staff, approval_status="APPROVED", identity_confirmed="on")
        self.assertEqual(response.status_code, 403)
        self.assertEqual(self.client.get(self.url).status_code, 403)
        self.assert_unchanged()

    def test_stock_change_permission_alone_does_not_allow_review(self):
        staff = make_staff("staff", "change_shop")
        self.assertEqual(self.decide(staff, approval_status="APPROVED", identity_confirmed="on").status_code, 403)
        self.assert_unchanged()

    def test_view_only_staff_can_read_but_not_change(self):
        staff = make_staff("viewer", "view_shop")
        self.client.force_login(staff)
        page = self.client.get(self.url)
        self.assertEqual(page.status_code, 200)
        self.assertNotContains(page, 'name="approval_status"')
        self.assertEqual(self.decide(staff, approval_status="APPROVED", identity_confirmed="on").status_code, 403)
        self.assert_unchanged()

    def test_reviewer_cannot_manage_admin_accounts(self):
        self.client.force_login(self.reviewer)
        self.assertEqual(self.client.get(reverse("admin:auth_user_changelist")).status_code, 403)

    def test_admin_cookies_do_not_share_names_with_the_web_frontend(self):
        self.assertNotEqual(settings.SESSION_COOKIE_NAME, "sessionid")
        self.assertNotEqual(settings.CSRF_COOKIE_NAME, "csrftoken")


class ReviewThroughAPITests(ShopAPITestCase):
    """The operator's API can see a decision but never make one."""

    def setUp(self):
        self.shop = Shop.objects.create(operator_user_id=7, **PROFILE)
        self.as_user(make_token(user_id=7))

    def test_operator_cannot_set_any_review_field(self):
        for field, value in (
            ("approval_status", "APPROVED"), ("rejection_reason", "x"), ("reviewed_by", 1),
            ("reviewed_at", "2026-01-01T00:00:00Z"), ("identity_confirmed", True),
        ):
            with self.subTest(field=field):
                response = self.client.patch(self.me_url, {field: value}, format="json")
                self.assertEqual(response.status_code, 400)
                self.assertEqual(response.data[field], ["This field is not allowed."])
        self.shop.refresh_from_db()
        self.assertEqual(self.shop.approval_status, "PENDING")
        self.assertIsNone(self.shop.reviewed_at)

    def test_staff_claim_in_a_token_grants_nothing_extra(self):
        self.as_user(make_token(user_id=7, is_staff=True))
        response = self.client.patch(self.me_url, {"approval_status": "APPROVED"}, format="json")
        self.assertEqual(response.status_code, 400)
        self.shop.refresh_from_db()
        self.assertEqual(self.shop.approval_status, "PENDING")

    def test_admin_session_is_not_accepted_on_the_api(self):
        self.client.credentials()
        self.client.force_login(User.objects.create_superuser(username="root", password=PASSWORD))
        self.assertEqual(self.client.patch(self.me_url, {"name": "Changed"}, format="json").status_code, 401)

    def test_owner_sees_decision_and_reason_but_not_the_reviewer(self):
        reviewer = make_staff("reviewer", "review_shop")
        Shop.objects.filter(pk=self.shop.pk).update(approval_status="REJECTED", rejection_reason=REASON, reviewed_by=reviewer)
        data = self.client.get(self.me_url).data
        self.assertEqual((data["approval_status"], data["rejection_reason"]), ("REJECTED", REASON))
        self.assertNotIn("reviewed_by", data)

    def test_profile_edit_leaves_the_review_record_alone(self):
        reviewer = make_staff("reviewer", "review_shop")
        Shop.objects.filter(pk=self.shop.pk).update(approval_status="APPROVED", reviewed_by=reviewer)
        self.assertEqual(self.client.patch(self.me_url, {"area": "Badaro"}, format="json").status_code, 200)
        self.shop.refresh_from_db()
        self.assertEqual((self.shop.approval_status, self.shop.reviewed_by), ("APPROVED", reviewer))
