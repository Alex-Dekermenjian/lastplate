"""LP-54: photo effect on the web pages, the one-tap peek, and the owner's real photo.

Marketplace decides which photo address customers get (tested there); these
tests check that the pages show what it sends, the right hint, the saved peek
after a reload, the tap route, and that owners always see the real photo.
"""

import json

from django.test import Client, override_settings
from django.urls import reverse

from .services import ServiceUnavailable
from .test_accounts import ApprovedShopMixin, WebAuthTestCase, patch_service
from .test_browse_offers import offer
from .test_lp38_lastbag import BAG_POST, DRAFT_BAG, bag_offer
from .test_lp39_restaurant_page import SHOP
from .test_lp56_login_signup import CUSTOMER, OPERATOR
from .test_lp62_cart import CartTestCase

ORIGINAL = "/media/listing_photos/0123456789abcdef0123456789abcdef.png"
BLURRED_COPY = "/media/listing_photos_blurred/fedcba9876543210fedcba9876543210.jpg"
CIRCLE = "data:image/png;base64,iVBORw0KGgo="
SAVED_PEEK = {"listing_id": 42, "x": 25.0, "y": 60.5, "size": 28.0, "image": CIRCLE}

NORMAL_OFFER = offer(id=42, photo_display=ORIGINAL, photo_effect="NORMAL")
BLURRED_OFFER = offer(id=42, photo_display=BLURRED_COPY, photo_effect="BLURRED")
MAGIC_OFFER = offer(id=42, photo_display=BLURRED_COPY, photo_effect="MAGIC")
PEEK_URL = reverse("offer-peek", args=[42])


@override_settings(DEBUG=True)  # photo links are development-only (LP-46)
class PhotoEffectPageTestCase(CartTestCase):

    def offer_page(self, data, peeks=(200, [])):
        with patch_service("listing_offer", return_value=(200, data)), \
                patch_service("my_peeks", return_value=peeks) as my_peeks:
            response = self.client.get(reverse("offer-detail", args=[data["id"]]))
        return response, my_peeks

    def restaurant_page(self, offers, peeks=(200, [])):
        with patch_service("restaurant_page", return_value=(200, {"shop": SHOP, "offers": offers})), \
                patch_service("my_peeks", return_value=peeks) as my_peeks:
            response = self.client.get(reverse("restaurant-detail", args=[3]))
        return response, my_peeks

    def browse_page(self, offers, peeks=(200, [])):
        with patch_service("browse_listings", return_value=(200, offers)), \
                patch_service("my_peeks", return_value=peeks) as my_peeks:
            response = self.client.get(reverse("browse-offers"))
        return response, my_peeks


class EachEffectShowsTheRightImageTests(PhotoEffectPageTestCase):

    def setUp(self):
        self.sign_in_as(CUSTOMER)

    def test_normal_shows_the_photo_with_no_peek(self):
        html = self.offer_page(NORMAL_OFFER)[0].content.decode()

        self.assertIn(f'src="{ORIGINAL}"', html)
        self.assertNotIn("data-peek", html)
        self.assertNotIn("Tap once to peek", html)

    def test_blurred_shows_the_blurred_copy_and_cannot_be_peeked(self):
        html = self.offer_page(BLURRED_OFFER)[0].content.decode()

        self.assertIn(f'src="{BLURRED_COPY}"', html)
        self.assertIn("A surprise · blurred", html)
        self.assertNotIn("data-peek", html)
        self.assertNotIn("Tap once to peek", html)

    def test_magic_shows_the_blurred_copy_and_the_tap_hint(self):
        html = self.offer_page(MAGIC_OFFER)[0].content.decode()

        self.assertIn(f'src="{BLURRED_COPY}"', html)
        self.assertIn("Tap once to peek ✨", html)
        self.assertIn(f'data-peek-url="{PEEK_URL}"', html)
        self.assertIn('data-viewer="customer"', html)
        self.assertIn('role="button"', html)

    def test_magic_works_on_offer_cards_and_the_restaurant_page(self):
        bag = bag_offer(id=50, photo_display=BLURRED_COPY, photo_effect="MAGIC")
        item = offer(id=42, kind="ITEM", photo_display=BLURRED_COPY, photo_effect="MAGIC")

        for response in (self.restaurant_page([bag, item])[0], self.browse_page([bag, item])[0]):
            html = response.content.decode()
            self.assertEqual(html.count("Tap once to peek ✨"), 2)
            self.assertIn(f'data-peek-url="{reverse("offer-peek", args=[50])}"', html)
            self.assertIn(f'data-peek-url="{reverse("offer-peek", args=[42])}"', html)

    def test_the_restaurant_page_now_shows_a_lastbags_photo(self):
        bag = bag_offer(id=50, photo_display=ORIGINAL, photo_effect="NORMAL")

        html = self.restaurant_page([bag])[0].content.decode()

        self.assertIn(f'src="{ORIGINAL}"', html)

    def test_the_page_never_holds_more_than_marketplace_sent(self):
        """The web adds no photo address of its own: for Magic it is only the
        blurred copy, plus the cut-out as an inline image."""
        html = self.offer_page(MAGIC_OFFER, peeks=(200, [SAVED_PEEK]))[0].content.decode()

        self.assertNotIn("/media/listing_photos/", html)
        self.assertIn(BLURRED_COPY, html)


