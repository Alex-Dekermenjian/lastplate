"""LP-46: the demo works through a Cloudflare quick tunnel
(https://<random>.trycloudflare.com -> http://localhost:8000)."""

from unittest import mock
from urllib.error import HTTPError, URLError

from django.conf import settings
from django.test import Client, TestCase, override_settings
from django.urls import reverse

from .test_accounts import PASSWORD, TOKENS, patch_service
from .test_browse_offers import offer
from .test_lp57_console import ADMIN, SUMMARY, ConsoleTestCase

TUNNEL = "quiet-river-demo.trycloudflare.com"
# What cloudflared sends to the web service.
VIA_TUNNEL = {"HTTP_HOST": TUNNEL, "HTTP_X_FORWARDED_PROTO": "https"}


class AllowedHostTests(TestCase):
    def test_settings_add_the_tunnel_and_keep_localhost(self):
        self.assertIn(".trycloudflare.com", settings.ALLOWED_HOSTS)
        self.assertIn("127.0.0.1", settings.ALLOWED_HOSTS)
        self.assertIn("localhost", settings.ALLOWED_HOSTS)

    def test_tunnel_host_is_accepted(self):
        response = self.client.get(reverse("health-live"), **VIA_TUNNEL)
        self.assertEqual(response.status_code, 200)

    def test_localhost_still_works(self):
        for host in ("127.0.0.1:8000", "localhost:8000"):
            with self.subTest(host=host):
                self.assertEqual(self.client.get(reverse("health-live"), HTTP_HOST=host).status_code, 200)

    def test_other_hosts_are_refused(self):
        response = self.client.get(reverse("health-live"), HTTP_HOST="evil.example")
        self.assertEqual(response.status_code, 400)

    def test_forwarded_https_is_trusted(self):
        self.assertEqual(settings.SECURE_PROXY_SSL_HEADER, ("HTTP_X_FORWARDED_PROTO", "https"))
        request = self.client.get(reverse("health-live"), **VIA_TUNNEL).wsgi_request
        self.assertTrue(request.is_secure())


class CsrfOriginTests(TestCase):
    def setUp(self):
        self.browser = Client(enforce_csrf_checks=True)
        self.browser.get(reverse("login"), **VIA_TUNNEL)
        self.token = self.browser.cookies[settings.CSRF_COOKIE_NAME].value

    def post_login(self, origin):
        with patch_service("sign_in", return_value=(401, {})):
            return self.browser.post(
                reverse("login"),
                {"email": "someone@example.test", "password": PASSWORD, "csrfmiddlewaretoken": self.token},
                HTTP_ORIGIN=origin,
                **VIA_TUNNEL,
            )

    def test_tunnel_origin_is_trusted(self):
        self.assertIn("https://*.trycloudflare.com", settings.CSRF_TRUSTED_ORIGINS)
        response = self.post_login(f"https://{TUNNEL}")
        self.assertEqual(response.status_code, 200)  # the form, with "wrong email or password"

    def test_other_origins_are_refused(self):
        self.assertEqual(self.post_login("https://evil.example").status_code, 403)


class TunnelPagesTests(ConsoleTestCase):
    def test_admin_login_and_console_work_through_the_tunnel(self):
        with patch_service("sign_in", return_value=(200, TOKENS)), \
                patch_service("account", return_value=(200, ADMIN)):
            response = self.client.post(reverse("login"), {"email": ADMIN["email"], "password": PASSWORD}, **VIA_TUNNEL)
        self.assertRedirects(response, reverse("console"), fetch_redirect_response=False)

        with patch_service("admin_summary", return_value=(200, SUMMARY)):
            response = self.client.get(reverse("console"), **VIA_TUNNEL)
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, "127.0.0.1")

    @override_settings(DEBUG=True)
    def test_offer_photos_are_links_on_this_site(self):
        photo = "/media/listing_photos/0123abcd.png"
        with patch_service("browse_listings", return_value=(200, [offer(photo_display=photo)])):
            response = self.client.get(reverse("browse-offers"), **VIA_TUNNEL)
        self.assertContains(response, f'src="{photo}"')
        self.assertNotContains(response, "127.0.0.1")
        self.assertNotContains(response, ":8002")


class FakeUpstream:
    def __init__(self, body):
        self.body = body

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def read(self, limit=-1):
        return self.body if limit < 0 else self.body[:limit]


PNG = b"\x89PNG\r\n\x1a\n" + b"fake-image-bytes"


@override_settings(SERVICE_URLS={**settings.SERVICE_URLS, "marketplace": "http://marketplace:8000"})
class MediaProxyTests(TestCase):
    URL = "/media/listing_photos/0123abcd.png"

    def fetch(self, url=URL, **upstream):
        with mock.patch("frontend.listing_photos.urlopen", **upstream) as opened:
            response = self.client.get(url, **VIA_TUNNEL)
        return response, opened

    def test_returns_the_image_from_marketplace(self):
        response, opened = self.fetch(return_value=FakeUpstream(PNG))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "image/png")
        self.assertEqual(response.content, PNG)
        request = opened.call_args.args[0]
        self.assertEqual(request.full_url, "http://marketplace:8000/media/listing_photos/0123abcd.png")
        self.assertEqual(request.get_method(), "GET")

    def test_jpeg_type(self):
        response, _ = self.fetch("/media/shop_photos/beef.jpg", return_value=FakeUpstream(b"jpeg"))
        self.assertEqual(response["Content-Type"], "image/jpeg")

    def test_missing_photo_is_404(self):
        response, _ = self.fetch(side_effect=HTTPError(self.URL, 404, "Not Found", {}, None))
        self.assertEqual(response.status_code, 404)

    def test_marketplace_down_is_502(self):
        response, _ = self.fetch(side_effect=URLError("refused"))
        self.assertEqual(response.status_code, 502)

    def test_unsafe_paths_never_reach_marketplace(self):
        for url in (
            "/media/../health/live/",
            "/media/listing_photos/%2e%2e/secret.png",
            "/media/.env",
            "/media/listing_photos/notes.txt",
            "/media/listing_photos/.hidden.png",
        ):
            with self.subTest(url=url):
                response, opened = self.fetch(url, return_value=FakeUpstream(PNG))
                self.assertEqual(response.status_code, 404)
                opened.assert_not_called()

    def test_only_get(self):
        response = self.client.post(self.URL, **VIA_TUNNEL)
        self.assertEqual(response.status_code, 405)
