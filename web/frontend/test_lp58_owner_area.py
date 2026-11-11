"""LP-58: the owner's offers in Live / Drafts / Ended tabs, with Mark sold
out and Delete draft, and /business as the owner's address."""

from datetime import timedelta

from django.urls import reverse
from django.utils import timezone

from .services import ServiceUnavailable
from .test_accounts import ApprovedShopMixin, WebAuthTestCase, patch_service
from .test_adjust_stock import PUBLISHED_LISTING
from .test_shop_profile import COMPLETE_SHOP

DRAFT = {**PUBLISHED_LISTING, "id": 43, "title": "Draft Pie", "status": "DRAFT", "free_quantity": None, "total_quantity": 3}
ENDED = {
    **PUBLISHED_LISTING, "id": 44, "title": "Yesterday's Bread",
    "pickup_start": (timezone.now() - timedelta(hours=5)).isoformat(),
    "pickup_end": (timezone.now() - timedelta(hours=3)).isoformat(),
}
SOLD_OUT = {**PUBLISHED_LISTING, "id": 45, "title": "Gone Cake", "free_quantity": 0}


class WorkspaceTabsTests(WebAuthTestCase):

    def setUp(self):
        self.sign_in_session()

    def workspace(self, listings, tab=None):
        with patch_service("my_shop", return_value=(200, {**COMPLETE_SHOP, "approval_status": "APPROVED"})), \
                patch_service("my_listings", return_value=(200, listings)):
            return self.client.get(reverse("workspace"), {"tab": tab} if tab else {})

    def panel(self, response, key):
        html = response.content.decode()
        start = html.index(f'id="offers-{key}"')
        end = html.find('class="offer-panel"', start + 1)
        return html[start:end if end != -1 else len(html)]

    def test_each_offer_is_in_its_tab_with_counts(self):
        response = self.workspace([PUBLISHED_LISTING, DRAFT, ENDED])

        self.assertIn("Croissant Box", self.panel(response, "live"))
        self.assertIn("Draft Pie", self.panel(response, "drafts"))
        self.assertIn("Yesterday", self.panel(response, "ended"))
        self.assertNotIn("Draft Pie", self.panel(response, "live"))
        for key in ("live", "drafts", "ended"):
            self.assertContains(response, f'href="?tab={key}"')
        self.assertContains(response, 'Live <span class="offer-tabs__count">1</span>', html=False)

    def test_live_opens_first_and_the_others_are_hidden(self):
        response = self.workspace([PUBLISHED_LISTING, DRAFT])

        self.assertEqual(response.context["active_tab"], "live")
        self.assertContains(response, '<div class="offer-panel" id="offers-drafts" hidden>')
        self.assertContains(response, '<div class="offer-panel" id="offers-live">')

    def test_drafts_open_first_when_nothing_is_live(self):
        self.assertEqual(self.workspace([DRAFT]).context["active_tab"], "drafts")

    def test_a_tab_can_be_chosen(self):
        response = self.workspace([PUBLISHED_LISTING, DRAFT], tab="drafts")

        self.assertEqual(response.context["active_tab"], "drafts")
        self.assertContains(response, 'href="?tab=drafts" aria-current="page"')

    def test_an_unknown_tab_falls_back(self):
        self.assertEqual(self.workspace([DRAFT], tab="nope").context["active_tab"], "drafts")

    def test_rows_show_price_units_left_and_pickup(self):
        listing = {**PUBLISHED_LISTING, "discounted_price": "4.50",
                   "pickup_start": "2026-11-05T14:00:00Z", "pickup_end": "2099-11-05T16:00:00Z"}
        response = self.workspace([listing])

        self.assertContains(response, "<strong>$4.50</strong>", html=False)
        self.assertContains(response, "4 left")

    def test_actions_fit_the_offer(self):
        response = self.workspace([PUBLISHED_LISTING, DRAFT, ENDED, SOLD_OUT])

        self.assertIn(reverse("mark-sold-out", args=[42]), self.panel(response, "live"))
        self.assertIn(reverse("delete-draft", args=[43]), self.panel(response, "drafts"))
        # Not for an ended offer, nor for one that is already sold out.
        self.assertNotContains(response, reverse("mark-sold-out", args=[44]))
        self.assertNotContains(response, reverse("mark-sold-out", args=[45]))
        self.assertNotContains(response, reverse("delete-draft", args=[42]))

    def test_the_status_banner_is_at_the_top(self):
        response = self.workspace([])

        self.assertContains(response, 'class="status-banner status-banner--APPROVED"')

    def test_no_inline_styles_are_left(self):
        response = self.workspace([PUBLISHED_LISTING, DRAFT, ENDED])

        self.assertNotIn('style="', response.content.decode().split("<main", 1)[1])


