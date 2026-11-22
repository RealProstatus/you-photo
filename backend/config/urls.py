"""Корневая маршрутизация URL для проекта YouPhoto."""

from __future__ import annotations

import importlib.util

from django.conf import settings
from django.conf.urls.static import static
from django.contrib import admin
from django.urls import include, path


def _module_exists(module_name: str) -> bool:
    try:
        return importlib.util.find_spec(module_name) is not None
    except (ModuleNotFoundError, ValueError):
        return False


urlpatterns = [
    path("admin/", admin.site.urls),
]

if _module_exists("apps.notifications.urls"):
    urlpatterns.append(path("api/v1/notifications/", include("apps.notifications.urls")))

if _module_exists("api.urls"):
    urlpatterns.append(path("api/v1/", include("api.urls")))

if _module_exists("apps.web.urls"):
    urlpatterns.append(path("", include("apps.web.urls")))

if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
