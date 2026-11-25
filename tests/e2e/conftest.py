"""Self-contained pytest fixtures for the YouPhoto opaque-box E2E test suite."""

from __future__ import annotations

import io
from collections.abc import Callable
from dataclasses import dataclass, field
from decimal import Decimal
from types import SimpleNamespace
from typing import Any

import pytest
from apps.core.enums import UserRole


@pytest.fixture()
def e2e_env(tmp_path: Any, settings: Any, monkeypatch: pytest.MonkeyPatch) -> Any:
    """Configure an isolated deterministic local environment for E2E tests."""
    media_root = tmp_path / "media"
    media_root.mkdir(parents=True, exist_ok=True)

    settings.MEDIA_ROOT = str(media_root)
    settings.USE_MINIO = False
    settings.TELEGRAM_BOT_TOKEN = getattr(
        settings, "TELEGRAM_BOT_TOKEN", "123456:TEST_BOT_TOKEN"
    ) or "123456:TEST_BOT_TOKEN"
    settings.TELEGRAM_BOT_USERNAME = getattr(
        settings, "TELEGRAM_BOT_USERNAME", "youphoto_test_bot"
    ) or "youphoto_test_bot"

    monkeypatch.setenv("USE_MINIO", "False")
    monkeypatch.setenv("YOUPHOTO_HARDWARE_MOCK", "True")
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", settings.TELEGRAM_BOT_TOKEN)
    monkeypatch.setenv("TELEGRAM_BOT_USERNAME", settings.TELEGRAM_BOT_USERNAME)
    return settings


@pytest.fixture()
def e2e_image_factory() -> Callable[[str, tuple[int, int], tuple[int, int, int]], bytes]:
    """Return an in-memory image generator supporting JPEG, PNG, TIFF, BMP, GIF, WEBP."""
    from PIL import Image

    def _make_image(
        fmt: str = "JPEG",
        size: tuple[int, int] = (800, 600),
        color: tuple[int, int, int] = (120, 180, 220),
    ) -> bytes:
        normalized = fmt.upper()
        supported = {"JPEG", "PNG", "TIFF", "BMP", "GIF", "WEBP"}
        if normalized not in supported:
            raise ValueError(f"Unsupported image format: {fmt}")
        buf = io.BytesIO()
        img = Image.new("RGB", size, color)
        img.save(buf, format=normalized)
        return buf.getvalue()

    return _make_image


