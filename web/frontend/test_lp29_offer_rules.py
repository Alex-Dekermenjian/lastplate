"""LP-29 (website): offer counts, the stock floor, taking an offer down and
the branch's closing time.

Marketplace decides every rule (tested there); these tests check what the
owner and the customer see, and what the website sends.
"""

from datetime import datetime, timedelta

from django.urls import reverse
from django.utils import timezone

from .listing_views import closing_hint, default_pickup_end
from .services import ServiceUnavailable
from .test_accounts import ApprovedShopMixin, WebAuthTestCase, patch_service
from .test_adjust_stock import PUBLISHED_LISTING
from .test_lp19_checkout import CheckoutTestCase, changed
from .test_lp62_cart import BREAD, CROISSANTS
from .test_lp58_owner_area import DRAFT
from .test_lp68_branches import ACHRAFIEH, HAMRA, ONE_BRANCH_SHOP, OwnerTestCase
from .test_shop_profile import COMPLETE_SHOP

LIVE = {**PUBLISHED_LISTING, "total_quantity": 3, "free_quantity": 2,
        "reserved_quantity": 1, "sold_quantity": 0, "committed_quantity": 1}
TAKEN_DOWN = {**LIVE, "id": 46, "title": "Old Cake", "status": "WITHDRAWN",
              "reserved_quantity": 2, "free_quantity": 1, "committed_quantity": 2}
HAMRA_CLOSES = {**HAMRA, "closing_time": "22:00:00"}


class WorkspaceTestCase(WebAuthTestCase):
    def setUp(self):
        self.sign_in_session()

    def workspace(self, listings, tab=None):
        with patch_service("my_shop", return_value=(200, {**COMPLETE_SHOP, "approval_status": "APPROVED"})), \
                patch_service("my_listings", return_value=(200, listings)):
            return self.client.get(reverse("workspace"), {"tab": tab} if tab else {})


class OfferCountsTests(WorkspaceTestCase):
    def test_each_offer_shows_posted_reserved_left_and_sold(self):
        response = self.workspace([LIVE])

        self.assertContains(response, "3 posted · 1 reserved · 2 left · 0 sold")

    def test_sold_units_are_counted_apart(self):
        listing = {**LIVE, "total_quantity": 5, "free_quantity": 1, "reserved_quantity": 1, "sold_quantity": 3}

        self.assertContains(self.workspace([listing]), "5 posted · 1 reserved · 1 left · 3 sold")

    def test_drafts_show_what_they_will_sell_instead(self):
        response = self.workspace([DRAFT])

        self.assertNotContains(response, "posted ·")
        self.assertContains(response, "3 to sell")


class StockFloorTests(ApprovedShopMixin, WorkspaceTestCase):
    def test_stock_form_shows_already_reserved(self):
        response = self.workspace([LIVE])

        self.assertContains(response, "Already reserved: 1")
        self.assertContains(response, "stock can't go below 1")
        # The browser can't send a removal bigger than what is left.
        self.assertContains(response, 'min="-2"')

    def test_cannot_lower_stock_below_reserved_and_the_message_says_how_far(self):
        refusal = (400, {"code": "below_committed", "detail": "Stock can't go below 2.", "already_reserved": 2})
        with patch_service("adjust_stock", return_value=refusal):
            response = self.client.post(reverse("adjust-stock", args=[42]),
                                        {"quantity_change": "-3", "reason": "Dropped a tray"}, follow=True)

        self.assertContains(response, "Already reserved: 2")
        self.assertContains(response, "can&#x27;t lower the stock below 2")


class TakeDownTests(ApprovedShopMixin, WorkspaceTestCase):
    def test_live_offers_can_be_taken_down_and_drafts_cannot(self):
        response = self.workspace([LIVE, DRAFT], tab="live")

        self.assertContains(response, reverse("unpublish-listing", args=[42]))
        self.assertContains(response, "The 1 already reserved stay reserved and can still be picked up.")
        self.assertNotContains(response, reverse("unpublish-listing", args=[43]))

    def test_unpublish_tells_the_owner_reservations_stay(self):
        answer = (200, {**TAKEN_DOWN, "id": 42, "title": "Croissant Box"})
        with patch_service("unpublish_listing", return_value=answer) as call:
            response = self.client.post(reverse("unpublish-listing", args=[42]))

        call.assert_called_once()
        self.assertEqual(call.call_args.args[1:], (42,))
        self.assertRedirects(response, reverse("workspace") + "?tab=ended", fetch_redirect_response=False)
        page = self.workspace([TAKEN_DOWN])
        self.assertContains(page, "Croissant Box was taken down. Customers no longer see it.")
        self.assertContains(page, "The 2 units already reserved stay reserved and can still be picked up.")

    def test_a_taken_down_offer_is_ended_hidden_and_still_counted(self):
        response = self.workspace([TAKEN_DOWN], tab="ended")

        self.assertEqual(response.context["listings"][0]["tab"], "ended")
        self.assertContains(response, "Status: Taken down")
        self.assertContains(response, "Orders already reserved can still be picked up.")
        self.assertContains(response, "3 posted · 2 reserved · 1 left · 0 sold")
        self.assertNotContains(response, reverse("unpublish-listing", args=[46]))
        self.assertNotContains(response, reverse("mark-sold-out", args=[46]))

    def test_refusals_and_no_answer(self):
        with patch_service("unpublish_listing", return_value=(409, {"code": "not_published"})):
            refused = self.client.post(reverse("unpublish-listing", args=[43]), follow=True)
        self.assertContains(refused, "Only live offers can be taken down.")

        with patch_service("unpublish_listing", side_effect=ServiceUnavailable):
            unknown = self.client.post(reverse("unpublish-listing", args=[42]), follow=True)
        self.assertContains(unknown, "couldn&#x27;t confirm whether the offer was taken down")