class NotSignedInTests(PhotoEffectPageTestCase):

    def test_guests_are_asked_to_log_in_to_peek(self):
        response, my_peeks = self.offer_page(MAGIC_OFFER)
        html = response.content.decode()

        self.assertIn("Log in to peek ✨", html)
        self.assertIn(f'href="{reverse("login")}?next=/offers/42/"', html)
        self.assertIn('data-viewer="guest"', html)
        self.assertNotIn("Tap once to peek", html)
        my_peeks.assert_not_called()

    def test_a_guests_tap_is_refused_with_the_login_link(self):
        with patch_service("peek_photo") as peek:
            response = self.client.post(PEEK_URL, json.dumps({"x": 10, "y": 10}),
                                        content_type="application/json", HTTP_X_PEEK_PAGE="/restaurants/3/")

        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.json()["code"], "login")
        self.assertEqual(response.json()["login_url"], f"{reverse('login')}?next=%2Frestaurants%2F3%2F")
        peek.assert_not_called()

    def test_the_login_link_only_goes_back_to_this_site(self):
        response = self.client.post(PEEK_URL, "{}", content_type="application/json",
                                    HTTP_X_PEEK_PAGE="//evil.example/")

        self.assertTrue(response.json()["login_url"].endswith("next=%2Foffers%2F42%2F"))

    def test_restaurants_see_the_blur_but_cannot_peek(self):
        self.sign_in_as(OPERATOR)

        with patch_service("my_shop", return_value=(404, {})):
            html = self.offer_page(MAGIC_OFFER)[0].content.decode()
        with patch_service("peek_photo") as peek:
            response = self.client.post(PEEK_URL, json.dumps({"x": 1, "y": 1}), content_type="application/json")

        self.assertIn("Customers can peek once ✨", html)
        self.assertNotIn('role="button"', html)
        self.assertEqual(response.status_code, 403)
        peek.assert_not_called()