@pytest.fixture()
def e2e_seed_data(db: Any, e2e_env: Any) -> SimpleNamespace:
    """Seed canonical salons, equipment, services, and users for E2E tests."""
    from apps.core.models import Equipment, Salon, Service
    from django.contrib.auth import get_user_model
    from django.utils import timezone

    User = get_user_model()

    salon_a = Salon.objects.create(
        name="Салон Центр",
        address="ул. Большая Покровская, д. 10",
        is_active=True,
        delivery_cost=Decimal("150.00"),
    )
    salon_b = Salon.objects.create(
        name="Салон Восток",
        address="пр. Гагарина, д. 55",
        is_active=True,
        delivery_cost=Decimal("200.00"),
    )
    salon_hidden = Salon.objects.create(
        name="Салон Архивный",
        address="ул. Скрытая, д. 99",
        is_active=False,
        delivery_cost=Decimal("300.00"),
    )

    passwords: dict[str, str] = {
        "admin@youphoto.ru": "AdminPass123!",
        "manager.a@youphoto.ru": "ManagerPass123!",
        "manager.b@youphoto.ru": "ManagerPass123!",
        "client1@youphoto.ru": "ClientPass123!",
        "client2@youphoto.ru": "ClientPass123!",
        "blocked@youphoto.ru": "BlockedPass123!",
    }

    admin = User.objects.create_user(
        email="admin@youphoto.ru",
        phone="+79001110001",
        name="Администратор Системы",
        password=passwords["admin@youphoto.ru"],
        role=UserRole.ADMIN,
    )
    if hasattr(admin, "is_staff") and not admin.is_staff:
        admin.is_staff = True
        admin.save(update_fields=["is_staff"])

    manager_a = User.objects.create_user(
        email="manager.a@youphoto.ru",
        phone="+79001110002",
        name="Менеджер Центр",
        password=passwords["manager.a@youphoto.ru"],
        role=UserRole.MANAGER,
        salon=salon_a,
    )
    manager_b = User.objects.create_user(
        email="manager.b@youphoto.ru",
        phone="+79001110003",
        name="Менеджер Восток",
        password=passwords["manager.b@youphoto.ru"],
        role=UserRole.MANAGER,
        salon=salon_b,
    )
    client_1 = User.objects.create_user(
        email="client1@youphoto.ru",
        phone="+79001110004",
        name="Иван Клиентов",
        password=passwords["client1@youphoto.ru"],
        role=UserRole.CLIENT,
        pdn_consent_at=timezone.now(),
    )
    client_2 = User.objects.create_user(
        email="client2@youphoto.ru",
        phone="+79001110005",
        name="Мария Покупателева",
        password=passwords["client2@youphoto.ru"],
        role=UserRole.CLIENT,
    )
    blocked_user = User.objects.create_user(
        email="blocked@youphoto.ru",
        phone="+79001110006",
        name="Заблокированный Пользователь",
        password=passwords["blocked@youphoto.ru"],
        role=UserRole.CLIENT,
        is_blocked=True,
    )

    eq_a_std = Equipment.objects.create(
        salon=salon_a,
        name="Фотопринтер А-1",
        equipment_type="PHOTO_PRINTER",
        max_width_cm=30.0,
        max_height_cm=45.0,
        is_operational=True,
    )
    eq_b_std = Equipment.objects.create(
        salon=salon_b,
        name="Фотопринтер Б-1",
        equipment_type="PHOTO_PRINTER",
        max_width_cm=30.0,
        max_height_cm=45.0,
        is_operational=True,
    )
    eq_b_wide = Equipment.objects.create(
        salon=salon_b,
        name="Плоттер Б-Широкоформатный",
        equipment_type="WIDE_FORMAT",
        max_width_cm=100.0,
        max_height_cm=150.0,
        is_operational=True,
    )

    service_standard = Service.objects.create(
        name="Стандартная фотопечать",
        service_type="standard_print",
        base_price=Decimal("50.00"),
        price_per_sq_cm=Decimal("0.2000"),
        allowed_materials=["Глянцевая", "Матовая"],
        allowed_options={"Без рамки": 0, "Ламинация": 100},
        required_equipment_type="PHOTO_PRINTER",
    )
    service_standard.salons.add(salon_a, salon_b)

    service_wide = Service.objects.create(
        name="Широкоформатная печать",
        service_type="large_format",
        base_price=Decimal("300.00"),
        price_per_sq_cm=Decimal("0.5000"),
        allowed_materials=["Холст", "Баннер"],
        allowed_options={"Установка люверсов": 200},
        required_equipment_type="WIDE_FORMAT",
    )
    service_wide.salons.add(salon_b)

    return SimpleNamespace(
        admin=admin,
        manager_a=manager_a,
        manager_b=manager_b,
        client_1=client_1,
        client_2=client_2,
        blocked_user=blocked_user,
        salon_a=salon_a,
        salon_b=salon_b,
        salon_hidden=salon_hidden,
        eq_a_std=eq_a_std,
        eq_b_std=eq_b_std,
        eq_b_wide=eq_b_wide,
        service_standard=service_standard,
        service_wide=service_wide,
        passwords=passwords,
    )


@pytest.fixture()
def api_client_factory(e2e_env: Any) -> Callable[[str | None, str | None], Any]:
    """Return a factory building a DRF APIClient, optionally logged in via API."""
    from rest_framework.test import APIClient

    def _make_client(
        login_str: str | None = None,
        password: str | None = None,
    ) -> APIClient:
        client = APIClient()
        if login_str is not None and password is not None:
            resp = client.post(
                "/api/v1/auth/login/",
                {"login": login_str, "password": password},
                format="json",
            )
            assert resp.status_code == 200, (
                f"Login failed for {login_str!r}: {resp.status_code} {resp.content!r}"
            )
            payload = resp.json()
            token = payload.get("token")
            assert token, f"Missing auth token in login response: {payload}"
            client.credentials(HTTP_AUTHORIZATION=f"Token {token}")
        return client

    return _make_client


@pytest.fixture()
def upload_photo_via_api() -> Callable[[Any, bytes, str, str], Any]:
    """Return a helper uploading multipart photo bytes to /api/v1/photos/upload/."""
    from django.core.files.uploadedfile import SimpleUploadedFile

    def _upload(
        client: Any,
        file_bytes: bytes,
        filename: str = "photo.jpg",
        content_type: str = "image/jpeg",
    ) -> Any:
        uploaded = SimpleUploadedFile(filename, file_bytes, content_type=content_type)
        return client.post(
            "/api/v1/photos/upload/",
            {"file": uploaded},
            format="multipart",
        )

    return _upload