class PublishedOfferCannotBeEditedTests(ApprovedShopMixin, WebAuthTestCase):
    def setUp(self):
        self.sign_in_session()

    def test_published_offer_cannot_be_edited(self):
        url = reverse("edit-listing", args=[42])
        with patch_service("my_listings", return_value=(200, [LIVE])), \
                patch_service("edit_listing") as edit_api:
            opened = self.client.get(url)
            sent = self.client.post(url, {"title": "Changed", "discounted_price": "0.50"})

        for response in (opened, sent):
            self.assertContains(response, "Only drafts can be edited", status_code=404)
        edit_api.assert_not_called()

    def test_the_workspace_offers_no_edit_for_it(self):
        with patch_service("my_listings", return_value=(200, [LIVE])):
            response = self.client.get(reverse("workspace"))

        self.assertNotContains(response, reverse("edit-listing", args=[42]))


class UnpublishedOfferInCartTests(CheckoutTestCase):
    """The cart holds 42 (Croissant Box) and 43 (Bread Bag). Then the
    restaurant takes 42 down, so the public offer API answers 404."""

    # Pickup later today, so the cart can't say "Pickup ended." instead.
    later = {"pickup_start": "2099-11-16T14:00:00Z", "pickup_end": "2099-11-16T16:00:00Z"}
    on_sale = {42: {**CROISSANTS, **later}, 43: {**BREAD, **later}}
    taken_down = {**on_sale, 42: None}

    def test_unpublished_offer_in_cart_is_grey_and_reserve_is_off(self):
        response = self.view(self.taken_down)

        self.assertIn("cart-line cart-line--gone", response.content.decode())
        self.assertContains(response, "No longer available")
        self.assertContains(
            response,
            '<button class="button button--primary button--block" type="button" disabled>Reserve</button>',
            html=True,
        )
        self.assertNotContains(response, reverse("cart-checkout"))
        self.assertEqual(response.context["total"], 3)          # only the Bread Bag counts

    def test_checkout_refuses_it(self):
        key = self.key_on_page(self.view(self.on_sale))           # the page from before it was taken down
        refusal = changed({"listing_id": 42, "title": "Croissant Box", "code": "not_available",
                           "detail": "This offer is no longer available."})

        response, call, _ = self.reserve(answer=refusal, key=key, changes=self.taken_down)

        call.assert_called_once()
        self.assertRedirects(response, reverse("cart"))
        self.assertContains(response, "nothing was reserved")
        self.assertContains(response, "This offer is no longer available.")
        self.assertIn("42", self.cart()["lines"])                # nothing silently dropped


