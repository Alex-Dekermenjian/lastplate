
from unittest.mock import patch

from django.test import TestCase
from django.urls import reverse
from rest_framework.test import APIClient

from .authentication import TokenUser
from .models import Listing, Shop


class CreateListingAPITests(TestCase):

    def setUp(self):
        self.client = APIClient()
        self.url = reverse("listings")
        self.payload = {
            "title": "Croissant Box",
            "description": "Fresh pastries",
            "category": "Bakery",
        }

    def authenticate(self, user_id, role):
        user = TokenUser(
            id=user_id,
            role=role,
            is_staff=False,
        )
        self.client.force_authenticate(user=user)

    def test_operator_can_create_draft(self):
        shop = Shop.objects.create(
            operator_user_id=10,
            name="Cedar Bakery",
        )
        self.authenticate(10, "SHOP_OPERATOR")

        response = self.client.post(
            self.url, self.payload, format="json"
        )

        self.assertEqual(response.status_code, 201)
        listing = Listing.objects.get()
        self.assertEqual(listing.shop_id, shop.id)
        self.assertEqual(listing.status, Listing.Status.DRAFT)

    def test_unauthenticated_user_cannot_create(self):
        response = self.client.post(
            self.url, self.payload, format="json"
        )

        self.assertIn(response.status_code, [401, 403])
        self.assertEqual(Listing.objects.count(), 0)

    def test_customer_cannot_create(self):
        self.authenticate(20, "CUSTOMER")

        response = self.client.post(
            self.url, self.payload, format="json"
        )

        self.assertEqual(response.status_code, 403)
        self.assertEqual(Listing.objects.count(), 0)

    def test_operator_without_shop_cannot_create(self):
        self.authenticate(30, "SHOP_OPERATOR")

        response = self.client.post(
            self.url, self.payload, format="json"
        )

        self.assertEqual(response.status_code, 403)
        self.assertEqual(Listing.objects.count(), 0)

    def test_client_cannot_choose_shop_or_status(self):
        Shop.objects.create(
            operator_user_id=10,
            name="Cedar Bakery",
        )
        self.authenticate(10, "SHOP_OPERATOR")

        response = self.client.post(
            self.url,
            {
                **self.payload,
                "shop_id": 999,
                "status": "PUBLISHED",
            },
            format="json",
        )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(Listing.objects.count(), 0)

class EditListingAPITests(TestCase):

    def setUp(self):
        self.client = APIClient()

        self.shop = Shop.objects.create(
            operator_user_id=10,
            name="Cedar Bakery",
        )

        self.other_shop = Shop.objects.create(
            operator_user_id=20,
            name="Other Bakery",
        )

        self.listing = Listing.objects.create(
            shop_id=self.shop.id,
            title="Croissant Box",
        )

        self.url = reverse(
            "listing_detail",
            kwargs={"pk": self.listing.pk},
        )

    def authenticate(self, user_id, role="SHOP_OPERATOR"):
        self.client.force_authenticate(
            user=TokenUser(
                id=user_id,
                role=role,
                is_staff=False,
            )
        )

    def test_owner_can_edit_draft(self):
        self.authenticate(10)

        response = self.client.patch(
            self.url,
            {"title": "Updated Croissant Box"},
            format="json",
        )

        self.assertEqual(response.status_code, 200)
        self.listing.refresh_from_db()
        self.assertEqual(
            self.listing.title,
            "Updated Croissant Box",
        )

    def test_other_shop_cannot_edit(self):
        self.authenticate(20)

        response = self.client.patch(
            self.url,
            {"title": "Stolen Listing"},
            format="json",
        )

        self.assertEqual(response.status_code, 404)
        self.listing.refresh_from_db()
        self.assertEqual(self.listing.title, "Croissant Box")

    def test_customer_cannot_edit(self):
        self.authenticate(30, role="CUSTOMER")

        response = self.client.patch(
            self.url,
            {"title": "Changed"},
            format="json",
        )

        self.assertEqual(response.status_code, 403)

    def test_unauthenticated_user_cannot_edit(self):
        response = self.client.patch(
            self.url,
            {"title": "Changed"},
            format="json",
        )

        self.assertIn(response.status_code, [401, 403])

    def test_protected_fields_cannot_be_changed(self):
        self.authenticate(10)

        response = self.client.patch(
            self.url,
            {"shop_id": self.other_shop.id},
            format="json",
        )

        self.assertEqual(response.status_code, 400)
        self.listing.refresh_from_db()
        self.assertEqual(self.listing.shop_id, self.shop.id)

    def test_published_listing_cannot_be_edited(self):
        self.listing.status = Listing.Status.PUBLISHED
        self.listing.save()
        self.authenticate(10)

        response = self.client.patch(
            self.url,
            {"title": "Changed"},
            format="json",
        )

        self.assertEqual(response.status_code, 404)