@pytest.fixture()
def resolve_web_url() -> Callable[..., str]:
    """Resolve client web URLs opaquely via candidate URL names and path probes."""
    from django.test import Client
    from django.urls import NoReverseMatch, Resolver404, resolve, reverse

    candidate_names: dict[str, list[str]] = {
        "register": ["register", "web:register", "auth_register", "web:auth_register"],
        "login": ["login", "web:login", "auth_login", "web:auth_login"],
        "logout": ["logout", "web:logout", "auth_logout", "web:auth_logout"],
        "profile": ["profile", "web:profile", "cabinet", "web:cabinet"],
        "order_create": [
            "order_create",
            "web:order_create",
            "orders_create",
            "web:orders_create",
        ],
        "order_list": [
            "order_list",
            "web:order_list",
            "orders_list",
            "web:orders_list",
            "orders",
            "web:orders",
        ],
        "order_detail": [
            "order_detail",
            "web:order_detail",
            "orders_detail",
            "web:orders_detail",
        ],
    }

    def _candidate_paths(route_key: str, kwargs: dict[str, Any]) -> list[str]:
        oid = kwargs.get("order_id") or kwargs.get("pk") or kwargs.get("id")
        if route_key == "register":
            return ["/register/", "/auth/register/", "/accounts/register/"]
        if route_key == "login":
            return ["/login/", "/auth/login/", "/accounts/login/"]
        if route_key == "logout":
            return ["/logout/", "/auth/logout/", "/accounts/logout/"]
        if route_key == "profile":
            return ["/profile/", "/accounts/profile/", "/cabinet/"]
        if route_key == "order_create":
            return ["/orders/create/", "/order/create/", "/orders/new/"]
        if route_key == "order_list":
            return ["/orders/", "/orders/list/", "/"]
        if route_key == "order_detail" and oid is not None:
            return [f"/orders/{oid}/", f"/order/{oid}/"]
        return [f"/{route_key.replace('_', '/')}/"]

    def _resolve(route_key: str, **kwargs: Any) -> str:
        names = candidate_names.get(route_key, [route_key, f"web:{route_key}"])
        for name in names:
            try:
                return reverse(name, kwargs=kwargs or None)
            except NoReverseMatch:
                if kwargs:
                    for alt_key in ("pk", "id", "order_id"):
                        val = next(iter(kwargs.values()))
                        try:
                            return reverse(name, kwargs={alt_key: val})
                        except NoReverseMatch:
                            continue
                continue

        probe_client = Client()
        for path in _candidate_paths(route_key, kwargs):
            try:
                resolve(path)
                return path
            except Resolver404:
                if route_key != "logout":
                    resp = probe_client.get(path, follow=False)
                    if resp.status_code != 404:
                        return path

        raise AssertionError(
            f"Could not resolve web URL for route_key={route_key!r} with kwargs={kwargs!r}"
        )

    return _resolve


@dataclass
class _AppriseMockController:
    """Captured Telegram notification state and failure simulator for tests."""

    calls: list[dict[str, Any]] = field(default_factory=list)
    added_urls: list[str] = field(default_factory=list)
    should_fail: bool = False
    exception: Exception | None = None

    def set_fail(self, fail: bool = True) -> None:
        self.should_fail = fail

    def set_exception(self, exc: Exception | None) -> None:
        self.exception = exc

    def reset(self) -> None:
        self.calls.clear()
        self.added_urls.clear()
        self.should_fail = False
        self.exception = None


@pytest.fixture()
def mock_apprise_send(monkeypatch: pytest.MonkeyPatch) -> _AppriseMockController:
    """Patch apprise.Apprise.notify to capture Telegram payloads without network calls."""
    import apprise

    controller = _AppriseMockController()
    original_add = apprise.Apprise.add

    def _fake_add(self: Any, servers: Any, *args: Any, **kwargs: Any) -> Any:
        if isinstance(servers, str):
            controller.added_urls.append(servers)
        elif isinstance(servers, (list, tuple)):
            controller.added_urls.extend(str(item) for item in servers)
        return original_add(self, servers, *args, **kwargs)

    def _fake_notify(
        self: Any,
        body: str = "",
        title: str = "",
        *args: Any,
        **kwargs: Any,
    ) -> bool:
        urls: list[str] = list(self.urls()) if hasattr(self, "urls") else []
        if not urls and hasattr(self, "__iter__"):
            urls = [str(srv.url()) if hasattr(srv, "url") else repr(srv) for srv in self]

        controller.calls.append(
            {
                "title": title,
                "body": body,
                "urls": urls,
                "args": args,
                "kwargs": kwargs,
            }
        )
        if controller.exception is not None:
            raise controller.exception
        return not controller.should_fail

    monkeypatch.setattr(apprise.Apprise, "add", _fake_add)
    monkeypatch.setattr(apprise.Apprise, "notify", _fake_notify)
    return controller