class BranchClosingTimeTests(OwnerTestCase):
    shop = {**ONE_BRANCH_SHOP, "branches": [HAMRA_CLOSES]}

    def test_branches_page_shows_and_edits_the_closing_time(self):
        with patch_service("my_branches", return_value=(200, [HAMRA_CLOSES, ACHRAFIEH])):
            page = self.client.get(reverse("branches"))

        self.assertContains(page, "Closes at 22:00")
        self.assertContains(page, "No closing time set")
        self.assertContains(page, 'name="branch-11-closing_time" value="22:00"')

        data = {"branch-11-name": "Hamra", "branch-11-address": "12 Bliss Street, Beirut",
                "branch-11-area": "Hamra", "branch-11-closing_time": "21:30"}
        with patch_service("my_branches", return_value=(200, [HAMRA_CLOSES])), \
                patch_service("update_branch", return_value=(200, HAMRA_CLOSES)) as update:
            self.client.post(reverse("branch-edit", args=[11]), data)
            self.client.post(reverse("branch-edit", args=[11]), {**data, "branch-11-closing_time": ""})

        self.assertEqual(update.call_args_list[0].args[2]["closing_time"], "21:30")
        self.assertIsNone(update.call_args_list[1].args[2]["closing_time"])

    def test_a_new_branch_can_be_added_with_one(self):
        data = {"new-name": "Achrafieh", "new-address": "5 Sassine Square, Beirut", "new-area": "Achrafieh",
                "new-closing_time": "20:00"}
        with patch_service("my_branches", return_value=(200, [HAMRA_CLOSES])), \
                patch_service("add_branch", return_value=(201, ACHRAFIEH)) as add:
            self.client.post(reverse("branches"), data)

        self.assertEqual(add.call_args.args[1]["closing_time"], "20:00")

    def test_an_empty_closing_time_is_left_out_unless_it_clears_one(self):
        data = {"branch-12-name": "Achrafieh", "branch-12-address": "5 Sassine Square, Beirut",
                "branch-12-area": "Achrafieh", "branch-12-closing_time": ""}
        with patch_service("my_branches", return_value=(200, [HAMRA_CLOSES, ACHRAFIEH])),                 patch_service("update_branch", return_value=(200, ACHRAFIEH)) as update:
            self.client.post(reverse("branch-edit", args=[12]), data)

        self.assertNotIn("closing_time", update.call_args.args[2])

    def test_closing_time_fills_pickup_end_by_default(self):
        response = self.client.get(reverse("create-listing"))

        expected = default_pickup_end([HAMRA_CLOSES])
        self.assertTrue(expected.endswith("T22:00"))
        self.assertContains(response, f'value="{expected}"')
        self.assertContains(response, "Hamra closes at 22:00.")

    def test_a_later_pickup_end_needs_a_confirm(self):
        warning = ("Pickup ends at 23:00, after Hamra closes at 22:00. Confirm that customers can still pick "
                   "up then, or choose an earlier pickup end.")
        data = {"title": "Bread", "pickup_start": "2099-11-16T18:00", "pickup_end": "2099-11-16T23:00"}

        with patch_service("create_listing", return_value=(400, {"confirm_after_closing": [warning]})) as create:
            asked = self.client.post(reverse("create-listing"), data)

        self.assertContains(asked, warning, status_code=400)
        self.assertContains(asked, 'name="confirm_after_closing" value="1" required', status_code=400)
        self.assertNotContains(asked, "Please check the following errors", status_code=400)
        self.assertContains(asked, 'value="2099-11-16T23:00"', status_code=400)   # what they typed is kept
        self.assertNotIn("confirm_after_closing", create.call_args.args[1])

        with patch_service("create_listing", return_value=(201, {"id": 50})) as create:
            confirmed = self.client.post(reverse("create-listing"), {**data, "confirm_after_closing": "1"})

        self.assertRedirects(confirmed, reverse("workspace"), fetch_redirect_response=False)
        self.assertIs(create.call_args.args[1]["confirm_after_closing"], True)

    def test_editing_a_draft_asks_too(self):
        draft = {**DRAFT, "branch": 11, "pickup_end": "2099-11-16T21:00:00+02:00"}
        warning = "Pickup ends at 23:00, after Hamra closes at 22:00."
        with patch_service("my_listings", return_value=(200, [draft])), \
                patch_service("edit_listing", return_value=(400, {"confirm_after_closing": [warning]})):
            response = self.client.post(reverse("edit-listing", args=[43]),
                                        {"title": "Draft Pie", "pickup_end": "2099-11-16T23:00"})

        self.assertContains(response, warning, status_code=400)
        self.assertContains(response, 'name="confirm_after_closing"', status_code=400)


class DefaultPickupEndTests(WebAuthTestCase):
    def at(self, hour, minute=0):
        return timezone.make_aware(datetime.combine(timezone.localdate(), datetime.min.time())
                                   + timedelta(hours=hour, minutes=minute))

    def test_today_at_closing_or_tomorrow_once_it_has_passed(self):
        today = timezone.localdate()

        self.assertEqual(default_pickup_end([HAMRA_CLOSES], now=self.at(15)), f"{today:%Y-%m-%d}T22:00")
        tomorrow = today + timedelta(days=1)
        self.assertEqual(default_pickup_end([HAMRA_CLOSES], now=self.at(22, 30)), f"{tomorrow:%Y-%m-%d}T22:00")

    def test_several_branches_use_the_earliest_and_none_means_empty(self):
        verdun = {**ACHRAFIEH, "name": "Verdun", "closing_time": "20:30:00"}

        self.assertTrue(default_pickup_end([HAMRA_CLOSES, verdun], now=self.at(9)).endswith("T20:30"))
        self.assertEqual(default_pickup_end([HAMRA, ACHRAFIEH]), "")
        self.assertEqual(closing_hint([HAMRA_CLOSES, verdun, ACHRAFIEH]),
                         "Hamra closes at 22:00 · Verdun closes at 20:30.")
