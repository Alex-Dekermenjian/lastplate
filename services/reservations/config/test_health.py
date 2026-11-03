from unittest import mock

from django.db import OperationalError, connection
from django.test import TestCase, override_settings
from django.urls import reverse


class HealthEndpointTests(TestCase):
    def test_live_reports_this_service(self):
        response = self.client.get(reverse("health-live"))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"service": "reservations", "status": "ok"})

    @override_settings(HEALTH_EXPOSE_DETAILS=True)
    def test_ready_uses_this_services_database_with_migrations_applied(self):
        response = self.client.get(reverse("health-ready"))
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["status"], "ready")
        self.assertEqual(body["checks"], {"database": "ok", "migrations": "ok"})
        self.assertEqual(body["database"], connection.settings_dict["NAME"])

    @override_settings(HEALTH_EXPOSE_DETAILS=False)
    def test_ready_hides_database_name_unless_enabled(self):
        response = self.client.get(reverse("health-ready"))
        self.assertNotIn("database", response.json())

    def test_ready_fails_when_database_is_unreachable(self):
        with mock.patch.object(connection, "cursor", side_effect=OperationalError):
            response = self.client.get(reverse("health-ready"))
        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.json()["checks"], {"database": "unreachable"})

    def test_ready_fails_with_unapplied_migrations(self):
        with mock.patch(
            "config.health.MigrationExecutor.migration_plan", return_value=[object()]
        ):
            response = self.client.get(reverse("health-ready"))
        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.json()["checks"]["migrations"], "1 unapplied")
