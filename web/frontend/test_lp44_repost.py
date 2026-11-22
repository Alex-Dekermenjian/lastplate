"""LP-44: Repost on an ended offer creates today's draft and opens its edit page."""

from django.urls import reverse
from django.utils.html import escape

from .services import ServiceUnavailable
from .test_accounts import ApprovedShopMixin, WebAuthTestCase, patch_service
from .test_adjust_stock import PUBLISHED_LISTING
from .test_lp58_owner_area import DRAFT, ENDED, follow
from .test_shop_profile import COMPLETE_SHOP

REPOSTED = {
    **ENDED, "id": 77, "status": "DRAFT", "total_quantity": 0, "free_quantity": 0,
    "reserved_quantity": 0, "sold_quantity": 0, "committed_quantity": 0,
    "pickup_start": "2026-11-22T15:00:00Z", "pickup_end": "2026-11-22T18:30:00Z",
}
TAKEN_DOWN = {**PUBLISHED_LISTING, "id": 46, "title": "Taken Down Tart", "status": "WITHDRAWN"}


class RepostButtonTests(WebAuthTestCase):

    def setUp(self):
        self.sign_in_session()

    def workspace(self, listings, tab=None):
        with patch_service("my_shop", return_value=(200, {**COMPLETE_SHOP, "approval_status": "APPROVED"})),                 patch_service("my_listings", return_value=(200, listings)):
            return self.client.get(reverse("workspace"), {"tab": tab} if tab else {})

    def panel(self, response, key):
        html = response.content.decode()
        start = html.index(f'id="offers-{key}"')
        end = html.find('class="offer-panel"', start + 1)
        return html[start:end if end != -1 else len(html)]

    def test_each_ended_offer_has_a_repost_button(self):
        response = self.workspace([PUBLISHED_LISTING, DRAFT, ENDED, TAKEN_DOWN], tab="ended")

        ended = self.panel(response, "ended")
        self.assertIn(reverse("repost-listing", args=[44]), ended)
        self.assertIn(reverse("repost-listing", args=[46]), ended)
        self.assertIn(">Repost</button>", ended)

    def test_live_offers_and_drafts_have_no_repost_button(self):
        response = self.workspace([PUBLISHED_LISTING, DRAFT, ENDED])

        self.assertNotContains(response, reverse("repost-listing", args=[42]))
        self.assertNotContains(response, reverse("repost-listing", args=[43]))


class RepostActionTests(ApprovedShopMixin, WebAuthTestCase):

    def setUp(self):
        self.sign_in_session()
        self.url = reverse("repost-listing", args=[44])

    def test_repost_opens_the_new_drafts_edit_page(self):
        with patch_service("repost_listing", return_value=(201, REPOSTED)) as api:
            response = self.client.post(self.url)

        api.assert_called_once()
        self.assertEqual(api.call_args.args[1], 44)
        self.assertRedirects(response, reverse("edit-listing", args=[77]), fetch_redirect_response=False)

    def test_the_edit_page_has_an_empty_quantity_to_fill_in(self):
        with patch_service("repost_listing", return_value=(201, REPOSTED)):
            response = self.client.post(self.url)
        with patch_service("my_listings", return_value=(200, [REPOSTED])):
            page = self.client.get(response.url)

        self.assertEqual(page.status_code, 200)
        self.assertContains(page, "Reposted as a new draft for today")
        self.assertContains(page, f'value="{escape(ENDED["title"])}"')
        html = page.content.decode()
        box = html[html.index('id="total_quantity"'):]
        box = box[:box.index(">")]
        self.assertIn('value=""', box)

    def test_a_draft_with_units_still_shows_them(self):
        with patch_service("my_listings", return_value=(200, [{**REPOSTED, "total_quantity": 6}])):
            page = self.client.get(reverse("edit-listing", args=[77]))

        html = page.content.decode()
        box = html[html.index('id="total_quantity"'):]
        self.assertIn('value="6"', box[:box.index(">")])

    def test_refusals_are_explained_and_go_back_to_ended(self):
        for code, text in (("not_ended", "Only ended offers can be reposted."),
                           ("not_owner", "You can only repost your own offers."),
                           ("branch_removed", "branch was removed")):
            with self.subTest(code=code), patch_service("repost_listing", return_value=(403, {"code": code})):
                response = self.client.post(self.url)
                self.assertRedirects(response, f"{reverse('workspace')}?tab=ended", fetch_redirect_response=False)
                self.assertContains(follow(self.client, response), escape(text))

    def test_unreachable_service_warns_not_to_repeat(self):
        with patch_service("repost_listing", side_effect=ServiceUnavailable("marketplace")):
            response = follow(self.client, self.client.post(self.url))

        self.assertContains(response, "couldn&#x27;t confirm whether the offer was reposted")

    def test_only_post(self):
        with patch_service("repost_listing") as api:
            self.assertEqual(self.client.get(self.url).status_code, 405)
        api.assert_not_called()

    def test_signed_out_is_sent_to_sign_in(self):
        self.client = self.client_class()
        with patch_service("repost_listing") as api:
            response = self.client.post(self.url)

        self.assertEqual(response.status_code, 302)
        api.assert_not_called()