def follow(client, response):
    """Open the page a redirect points to, with the offer list mocked."""
    with patch_service("my_listings", return_value=(200, [])):
        return client.get(response.url)


class MarkSoldOutTests(ApprovedShopMixin, WebAuthTestCase):

    def setUp(self):
        self.sign_in_session()
        self.url = reverse("mark-sold-out", args=[42])

    def test_sold_out_goes_back_to_live_with_a_message(self):
        sold = (201, {"adjustment": {"id": 1}, "listing": {**PUBLISHED_LISTING, "free_quantity": 0}})
        with patch_service("mark_sold_out", return_value=sold) as api:
            response = self.client.post(self.url, follow=False)

        api.assert_called_once()
        self.assertEqual(api.call_args.args[1], 42)
        self.assertRedirects(response, f"{reverse('workspace')}?tab=live", fetch_redirect_response=False)
        page = follow(self.client, response)
        self.assertContains(page, "Croissant Box is marked sold out.")

    def test_refusals_are_explained(self):
        for code, text in (("already_sold_out", "already sold out"), ("pickup_ended", "pickup time has ended"),
                           ("below_committed", "already reserved")):
            with self.subTest(code=code), patch_service("mark_sold_out", return_value=(409, {"code": code})):
                response = follow(self.client, self.client.post(self.url))
                self.assertContains(response, text)

    def test_unreachable_service_warns_not_to_repeat(self):
        with patch_service("mark_sold_out", side_effect=ServiceUnavailable("marketplace")):
            response = follow(self.client, self.client.post(self.url))

        self.assertContains(response, "couldn&#x27;t confirm whether the offer was marked sold out")

    def test_only_post(self):
        with patch_service("mark_sold_out") as api:
            self.assertEqual(self.client.get(self.url).status_code, 405)
        api.assert_not_called()


class DeleteDraftTests(ApprovedShopMixin, WebAuthTestCase):

    def setUp(self):
        self.sign_in_session()
        self.url = reverse("delete-draft", args=[43])

    def test_deleted(self):
        with patch_service("delete_draft", return_value=(204, {})) as api:
            response = self.client.post(self.url)

        self.assertEqual(api.call_args.args[1], 43)
        self.assertRedirects(response, f"{reverse('workspace')}?tab=drafts", fetch_redirect_response=False)

    def test_a_published_offer_is_refused(self):
        with patch_service("delete_draft", return_value=(409, {"code": "not_a_draft"})):
            response = follow(self.client, self.client.post(self.url))

        self.assertContains(response, "Only drafts can be deleted.")

    def test_only_post(self):
        with patch_service("delete_draft") as api:
            self.assertEqual(self.client.get(self.url).status_code, 405)
        api.assert_not_called()


class BusinessAddressTests(WebAuthTestCase):

    def test_business_opens_the_workspace(self):
        response = self.client.get("/business/?tab=drafts")

        self.assertRedirects(response, f"{reverse('workspace')}?tab=drafts", fetch_redirect_response=False)