class PublishListingAPITests(TestCase):

    def setUp(self):
        self.client = APIClient()

        self.shop = Shop.objects.create(
            operator_user_id=10,
            name="Cedar Bakery",
        )

        self.other_shop = Shop.objects.create(
            operator_user_id=20,
            name="Other Bakery",
        )

        self.listing = Listing.objects.create(
            shop_id=self.shop.id,
            title="Croissant Box",
        )

        self.url = reverse(
            "publish_listing",
            kwargs={"pk": self.listing.pk},
        )

    def authenticate(self, user_id, role="SHOP_OPERATOR"):
        self.client.force_authenticate(
            user=TokenUser(
                id=user_id,
                role=role,
                is_staff=False,
            )
        )

    def test_unauthenticated_user_cannot_publish(self):
        response = self.client.post(self.url, {}, format="json")

        self.assertIn(response.status_code, [401, 403])
        self.listing.refresh_from_db()
        self.assertEqual(self.listing.status, Listing.Status.DRAFT)

    def test_customer_cannot_publish(self):
        self.authenticate(30, "CUSTOMER")

        response = self.client.post(self.url, {}, format="json")

        self.assertEqual(response.status_code, 403)
        self.listing.refresh_from_db()
        self.assertEqual(self.listing.status, Listing.Status.DRAFT)

    def test_other_shop_cannot_publish(self):
        self.authenticate(20)

        response = self.client.post(self.url, {}, format="json")

        self.assertEqual(response.status_code, 403)
        self.listing.refresh_from_db()
        self.assertEqual(self.listing.status, Listing.Status.DRAFT)

    def test_unapproved_shop_cannot_publish(self):
        self.authenticate(10)

        response = self.client.post(self.url, {}, format="json")

        self.assertEqual(response.status_code, 403)
        self.listing.refresh_from_db()
        self.assertEqual(self.listing.status, Listing.Status.DRAFT)

    def test_approved_shop_still_requires_profile(self):
        self.shop.approval_status = Shop.ApprovalStatus.APPROVED
        self.shop.save()

        self.authenticate(10)
        response = self.client.post(self.url, {}, format="json")

        # LP-8 profile verification is not integrated yet.
        self.assertEqual(response.status_code, 403)
        self.listing.refresh_from_db()
        self.assertEqual(self.listing.status, Listing.Status.DRAFT)

    def test_client_cannot_supply_approval_flags(self):
        self.authenticate(10)

        response = self.client.post(
            self.url,
            {
                "shop_approved": True,
                "profile_complete": True,
            },
            format="json",
        )

        self.assertEqual(response.status_code, 400)
        self.listing.refresh_from_db()
        self.assertEqual(self.listing.status, Listing.Status.DRAFT)
    
    def test_approved_shop_can_publish_complete_listing(self):
        # LP-9: Shop must be approved.
        self.shop.approval_status = Shop.ApprovalStatus.APPROVED

        # LP-8: Shop profile must be complete.
        self.shop.address = "12 Bliss Street, Beirut"
        self.shop.area = "Hamra"
        self.shop.public_contact = "+961 1 234 567"
        self.shop.save()

        # LP-10: Listing must contain the required publication details.
        from datetime import timedelta
        from django.utils import timezone

        start = timezone.now() + timedelta(days=1)
        end = start + timedelta(hours=2)

        self.listing.title = "Croissant Box"
        self.listing.description = "Fresh pastries"
        self.listing.category = "Bakery"
        self.listing.original_price = "10.00"
        self.listing.discounted_price = "5.00"
        self.listing.total_quantity = 5
        self.listing.pickup_address = "12 Bliss Street, Beirut"
        self.listing.pickup_start = start
        self.listing.pickup_end = end
        self.listing.save()

        self.authenticate(10)

        response = self.client.post(self.url, {}, format="json")

        self.assertEqual(response.status_code, 200)
        self.listing.refresh_from_db()
        self.assertEqual(
            self.listing.status,
            Listing.Status.PUBLISHED,
        )

class MyListingsAPITests(TestCase):

    def setUp(self):
        self.client = APIClient()
        self.url = reverse("my_listings")

        self.shop = Shop.objects.create(
            operator_user_id=10,
            name="Cedar Bakery",
        )
        self.other_shop = Shop.objects.create(
            operator_user_id=20,
            name="Other Bakery",
        )

        self.own_listing = Listing.objects.create(
            shop_id=self.shop.id,
            title="Croissant Box",
        )
        self.other_listing = Listing.objects.create(
            shop_id=self.other_shop.id,
            title="Pizza Box",
        )

    def authenticate(self, user_id, role="SHOP_OPERATOR"):
        self.client.force_authenticate(
            user=TokenUser(
                id=user_id,
                role=role,
                is_staff=False,
            )
        )

    def test_operator_sees_only_own_listings(self):
        self.authenticate(10)

        response = self.client.get(self.url)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.data), 1)
        self.assertEqual(
            response.data[0]["title"],
            "Croissant Box",
        )

    def test_other_operator_sees_only_their_listings(self):
        self.authenticate(20)

        response = self.client.get(self.url)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.data), 1)
        self.assertEqual(
            response.data[0]["title"],
            "Pizza Box",
        )

    def test_customer_cannot_view_private_listings(self):
        self.authenticate(30, role="CUSTOMER")

        response = self.client.get(self.url)

        self.assertEqual(response.status_code, 403)

    def test_unauthenticated_user_cannot_view_listings(self):
        response = self.client.get(self.url)

        self.assertIn(response.status_code, [401, 403])

    def test_operator_without_shop_gets_404(self):
        self.authenticate(40)

        response = self.client.get(self.url)

        self.assertEqual(response.status_code, 404)