class TapTests(PhotoEffectPageTestCase):

    def setUp(self):
        self.sign_in_as(CUSTOMER)

    def tap(self, answer=(201, SAVED_PEEK), body=None, side_effect=None):
        with patch_service("peek_photo", return_value=answer, side_effect=side_effect) as peek:
            response = self.client.post(
                PEEK_URL, json.dumps(body if body is not None else {"x": 25, "y": 60.5}),
                content_type="application/json",
            )
        return response, peek

    def test_the_tap_point_goes_to_marketplace_with_the_customers_token(self):
        response, peek = self.tap()

        self.assertEqual(response.status_code, 201)
        peek.assert_called_once_with("access-token-1", 42, 25, 60.5)
        self.assertEqual(response.json(), {"peek": {"x": 25.0, "y": 60.5, "size": 28.0, "image": CIRCLE}})

    def test_a_second_tap_is_refused_and_shows_the_first_peek(self):
        refused = (409, {"code": "already_peeked", "detail": "x", "peek": SAVED_PEEK})

        response, _ = self.tap(answer=refused, body={"x": 90, "y": 90})

        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json()["code"], "already_peeked")
        self.assertEqual(response.json()["peek"]["x"], 25.0)

    def test_a_photo_that_is_not_magic_is_refused(self):
        response, _ = self.tap(answer=(409, {"code": "no_magic", "detail": "This photo can't be peeked at."}))

        self.assertEqual(response.status_code, 409)
        self.assertNotIn("peek", response.json())

    def test_a_tap_must_be_a_point(self):
        for body in ({}, {"x": 1}, {"x": 1, "y": 2, "customer_user_id": 9}, []):
            with self.subTest(body=body):
                response, peek = self.tap(body=body)
                self.assertEqual(response.status_code, 400)
                peek.assert_not_called()

    def test_marketplace_down_asks_to_try_again(self):
        response, _ = self.tap(side_effect=ServiceUnavailable("marketplace"))

        self.assertEqual(response.status_code, 503)

    def test_a_tap_needs_the_csrf_token(self):
        client = Client(enforce_csrf_checks=True)
        client.cookies = self.client.cookies

        with patch_service("peek_photo") as peek:
            response = client.post(PEEK_URL, json.dumps({"x": 1, "y": 1}), content_type="application/json")

        self.assertEqual(response.status_code, 403)
        peek.assert_not_called()


class SameSpotOnReloadTests(PhotoEffectPageTestCase):

    def setUp(self):
        self.sign_in_as(CUSTOMER)

    def test_the_saved_circle_is_shown_again_at_the_same_spot(self):
        response, my_peeks = self.offer_page(MAGIC_OFFER, peeks=(200, [SAVED_PEEK]))
        html = response.content.decode()

        my_peeks.assert_called_once_with("access-token-1", [42])
        self.assertIn(f'src="{CIRCLE}"', html)
        self.assertIn('data-x="25.0" data-y="60.5" data-size="28.0"', html)
        self.assertIn("left: 25.0%; top: 60.5%; width: 28.0%;", html)
        self.assertIn("You've had your peek ✨", html)
        self.assertNotIn("Tap once to peek", html)
        # No longer a button: there is nothing left to tap.
        self.assertNotIn('role="button"', html)

    def test_one_lookup_for_the_whole_page_and_only_for_magic_offers(self):
        offers = [
            offer(id=1, photo_display=BLURRED_COPY, photo_effect="MAGIC"),
            offer(id=2, photo_display=ORIGINAL, photo_effect="NORMAL"),
            offer(id=3, photo_display=BLURRED_COPY, photo_effect="BLURRED"),
            offer(id=4, photo_display=BLURRED_COPY, photo_effect="MAGIC"),
            offer(id=5, photo_display=None, photo_effect="MAGIC"),
        ]

        _, my_peeks = self.browse_page(offers)

        my_peeks.assert_called_once_with("access-token-1", [1, 4])

    def test_the_page_still_opens_when_peeks_cannot_be_loaded(self):
        with patch_service("listing_offer", return_value=(200, MAGIC_OFFER)), \
                patch_service("my_peeks", side_effect=ServiceUnavailable("marketplace")):
            response = self.client.get(reverse("offer-detail", args=[42]))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Tap once to peek ✨")


