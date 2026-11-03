from unittest import mock
from urllib.error import URLError

from django.conf import settings
from django.contrib.sessions.middleware import SessionMiddleware
from django.contrib.sessions.models import Session
from django.db import OperationalError, connection
from django.http import HttpResponse
from django.test import RequestFactory, TestCase, override_settings
from django.urls import reverse

from frontend import tokens
from frontend.views import fetch_service_status


class HealthEndpointTests(TestCase):
    def test_live_reports_this_service(self):
        response = self.client.get(reverse("health-live"))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"service": "web", "status": "ok"})

    @override_settings(HEALTH_EXPOSE_DETAILS=True)
    def test_ready_uses_the_session_database_with_migrations_applied(self):
        response = self.client.get(reverse("health-ready"))
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["checks"], {"database": "ok", "migrations": "ok"})
        self.assertEqual(body["database"], connection.settings_dict["NAME"])

    def test_ready_fails_when_database_is_unreachable(self):
        with mock.patch.object(connection, "cursor", side_effect=OperationalError):
            response = self.client.get(reverse("health-ready"))
        self.assertEqual(response.status_code, 503)


class ServerSideTokenStorageTests(TestCase):
    def test_sessions_are_database_backed_not_signed_cookies(self):
        self.assertEqual(settings.SESSION_ENGINE, "django.contrib.sessions.backends.db")
        self.assertTrue(settings.SESSION_COOKIE_HTTPONLY)

    def test_tokens_stay_on_the_server_and_cookie_holds_only_the_session_id(self):
        def sign_in(request):
            tokens.store_tokens(request, "access-test-value", "refresh-test-value")
            return HttpResponse()

        request = RequestFactory().get("/")
        response = SessionMiddleware(sign_in)(request)

        cookie = response.cookies[settings.SESSION_COOKIE_NAME]
        self.assertNotIn("access-test-value", cookie.value)
        self.assertNotIn("refresh-test-value", cookie.value)
        self.assertTrue(cookie["httponly"])
        stored = Session.objects.get(session_key=cookie.value).get_decoded()
        self.assertEqual(stored[tokens.ACCESS_TOKEN_KEY], "access-test-value")
        self.assertEqual(stored[tokens.REFRESH_TOKEN_KEY], "refresh-test-value")

    def test_clearing_tokens_deletes_the_server_side_session(self):
        session = self.client.session
        session[tokens.ACCESS_TOKEN_KEY] = "access-test-value"
        session.save()
        request = RequestFactory().get("/")
        request.session = session

        tokens.clear_tokens(request)

        self.assertFalse(Session.objects.filter(session_key=session.session_key).exists())
        self.assertIsNone(tokens.get_access_token(request))


class StatusPageTests(TestCase):
    def test_home_page_lists_each_service(self):
        with mock.patch("frontend.views.urlopen", side_effect=URLError("down")):
            response = self.client.get(reverse("home"))
        self.assertEqual(response.status_code, 200)
        for name in ("accounts", "marketplace", "reservations"):
            self.assertContains(response, name)
        self.assertContains(response, "unreachable", count=3)

    def test_ready_service_is_reported_ready(self):
        upstream = mock.MagicMock()
        upstream.__enter__.return_value.read.return_value = b'{"status": "ready"}'
        with mock.patch("frontend.views.urlopen", return_value=upstream):
            status = fetch_service_status("accounts", "http://accounts.test")
        self.assertEqual(status["state"], "ready")
