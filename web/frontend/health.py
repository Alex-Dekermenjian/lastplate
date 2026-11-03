"""
Liveness and readiness endpoints for the web frontend.

Readiness checks the frontend's own session database. It deliberately does not
depend on the other services, so the frontend can start on its own.
"""

from django.conf import settings
from django.db import DatabaseError, connection
from django.db.migrations.executor import MigrationExecutor
from django.http import JsonResponse
from django.views.decorators.http import require_GET


@require_GET
def live(request):
    return JsonResponse({"service": settings.SERVICE_NAME, "status": "ok"})


@require_GET
def ready(request):
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
        return JsonResponse(body, status=503)

    checks["database"] = "ok" if database == expected else "unexpected database"
    checks["migrations"] = "ok" if not pending else f"{len(pending)} unapplied"
    if settings.HEALTH_EXPOSE_DETAILS:
        body["database"] = database

    if all(result == "ok" for result in checks.values()):
        body["status"] = "ready"
        return JsonResponse(body)
    return JsonResponse(body, status=503)