@override_settings(DEBUG=True)
class OwnerSeesTheOriginalTests(ApprovedShopMixin, WebAuthTestCase):

    def setUp(self):
        self.sign_in_session()

    def owned(self, effect, **changes):
        return {**DRAFT_BAG, "photo_display": ORIGINAL, "photo_effect": effect, **changes}

    def test_the_workspace_shows_the_real_photo_and_the_effect_customers_get(self):
        listings = [self.owned("MAGIC", id=42), self.owned("NORMAL", id=43), self.owned("BLURRED", id=44)]

        with patch_service("my_listings", return_value=(200, listings)):
            html = self.client.get(reverse("workspace"), {"tab": "drafts"}).content.decode()

        self.assertIn(f'src="{reverse("owner-offer-photo", args=[42])}"', html)
        self.assertIn(f'src="{reverse("owner-offer-photo", args=[44])}"', html)
        self.assertIn(f'src="{ORIGINAL}"', html)  # the Normal one, as before
        self.assertIn("Customers see: Blurred with magic ✨", html)
        self.assertIn("Customers see: Blurred<", html)
        self.assertNotIn(BLURRED_COPY, html)

    def test_the_edit_form_shows_the_real_photo_and_the_effect(self):
        with patch_service("my_listings", return_value=(200, [self.owned("MAGIC")])):
            html = self.client.get(reverse("edit-listing", args=[42])).content.decode()

        self.assertIn(f'src="{reverse("owner-offer-photo", args=[42])}"', html)
        self.assertIn("Customers see: Blurred with magic ✨", html)
        self.assertIn('value="MAGIC" checked', html)

    def test_the_owners_photo_route_returns_the_original(self):
        with patch_service("owner_listing_photo", return_value=(200, ("image/png", b"real-photo"))) as fetch:
            response = self.client.get(reverse("owner-offer-photo", args=[42]))

        fetch.assert_called_once_with("access-token-1", 42)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.content, b"real-photo")
        self.assertEqual(response["Content-Type"], "image/png")
        self.assertIn("no-store", response["Cache-Control"])

    def test_someone_elses_photo_is_not_found(self):
        with patch_service("owner_listing_photo", return_value=(404, None)):
            response = self.client.get(reverse("owner-offer-photo", args=[99]))

        self.assertEqual(response.status_code, 404)

    def test_customers_cannot_use_the_owners_photo_route(self):
        session = self.client.session
        session["account"] = {"id": 5, "email": "c@example.test", "display_name": "C", "role": "CUSTOMER"}
        session.save()

        with patch_service("owner_listing_photo") as fetch:
            response = self.client.get(reverse("owner-offer-photo", args=[42]))

        self.assertEqual(response.status_code, 302)
        fetch.assert_not_called()


class OfferFormEffectTests(ApprovedShopMixin, WebAuthTestCase):

    def setUp(self):
        self.url = reverse("create-listing")
        self.sign_in_session()

    def test_items_default_to_normal(self):
        response = self.client.get(self.url, {"kind": "ITEM"})

        self.assertContains(response, "Photo effect")
        self.assertContains(response, 'value="NORMAL" checked')
        self.assertNotContains(response, 'value="MAGIC" checked')

    def test_a_lastbag_suggests_blurred_with_magic(self):
        response = self.client.get(self.url, {"kind": "LASTBAG"})

        self.assertContains(response, 'value="MAGIC" checked')
        self.assertContains(response, "Suggested")

    def test_the_chosen_effect_is_sent_to_marketplace(self):
        with patch_service("create_listing", return_value=(201, {"id": 7})) as create:
            self.client.post(self.url, {**BAG_POST, "photo_effect": "BLURRED"})

        self.assertEqual(create.call_args.args[1]["photo_effect"], "BLURRED")

    def test_an_unknown_effect_is_not_sent(self):
        with patch_service("create_listing", return_value=(201, {"id": 7})) as create:
            self.client.post(self.url, {**BAG_POST, "photo_effect": "SHARP"})

        self.assertNotIn("photo_effect", create.call_args.args[1])

    def test_editing_sends_the_new_effect(self):
        with patch_service("my_listings", return_value=(200, [DRAFT_BAG])), \
                patch_service("edit_listing", return_value=(200, DRAFT_BAG)) as edit:
            self.client.post(reverse("edit-listing", args=[42]), {**BAG_POST, "photo_effect": "NORMAL"})

        self.assertEqual(edit.call_args.args[2]["photo_effect"], "NORMAL")
