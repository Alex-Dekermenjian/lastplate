"""LP-45: a rejected restaurant fixes its details and asks for review again."""

from django.urls import reverse

from .models import Shop
from .shop_review import review_shop
from .test_browse import OPERATOR_ID, make_shop
from .test_shops import ShopAPITestCase, make_token

URL = reverse("shop-review-again")
ADMIN_ID = 99
Status = Shop.ApprovalStatus


class ReviewAgainTests(ShopAPITestCase):

    def setUp(self):
        self.shop = make_shop(approval_status=Status.REJECTED, rejection_reason="Add a real phone number.")
        self.as_user(make_token(user_id=OPERATOR_ID))

    def ask(self, data=None):
        return self.client.post(URL, data or {}, format="json")

    def test_rejected_goes_back_to_pending_and_the_reason_is_kept(self):
        response = self.ask({"note": "  I added our real phone number.  "})

        self.assertEqual(response.status_code, 200, response.json())
        self.assertEqual(response.json()["approval_status"], "PENDING")
        self.assertEqual(response.json()["review_requests"], 1)
        self.shop.refresh_from_db()
        self.assertEqual(self.shop.approval_status, Status.PENDING)
        self.assertEqual(self.shop.rejection_reason, "")
        self.assertEqual(self.shop.last_rejection_reason, "Add a real phone number.")
        self.assertEqual(self.shop.review_request_note, "I added our real phone number.")
        self.assertIsNotNone(self.shop.review_requested_at)

    def test_the_note_is_optional(self):
        self.assertEqual(self.ask().status_code, 200)

    def test_only_a_rejected_restaurant_can_ask(self):
        for current in (Status.PENDING, Status.APPROVED):
            with self.subTest(current=current):
                Shop.objects.filter(pk=self.shop.pk).update(approval_status=current, rejection_reason="")

                response = self.ask()

                self.assertEqual(response.status_code, 409)
                self.assertEqual(response.json()["code"], "not_rejected")
                self.shop.refresh_from_db()
                self.assertEqual(self.shop.approval_status, current)

    def test_an_owner_can_never_approve_their_own_restaurant(self):
        for data in ({"approval_status": "APPROVED"}, {"decision": "APPROVED"}, {"note": "hi", "status": "APPROVED"}):
            with self.subTest(data=data):
                response = self.ask(data)

                self.assertEqual(response.status_code, 400)
                self.shop.refresh_from_db()
                self.assertEqual(self.shop.approval_status, Status.REJECTED)
        self.ask()
        self.shop.refresh_from_db()
        self.assertEqual(self.shop.approval_status, Status.PENDING)

    def test_a_long_note_is_refused(self):
        self.assertEqual(self.ask({"note": "x" * 1001}).status_code, 400)

    def test_customers_and_signed_out_visitors_are_refused(self):
        self.client.credentials()
        self.assertEqual(self.ask().status_code, 401)
        self.as_user(make_token(user_id=OPERATOR_ID, role="CUSTOMER"))
        self.assertEqual(self.ask().status_code, 403)

    def test_an_owner_without_a_restaurant(self):
        self.as_user(make_token(user_id=OPERATOR_ID + 1))

        self.assertEqual(self.ask().status_code, 404)

    def test_the_admin_can_review_it_again_and_each_request_is_counted(self):
        self.ask()
        review_shop(self.shop.pk, Status.REJECTED, "Still no phone.", reviewer_user_id=ADMIN_ID)
        self.ask({"note": "Now really."})

        self.shop.refresh_from_db()
        self.assertEqual(self.shop.review_requests, 2)
        self.assertEqual(self.shop.last_rejection_reason, "Still no phone.")
        review_shop(self.shop.pk, Status.APPROVED, identity_confirmed=True, reviewer_user_id=ADMIN_ID)
        self.shop.refresh_from_db()
        self.assertEqual(self.shop.approval_status, Status.APPROVED)


class AdminSeesRequestsSentAgainTests(ShopAPITestCase):

    def setUp(self):
        self.first_time = make_shop(operator_user_id=1, approval_status=Status.PENDING)
        self.again = make_shop(operator_user_id=2, approval_status=Status.REJECTED, rejection_reason="Blurry photo.")
        self.as_user(make_token(user_id=2))
        self.client.post(URL, {"note": "New photo."}, format="json")
        self.as_user(make_token(user_id=ADMIN_ID, role="ADMIN"))

    def test_the_pending_list_can_show_only_requests_sent_again(self):
        everyone = self.client.get(reverse("admin-shops")).json()
        again = self.client.get(reverse("admin-shops"), {"resubmitted": "1"}).json()

        self.assertEqual({shop["id"] for shop in everyone}, {self.first_time.pk, self.again.pk})
        self.assertEqual([shop["id"] for shop in again], [self.again.pk])

    def test_the_admin_sees_the_earlier_reason_and_the_note(self):
        shop = self.client.get(reverse("admin-shop-detail", args=[self.again.pk])).json()

        self.assertEqual(shop["review_requests"], 1)
        self.assertEqual(shop["last_rejection_reason"], "Blurry photo.")
        self.assertEqual(shop["review_request_note"], "New photo.")
        self.assertIsNotNone(shop["review_requested_at"])
