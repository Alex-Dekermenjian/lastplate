"""
Liveness and readiness endpoints.

Liveness proves the process is serving requests. Readiness proves the service
can reach its own database (the one named in its settings) with every
migration applied. Each service keeps its own copy of this module so that
services stay independently deployable.
"""

from django.conf import settings
from django.db import DatabaseError, connection
from django.db.migrations.executor import MigrationExecutor
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView


class HealthView(APIView):
    authentication_classes = []
    permission_classes = [AllowAny]


class LiveView(HealthView):
    def get(self, request):
        return Response({"service": settings.SERVICE_NAME, "status": "ok"})


class ReadyView(HealthView):
    def get(self, request):
        checks = {}
        body = {"service": settings.SERVICE_NAME, "status": "unavailable", "checks": checks}
        expected = connection.settings_dict["NAME"]

        try:
            with connection.cursor() as cursor:
                cursor.execute("SELECT current_database()")
                (database,) = cursor.fetchone()
            executor = MigrationExecutor(connection)
            pending = executor.migration_plan(executor.loader.graph.leaf_nodes())
        except DatabaseError:
            checks["database"] = "unreachable"
            return Response(body, status=503)

        checks["database"] = "ok" if database == expected else "unexpected database"
        checks["migrations"] = "ok" if not pending else f"{len(pending)} unapplied"
        if settings.HEALTH_EXPOSE_DETAILS:
            body["database"] = database

        if all(result == "ok" for result in checks.values()):
            body["status"] = "ready"
            return Response(body)
        return Response(body, status=503)
