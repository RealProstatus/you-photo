"""Tier 1 opaque-box E2E tests for Features 1-5 (Backend, Storage, Notifications, Routing, API)."""

from __future__ import annotations

import inspect
import uuid
from datetime import timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
from apps.core.enums import (
    ORDER_STATUS_LABELS_RU,
    OrderStatus,
    OrderType,
    UserRole,
)

pytestmark = [pytest.mark.django_db(transaction=True), pytest.mark.integration]


def _extract_id(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, dict):
        raw = value.get("id")
        return str(raw) if raw is not None else None
    return str(value)


def _locate_local_media_file(media_root: str, file_key: str) -> Path | None:
    root = Path(media_root)
    candidates = [root / "storage" / file_key, root / file_key]
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    return None


def _create_online_order_via_api(
    client: Any,
    upload_fn: Any,
    image_bytes: bytes,
    pickup_salon_id: str,
    service_id: str,
    width_cm: float = 10.0,
    height_cm: float = 15.0,
    quantity: int = 1,
    material: str = "Глянцевая",
    options: list[str] | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    upload_resp = upload_fn(client, image_bytes, filename="order_photo.jpg")
    assert upload_resp.status_code in (200, 201), upload_resp.content
    photo_data = upload_resp.json()

    order_payload = {
        "pickup_salon_id": pickup_salon_id,
        "items": [
            {
                "photo_id": photo_data["id"],
                "service_id": service_id,
                "width_cm": width_cm,
                "height_cm": height_cm,
                "quantity": quantity,
                "material": material,
                "options": options if options is not None else [],
            }
        ],
    }
    order_resp = client.post("/api/v1/orders/", order_payload, format="json")
    assert order_resp.status_code == 201, order_resp.content
    return order_resp.json(), photo_data


# ---------------------------------------------------------------------------
# Feature 1: Django Config, Models, Seed & Migrations (>= 5 tests)
# ---------------------------------------------------------------------------


def test_t1_f1_migrate_check_no_pending_migrations(e2e_env: Any) -> None:
    """Verify database migrations apply cleanly with zero pending migrations."""
    from django.core.management import call_command

    call_command("migrate", "--check")
    call_command("makemigrations", "--check", "--dry-run")


def test_t1_f1_seed_initial_data_idempotent_and_populates_records(e2e_env: Any) -> None:
    """Verify seed_initial_data populates core tables and is idempotent across runs."""
    from apps.core.models import Equipment, Salon, Service
    from django.contrib.auth import get_user_model
    from django.core.management import call_command

    User = get_user_model()

    call_command("seed_initial_data")
    first_counts = (
        Salon.objects.count(),
        Equipment.objects.count(),
        Service.objects.count(),
        User.objects.count(),
    )
    assert all(count > 0 for count in first_counts), f"Expected seeded records, got {first_counts}"

    call_command("seed_initial_data")
    second_counts = (
        Salon.objects.count(),
        Equipment.objects.count(),
        Service.objects.count(),
        User.objects.count(),
    )
    assert second_counts == first_counts


def test_t1_f1_core_models_use_uuid_primary_keys(
    e2e_seed_data: Any,
    e2e_image_factory: Any,
    api_client_factory: Any,
    upload_photo_via_api: Any,
) -> None:
    """Verify all 8 core models use uuid.UUID primary keys."""
    from apps.core.models import (
        Equipment,
        Order,
        OrderItem,
        OrderStatusHistory,
        PhotoFile,
        Salon,
        Service,
        User,
    )
    from django.db.models import UUIDField

    for model_cls in (
        User,
        Salon,
        Equipment,
        Service,
        Order,
        OrderItem,
        PhotoFile,
        OrderStatusHistory,
    ):
        assert isinstance(model_cls._meta.pk, UUIDField), (
            f"{model_cls.__name__}.pk must be UUIDField"
        )

    client = api_client_factory(
        e2e_seed_data.client_1.email,
        e2e_seed_data.passwords[e2e_seed_data.client_1.email],
    )
    order_json, _ = _create_online_order_via_api(
        client,
        upload_photo_via_api,
        e2e_image_factory("JPEG"),
        str(e2e_seed_data.salon_a.id),
        str(e2e_seed_data.service_standard.id),
    )

    order = Order.objects.get(id=order_json["id"])
    item = order.items.first()
    photo = item.photo
    history = order.history.first()

    for instance in (
        e2e_seed_data.admin,
        e2e_seed_data.salon_a,
        e2e_seed_data.eq_a_std,
        e2e_seed_data.service_standard,
        order,
        item,
        photo,
        history,
    ):
        assert isinstance(instance.id, uuid.UUID)


def test_t1_f1_user_roles_and_password_hashing(e2e_seed_data: Any) -> None:
    """Verify User roles and secure password hashing (never stored in plaintext)."""
    users_and_roles = [
        (e2e_seed_data.admin, UserRole.ADMIN),
        (e2e_seed_data.manager_a, UserRole.MANAGER),
        (e2e_seed_data.client_1, UserRole.CLIENT),
    ]
    for user, expected_role in users_and_roles:
        raw_pw = e2e_seed_data.passwords[user.email]
        assert user.role == expected_role
        assert user.has_usable_password()
        assert user.password != raw_pw
        assert raw_pw not in user.password
        assert user.check_password(raw_pw)


def test_t1_f1_order_status_10_russian_labels_and_history_audit_trail(
    e2e_seed_data: Any,
    e2e_image_factory: Any,
    api_client_factory: Any,
    upload_photo_via_api: Any,
) -> None:
    """Verify 10 OrderStatus values have Russian labels and transitions record history."""
    from apps.core.models import Order, OrderStatusHistory

    assert len(OrderStatus) == 10
    assert len(ORDER_STATUS_LABELS_RU) == 10
    for status in OrderStatus:
        label = ORDER_STATUS_LABELS_RU[status]
        assert isinstance(label, str) and len(label.strip()) > 0

    client_api = api_client_factory(
        e2e_seed_data.client_1.email,
        e2e_seed_data.passwords[e2e_seed_data.client_1.email],
    )
    order_json, _ = _create_online_order_via_api(
        client_api,
        upload_photo_via_api,
        e2e_image_factory("JPEG"),
        str(e2e_seed_data.salon_a.id),
        str(e2e_seed_data.service_standard.id),
    )

    manager_api = api_client_factory(
        e2e_seed_data.manager_a.email,
        e2e_seed_data.passwords[e2e_seed_data.manager_a.email],
    )
    resp = manager_api.post(
        f"/api/v1/orders/{order_json['id']}/status/",
        {"status": OrderStatus.IN_PROGRESS, "comment": "Взято в работу"},
        format="json",
    )
    assert resp.status_code == 200, resp.content

    order = Order.objects.get(id=order_json["id"])
    history_entries = list(
        OrderStatusHistory.objects.filter(order=order).order_by("changed_at")
    )
    assert len(history_entries) >= 2
    last_entry = history_entries[-1]
    assert last_entry.old_status == OrderStatus.ASSIGNED_TO_SALON
    assert last_entry.new_status == OrderStatus.IN_PROGRESS
    assert last_entry.actor_id == e2e_seed_data.manager_a.id
    assert "Взято в работу" in last_entry.comment


# ---------------------------------------------------------------------------
# Feature 2: File Storage, Validation & 30d Retention (>= 5 tests)
# ---------------------------------------------------------------------------


def test_t1_f2_upload_jpeg_creates_metadata_and_local_file(
    e2e_env: Any,
    e2e_seed_data: Any,
    e2e_image_factory: Any,
    api_client_factory: Any,
    upload_photo_via_api: Any,
) -> None:
    """Verify JPEG upload creates PhotoFile DB metadata and local file under MEDIA_ROOT."""
    from apps.core.models import PhotoFile

    client_api = api_client_factory(
        e2e_seed_data.client_1.email,
        e2e_seed_data.passwords[e2e_seed_data.client_1.email],
    )
    raw_bytes = e2e_image_factory("JPEG", size=(640, 480))
    resp = upload_photo_via_api(client_api, raw_bytes, "holiday.jpg", "image/jpeg")
    assert resp.status_code in (200, 201), resp.content
    data = resp.json()

    assert data["original_name"] == "holiday.jpg"
    assert data["file_format"] == "JPEG"
    assert data["size_bytes"] == len(raw_bytes)
    assert data["download_url"]

    photo = PhotoFile.objects.get(id=data["id"])
    assert photo.file_key == data["file_key"]
    assert photo.is_deleted_from_storage is False

    local_path = _locate_local_media_file(e2e_env.MEDIA_ROOT, photo.file_key)
    assert local_path is not None, f"Local file missing for key {photo.file_key}"
    assert local_path.read_bytes() == raw_bytes


def test_t1_f2_upload_png_accepted_and_stored(
    e2e_env: Any,
    e2e_seed_data: Any,
    e2e_image_factory: Any,
    api_client_factory: Any,
    upload_photo_via_api: Any,
) -> None:
    """Verify PNG upload is accepted, validated, and stored locally."""
    client_api = api_client_factory(
        e2e_seed_data.client_1.email,
        e2e_seed_data.passwords[e2e_seed_data.client_1.email],
    )
    raw_bytes = e2e_image_factory("PNG", size=(500, 500))
    resp = upload_photo_via_api(client_api, raw_bytes, "portrait.png", "image/png")
    assert resp.status_code in (200, 201), resp.content
    data = resp.json()

    assert data["file_format"] == "PNG"
    assert data["size_bytes"] == len(raw_bytes)
    local_path = _locate_local_media_file(e2e_env.MEDIA_ROOT, data["file_key"])
    assert local_path is not None
    assert local_path.read_bytes() == raw_bytes


def test_t1_f2_upload_tiff_accepted_and_stored(
    e2e_env: Any,
    e2e_seed_data: Any,
    e2e_image_factory: Any,
    api_client_factory: Any,
    upload_photo_via_api: Any,
) -> None:
    """Verify TIFF upload is accepted, validated, and stored locally."""
    client_api = api_client_factory(
        e2e_seed_data.client_1.email,
        e2e_seed_data.passwords[e2e_seed_data.client_1.email],
    )
    raw_bytes = e2e_image_factory("TIFF", size=(400, 300))
    resp = upload_photo_via_api(client_api, raw_bytes, "scan.tiff", "image/tiff")
    assert resp.status_code in (200, 201), resp.content
    data = resp.json()

    assert data["file_format"] == "TIFF"
    assert data["size_bytes"] == len(raw_bytes)
    local_path = _locate_local_media_file(e2e_env.MEDIA_ROOT, data["file_key"])
    assert local_path is not None
    assert local_path.read_bytes() == raw_bytes


def test_t1_f2_authorized_photo_download_returns_original_bytes(
    e2e_seed_data: Any,
    e2e_image_factory: Any,
    api_client_factory: Any,
    upload_photo_via_api: Any,
) -> None:
    """Verify owner client, assigned salon manager, and admin can download order photo."""
    raw_bytes = e2e_image_factory("JPEG", size=(320, 240))
    client_api = api_client_factory(
        e2e_seed_data.client_1.email,
        e2e_seed_data.passwords[e2e_seed_data.client_1.email],
    )
    _, photo_data = _create_online_order_via_api(
        client_api,
        upload_photo_via_api,
        raw_bytes,
        str(e2e_seed_data.salon_a.id),
        str(e2e_seed_data.service_standard.id),
    )

    manager_a_api = api_client_factory(
        e2e_seed_data.manager_a.email,
        e2e_seed_data.passwords[e2e_seed_data.manager_a.email],
    )
    admin_api = api_client_factory(
        e2e_seed_data.admin.email,
        e2e_seed_data.passwords[e2e_seed_data.admin.email],
    )

    download_url = f"/api/v1/photos/{photo_data['id']}/download/"
    for actor_client in (client_api, manager_a_api, admin_api):
        dl_resp = actor_client.get(download_url)
        assert dl_resp.status_code == 200, dl_resp.content
        downloaded_bytes = b"".join(dl_resp.streaming_content) if getattr(
            dl_resp, "streaming", False
        ) else dl_resp.content
        assert downloaded_bytes == raw_bytes


def test_t1_f2_purge_expired_photos_deletes_gt_30d_keeps_order_and_lt_30d(
    e2e_env: Any,
    e2e_seed_data: Any,
    e2e_image_factory: Any,
    api_client_factory: Any,
    upload_photo_via_api: Any,
) -> None:
    """Verify purge_expired_photos removes >30d files while keeping Order/History and <30d files."""
    from apps.core.models import Order, OrderStatusHistory, PhotoFile
    from django.core.management import call_command
    from django.utils import timezone

    client_api = api_client_factory(
        e2e_seed_data.client_1.email,
        e2e_seed_data.passwords[e2e_seed_data.client_1.email],
    )
    manager_api = api_client_factory(
        e2e_seed_data.manager_a.email,
        e2e_seed_data.passwords[e2e_seed_data.manager_a.email],
    )

    old_order_json, old_photo_json = _create_online_order_via_api(
        client_api,
        upload_photo_via_api,
        e2e_image_factory("JPEG", size=(300, 300)),
        str(e2e_seed_data.salon_a.id),
        str(e2e_seed_data.service_standard.id),
    )
    fresh_order_json, fresh_photo_json = _create_online_order_via_api(
        client_api,
        upload_photo_via_api,
        e2e_image_factory("PNG", size=(300, 300)),
        str(e2e_seed_data.salon_a.id),
        str(e2e_seed_data.service_standard.id),
    )

    for order_data in (old_order_json, fresh_order_json):
        oid = order_data["id"]
        for target_status in (
            OrderStatus.IN_PROGRESS,
            OrderStatus.PRINTING,
            OrderStatus.READY_FOR_PICKUP,
        ):
            r = manager_api.post(
                f"/api/v1/orders/{oid}/status/",
                {"status": target_status},
                format="json",
            )
            assert r.status_code == 200, r.content
        cp = manager_api.post(
            f"/api/v1/orders/{oid}/complete-pickup/",
            {"pickup_code": order_data["pickup_code"]},
            format="json",
        )
        assert cp.status_code == 200, cp.content

    now = timezone.now()
    old_completed_at = now - timedelta(days=31)
    Order.objects.filter(id=old_order_json["id"]).update(completed_at=old_completed_at)
    PhotoFile.objects.filter(id=old_photo_json["id"]).update(
        delete_after=old_completed_at + timedelta(days=30)
    )

    fresh_completed_at = now - timedelta(days=10)
    Order.objects.filter(id=fresh_order_json["id"]).update(completed_at=fresh_completed_at)
    PhotoFile.objects.filter(id=fresh_photo_json["id"]).update(
        delete_after=fresh_completed_at + timedelta(days=30)
    )

    old_local = _locate_local_media_file(e2e_env.MEDIA_ROOT, old_photo_json["file_key"])
    fresh_local = _locate_local_media_file(e2e_env.MEDIA_ROOT, fresh_photo_json["file_key"])
    assert old_local is not None and fresh_local is not None

    call_command("purge_expired_photos")

    old_photo = PhotoFile.objects.get(id=old_photo_json["id"])
    fresh_photo = PhotoFile.objects.get(id=fresh_photo_json["id"])
    assert old_photo.is_deleted_from_storage is True
    assert not old_local.exists()
    assert Order.objects.filter(id=old_order_json["id"]).exists()
    assert OrderStatusHistory.objects.filter(order_id=old_order_json["id"]).exists()

    assert fresh_photo.is_deleted_from_storage is False
    assert fresh_local.exists()


# ---------------------------------------------------------------------------
# Feature 3: Telegram Notifications & Bot Binding (>= 5 tests)
# ---------------------------------------------------------------------------


def test_t1_f3_telegram_bot_binder_generates_start_link(
    e2e_seed_data: Any,
) -> None:
    """Verify TelegramBotBinder generates a valid https://t.me/...?start=<token> URL."""
    from apps.notifications.services import TelegramBotBinder

    binder = TelegramBotBinder()
    sig = inspect.signature(binder.generate_binding_link)
    first_param = next(iter(sig.parameters))
    arg = e2e_seed_data.client_1.id if first_param == "user_id" else e2e_seed_data.client_1

    link = binder.generate_binding_link(arg)
    assert link.startswith("https://t.me/")
    assert "?start=" in link
    token = link.split("?start=", 1)[1]
    assert len(token) >= 8


def test_t1_f3_telegram_webhook_binds_chat_id_on_valid_token(
    e2e_seed_data: Any,
    api_client_factory: Any,
) -> None:
    """Verify /api/v1/notifications/telegram/webhook/ binds telegram_chat_id on valid token."""
    from apps.notifications.services import TelegramBotBinder

    binder = TelegramBotBinder()
    sig = inspect.signature(binder.generate_binding_link)
    first_param = next(iter(sig.parameters))
    arg = e2e_seed_data.client_1.id if first_param == "user_id" else e2e_seed_data.client_1
    link = binder.generate_binding_link(arg)
    token = link.split("?start=", 1)[1]

    anon_api = api_client_factory()
    chat_id = "555123456"
    resp = anon_api.post(
        "/api/v1/notifications/telegram/webhook/",
        {
            "token": token,
            "chat_id": chat_id,
            "message": {"chat": {"id": int(chat_id)}, "text": f"/start {token}"},
        },
        format="json",
    )
    assert resp.status_code == 200, resp.content

    e2e_seed_data.client_1.refresh_from_db()
    assert str(e2e_seed_data.client_1.telegram_chat_id) == chat_id


def test_t1_f3_apprise_sends_russian_status_changed_notification(
    e2e_seed_data: Any,
    e2e_image_factory: Any,
    api_client_factory: Any,
    upload_photo_via_api: Any,
    mock_apprise_send: Any,
) -> None:
    """Verify status transition triggers Russian Apprise Telegram notification."""
    e2e_seed_data.client_1.telegram_chat_id = "777888999"
    e2e_seed_data.client_1.save(update_fields=["telegram_chat_id"])

    client_api = api_client_factory(
        e2e_seed_data.client_1.email,
        e2e_seed_data.passwords[e2e_seed_data.client_1.email],
    )
    order_json, _ = _create_online_order_via_api(
        client_api,
        upload_photo_via_api,
        e2e_image_factory("JPEG"),
        str(e2e_seed_data.salon_a.id),
        str(e2e_seed_data.service_standard.id),
    )

    mock_apprise_send.reset()
    manager_api = api_client_factory(
        e2e_seed_data.manager_a.email,
        e2e_seed_data.passwords[e2e_seed_data.manager_a.email],
    )
    resp = manager_api.post(
        f"/api/v1/orders/{order_json['id']}/status/",
        {"status": OrderStatus.IN_PROGRESS, "comment": "Проверяем фото"},
        format="json",
    )
    assert resp.status_code == 200, resp.content

    assert len(mock_apprise_send.calls) >= 1
    combined_text = " ".join(
        f"{c['title']} {c['body']}" for c in mock_apprise_send.calls
    )
    assert "Статус вашего заказа №" in combined_text
    assert order_json["order_number"] in combined_text
    assert ORDER_STATUS_LABELS_RU[OrderStatus.IN_PROGRESS] in combined_text


def test_t1_f3_apprise_sends_pickup_reminder_with_pickup_code(
    e2e_seed_data: Any,
    e2e_image_factory: Any,
    api_client_factory: Any,
    upload_photo_via_api: Any,
    mock_apprise_send: Any,
) -> None:
    """Verify Apprise sends Russian pickup reminder containing the order pickup_code."""
    from apps.core.models import Order
    from apps.notifications.services import AppriseNotificationService

    e2e_seed_data.client_1.telegram_chat_id = "112233445"
    e2e_seed_data.client_1.save(update_fields=["telegram_chat_id"])

    client_api = api_client_factory(
        e2e_seed_data.client_1.email,
        e2e_seed_data.passwords[e2e_seed_data.client_1.email],
    )
    order_json, _ = _create_online_order_via_api(
        client_api,
        upload_photo_via_api,
        e2e_image_factory("JPEG"),
        str(e2e_seed_data.salon_a.id),
        str(e2e_seed_data.service_standard.id),
    )
    order = Order.objects.get(id=order_json["id"])

    mock_apprise_send.reset()
    svc = AppriseNotificationService()
    sig = inspect.signature(svc.send_pickup_reminder)
    first_param = next(iter(sig.parameters))
    if first_param == "user":
        sent = svc.send_pickup_reminder(e2e_seed_data.client_1, order)
    else:
        sent = svc.send_pickup_reminder(
            e2e_seed_data.client_1.telegram_chat_id,
            order.order_number,
            order.pickup_code,
            order.pickup_salon.address,
        )

    assert sent is True
    assert len(mock_apprise_send.calls) >= 1
    body = mock_apprise_send.calls[-1]["body"]
    assert order.order_number in body
    assert order.pickup_code in body


def test_t1_f3_in_salon_walkin_order_without_client_succeeds_silently(
    e2e_seed_data: Any,
    e2e_image_factory: Any,
    api_client_factory: Any,
    upload_photo_via_api: Any,
    mock_apprise_send: Any,
) -> None:
    """Verify IN_SALON walk-in order (client=None) transitions without notification errors."""
    from apps.core.enums import DocumentStandard
    from apps.core.models import Order

    manager_api = api_client_factory(
        e2e_seed_data.manager_a.email,
        e2e_seed_data.passwords[e2e_seed_data.manager_a.email],
    )
    upload_resp = upload_photo_via_api(
        manager_api, e2e_image_factory("JPEG", size=(413, 531)), "passport.jpg"
    )
    assert upload_resp.status_code in (200, 201), upload_resp.content
    photo_data = upload_resp.json()

    mock_apprise_send.reset()
    doc_resp = manager_api.post(
        "/api/v1/orders/document-photo/",
        {
            "salon_id": str(e2e_seed_data.salon_a.id),
            "standard": DocumentStandard.RF_PASSPORT_35x45,
            "copies": 4,
            "photo_id": photo_data["id"],
            "file_key": photo_data["file_key"],
        },
        format="json",
    )
    assert doc_resp.status_code == 201, doc_resp.content
    doc_order_json = doc_resp.json()

    order = Order.objects.get(id=doc_order_json["id"])
    assert order.order_type == OrderType.IN_SALON
    assert order.client is None
    assert order.status == OrderStatus.PRINTING

    ready_resp = manager_api.post(
        f"/api/v1/orders/{order.id}/status/",
        {"status": OrderStatus.READY_FOR_PICKUP},
        format="json",
    )
    assert ready_resp.status_code == 200, ready_resp.content
    assert len(mock_apprise_send.calls) == 0


# ---------------------------------------------------------------------------
# Feature 4: Pricing, Auto-Routing & Order FSM (>= 6 tests)
# ---------------------------------------------------------------------------


def test_t1_f4_calculate_price_returns_itemized_and_total(
    e2e_seed_data: Any,
    api_client_factory: Any,
) -> None:
    """Verify /api/v1/orders/calculate-price/ calculates exact itemized & total RUB amounts."""
    client_api = api_client_factory(
        e2e_seed_data.client_1.email,
        e2e_seed_data.passwords[e2e_seed_data.client_1.email],
    )
    payload = {
        "items": [
            {
                "service_id": str(e2e_seed_data.service_standard.id),
                "width_cm": 10.0,
                "height_cm": 15.0,
                "quantity": 2,
                "material": "Глянцевая",
                "options": ["Ламинация"],
            },
            {
                "service_id": str(e2e_seed_data.service_standard.id),
                "width_cm": 15.0,
                "height_cm": 20.0,
                "quantity": 1,
                "material": "Матовая",
                "options": ["Без рамки"],
            },
        ]
    }
    resp = client_api.post("/api/v1/orders/calculate-price/", payload, format="json")
    assert resp.status_code == 200, resp.content
    data = resp.json()

    assert len(data["items"]) == 2
    assert Decimal(str(data["total_price"])) == Decimal("470.00")


def test_t1_f4_auto_routing_optimizes_load_plus_delivery_cost(
    e2e_seed_data: Any,
    e2e_image_factory: Any,
    api_client_factory: Any,
    upload_photo_via_api: Any,
) -> None:
    """Verify auto-routing selects minimum (load * 100) + delivery_cost salon."""
    from apps.core.models import Order

    client_api = api_client_factory(
        e2e_seed_data.client_1.email,
        e2e_seed_data.passwords[e2e_seed_data.client_1.email],
    )
    img = e2e_image_factory("JPEG")

    # With equal 0 load, pickup salon_a has delivery_cost=0 vs salon_b delivery_cost=200.
    first_order, _ = _create_online_order_via_api(
        client_api,
        upload_photo_via_api,
        img,
        str(e2e_seed_data.salon_a.id),
        str(e2e_seed_data.service_standard.id),
    )
    assert _extract_id(first_order["assigned_salon"]) == str(e2e_seed_data.salon_a.id)

    # Seed 2 more active orders on salon_a (total 3 prior orders -> 300 > salon_b 200, no tie).
    for idx in range(2):
        Order.objects.create(
            order_number=f"YP-T1-LOAD-{idx:02d}",
            pickup_code=f"22000{idx}",
            order_type=OrderType.ONLINE,
            status=OrderStatus.ASSIGNED_TO_SALON,
            client=e2e_seed_data.client_1,
            pickup_salon=e2e_seed_data.salon_a,
            assigned_salon=e2e_seed_data.salon_a,
        )

    routed_order, _ = _create_online_order_via_api(
        client_api,
        upload_photo_via_api,
        img,
        str(e2e_seed_data.salon_a.id),
        str(e2e_seed_data.service_standard.id),
    )
    assert routed_order["status"] == OrderStatus.ASSIGNED_TO_SALON
    assert _extract_id(routed_order["assigned_salon"]) == str(e2e_seed_data.salon_b.id)


def test_t1_f4_equipment_90_degree_rotation_fulfills_45x30_on_30x45(
    e2e_seed_data: Any,
    e2e_image_factory: Any,
    api_client_factory: Any,
    upload_photo_via_api: Any,
) -> None:
    """Verify 30x45 equipment fulfills 45x30 print via 90-degree rotation."""
    client_api = api_client_factory(
        e2e_seed_data.client_1.email,
        e2e_seed_data.passwords[e2e_seed_data.client_1.email],
    )
    order_json, _ = _create_online_order_via_api(
        client_api,
        upload_photo_via_api,
        e2e_image_factory("JPEG"),
        str(e2e_seed_data.salon_a.id),
        str(e2e_seed_data.service_standard.id),
        width_cm=45.0,
        height_cm=30.0,
    )
    assert order_json["status"] == OrderStatus.ASSIGNED_TO_SALON
    assert _extract_id(order_json["assigned_salon"]) == str(e2e_seed_data.salon_a.id)


def test_t1_f4_local_fulfillment_transitions_to_ready_for_pickup(
    e2e_seed_data: Any,
    e2e_image_factory: Any,
    api_client_factory: Any,
    upload_photo_via_api: Any,
) -> None:
    """Verify local order transitions ASSIGNED -> IN_PROGRESS -> PRINTING -> FINISHING -> READY."""
    client_api = api_client_factory(
        e2e_seed_data.client_1.email,
        e2e_seed_data.passwords[e2e_seed_data.client_1.email],
    )
    order_json, _ = _create_online_order_via_api(
        client_api,
        upload_photo_via_api,
        e2e_image_factory("JPEG"),
        str(e2e_seed_data.salon_a.id),
        str(e2e_seed_data.service_standard.id),
    )
    assert order_json["status"] == OrderStatus.ASSIGNED_TO_SALON

    manager_a_api = api_client_factory(
        e2e_seed_data.manager_a.email,
        e2e_seed_data.passwords[e2e_seed_data.manager_a.email],
    )
    for target in (
        OrderStatus.IN_PROGRESS,
        OrderStatus.PRINTING,
        OrderStatus.FINISHING,
        OrderStatus.READY_FOR_PICKUP,
    ):
        resp = manager_a_api.post(
            f"/api/v1/orders/{order_json['id']}/status/",
            {"status": target},
            format="json",
        )
        assert resp.status_code == 200, resp.content
        body = resp.json()
        assert body["status"] == target
        assert body["status_label"] == ORDER_STATUS_LABELS_RU[target]


def test_t1_f4_cross_salon_fulfillment_transitions_through_in_transit(
    e2e_seed_data: Any,
    e2e_image_factory: Any,
    api_client_factory: Any,
    upload_photo_via_api: Any,
) -> None:
    """Verify cross-salon order transitions PRINTING -> IN_TRANSIT -> READY_FOR_PICKUP."""
    client_api = api_client_factory(
        e2e_seed_data.client_1.email,
        e2e_seed_data.passwords[e2e_seed_data.client_1.email],
    )
    # service_wide is only at salon_b, pickup is salon_a.
    order_json, _ = _create_online_order_via_api(
        client_api,
        upload_photo_via_api,
        e2e_image_factory("JPEG"),
        str(e2e_seed_data.salon_a.id),
        str(e2e_seed_data.service_wide.id),
        width_cm=60.0,
        height_cm=90.0,
        material="Холст",
        options=["Установка люверсов"],
    )
    assert order_json["status"] == OrderStatus.ASSIGNED_TO_SALON
    assert _extract_id(order_json["assigned_salon"]) == str(e2e_seed_data.salon_b.id)
    assert _extract_id(order_json["pickup_salon"]) == str(e2e_seed_data.salon_a.id)

    manager_b_api = api_client_factory(
        e2e_seed_data.manager_b.email,
        e2e_seed_data.passwords[e2e_seed_data.manager_b.email],
    )
    for target in (OrderStatus.IN_PROGRESS, OrderStatus.PRINTING, OrderStatus.IN_TRANSIT):
        r = manager_b_api.post(
            f"/api/v1/orders/{order_json['id']}/status/",
            {"status": target},
            format="json",
        )
        assert r.status_code == 200, r.content
        assert r.json()["status"] == target

    manager_a_api = api_client_factory(
        e2e_seed_data.manager_a.email,
        e2e_seed_data.passwords[e2e_seed_data.manager_a.email],
    )
    arrived = manager_a_api.post(
        f"/api/v1/orders/{order_json['id']}/status/",
        {"status": OrderStatus.READY_FOR_PICKUP},
        format="json",
    )
    assert arrived.status_code == 200, arrived.content
    assert arrived.json()["status"] == OrderStatus.READY_FOR_PICKUP


def test_t1_f4_complete_pickup_with_valid_code_sets_completed_and_30d_retention(
    e2e_seed_data: Any,
    e2e_image_factory: Any,
    api_client_factory: Any,
    upload_photo_via_api: Any,
) -> None:
    """Verify complete-pickup with valid pickup_code sets COMPLETED and 30d delete_after."""
    from apps.core.models import Order, PhotoFile

    client_api = api_client_factory(
        e2e_seed_data.client_1.email,
        e2e_seed_data.passwords[e2e_seed_data.client_1.email],
    )
    order_json, photo_data = _create_online_order_via_api(
        client_api,
        upload_photo_via_api,
        e2e_image_factory("JPEG"),
        str(e2e_seed_data.salon_a.id),
        str(e2e_seed_data.service_standard.id),
    )

    manager_a_api = api_client_factory(
        e2e_seed_data.manager_a.email,
        e2e_seed_data.passwords[e2e_seed_data.manager_a.email],
    )
    for target in (OrderStatus.IN_PROGRESS, OrderStatus.PRINTING, OrderStatus.READY_FOR_PICKUP):
        r = manager_a_api.post(
            f"/api/v1/orders/{order_json['id']}/status/",
            {"status": target},
            format="json",
        )
        assert r.status_code == 200, r.content

    complete_resp = manager_a_api.post(
        f"/api/v1/orders/{order_json['id']}/complete-pickup/",
        {"pickup_code": order_json["pickup_code"]},
        format="json",
    )
    assert complete_resp.status_code == 200, complete_resp.content
    assert complete_resp.json()["status"] == OrderStatus.COMPLETED

    order = Order.objects.get(id=order_json["id"])
    photo = PhotoFile.objects.get(id=photo_data["id"])
    assert order.completed_at is not None
    assert photo.delete_after is not None
    delta = photo.delete_after - order.completed_at
    assert timedelta(days=29, hours=23) <= delta <= timedelta(days=30, hours=1)


# ---------------------------------------------------------------------------
# Feature 5: Backend REST API & RBAC (>= 6 tests)
# ---------------------------------------------------------------------------


def test_t1_f5_login_via_email_and_via_phone(
    e2e_seed_data: Any,
    api_client_factory: Any,
) -> None:
    """Verify /api/v1/auth/login/ authenticates by both email and phone number."""
    anon = api_client_factory()
    raw_pw = e2e_seed_data.passwords[e2e_seed_data.client_1.email]

    for login_str in (e2e_seed_data.client_1.email, e2e_seed_data.client_1.phone):
        resp = anon.post(
            "/api/v1/auth/login/",
            {"login": login_str, "password": raw_pw},
            format="json",
        )
        assert resp.status_code == 200, resp.content
        body = resp.json()
        assert body.get("token")
        assert body["user"]["id"] == str(e2e_seed_data.client_1.id)
        assert body["user"]["role"] == UserRole.CLIENT


def test_t1_f5_orders_list_role_scoping_and_admin_filters(
    e2e_seed_data: Any,
    e2e_image_factory: Any,
    api_client_factory: Any,
    upload_photo_via_api: Any,
) -> None:
    """Verify GET /api/v1/orders/ scopes visibility by CLIENT, MANAGER, and ADMIN roles."""
    c1_api = api_client_factory(
        e2e_seed_data.client_1.email,
        e2e_seed_data.passwords[e2e_seed_data.client_1.email],
    )
    c2_api = api_client_factory(
        e2e_seed_data.client_2.email,
        e2e_seed_data.passwords[e2e_seed_data.client_2.email],
    )
    img = e2e_image_factory("JPEG")

    # Order 1: client_1, salon_a local
    o1, _ = _create_online_order_via_api(
        c1_api,
        upload_photo_via_api,
        img,
        str(e2e_seed_data.salon_a.id),
        str(e2e_seed_data.service_standard.id),
    )
    # Order 2: client_2, assigned salon_b, pickup salon_a (visible to manager_a & manager_b)
    o2, _ = _create_online_order_via_api(
        c2_api,
        upload_photo_via_api,
        img,
        str(e2e_seed_data.salon_a.id),
        str(e2e_seed_data.service_wide.id),
        width_cm=60.0,
        height_cm=80.0,
        material="Холст",
        options=[],
    )
    # Order 3: client_2, salon_b local (NOT visible to manager_a)
    o3, _ = _create_online_order_via_api(
        c2_api,
        upload_photo_via_api,
        img,
        str(e2e_seed_data.salon_b.id),
        str(e2e_seed_data.service_standard.id),
    )

    c1_ids = {item["id"] for item in c1_api.get("/api/v1/orders/").json()}
    assert c1_ids == {o1["id"]}

    mgr_a_api = api_client_factory(
        e2e_seed_data.manager_a.email,
        e2e_seed_data.passwords[e2e_seed_data.manager_a.email],
    )
    mgr_a_ids = {item["id"] for item in mgr_a_api.get("/api/v1/orders/").json()}
    assert {o1["id"], o2["id"]}.issubset(mgr_a_ids)
    assert o3["id"] not in mgr_a_ids

    admin_api = api_client_factory(
        e2e_seed_data.admin.email,
        e2e_seed_data.passwords[e2e_seed_data.admin.email],
    )
    admin_ids = {item["id"] for item in admin_api.get("/api/v1/orders/").json()}
    assert {o1["id"], o2["id"], o3["id"]}.issubset(admin_ids)

    filtered_resp = admin_api.get(
        f"/api/v1/orders/?status={OrderStatus.ASSIGNED_TO_SALON}&salon={e2e_seed_data.salon_b.id}"
    )
    assert filtered_resp.status_code == 200, filtered_resp.content
    assert isinstance(filtered_resp.json(), list)


def test_t1_f5_admin_manual_reassign_to_capable_salon(
    e2e_seed_data: Any,
    e2e_image_factory: Any,
    api_client_factory: Any,
    upload_photo_via_api: Any,
) -> None:
    """Verify ADMIN can manually reassign an order to another capable active salon."""
    from apps.core.models import OrderStatusHistory

    c1_api = api_client_factory(
        e2e_seed_data.client_1.email,
        e2e_seed_data.passwords[e2e_seed_data.client_1.email],
    )
    order_json, _ = _create_online_order_via_api(
        c1_api,
        upload_photo_via_api,
        e2e_image_factory("JPEG"),
        str(e2e_seed_data.salon_a.id),
        str(e2e_seed_data.service_standard.id),
    )
    assert _extract_id(order_json["assigned_salon"]) == str(e2e_seed_data.salon_a.id)

    admin_api = api_client_factory(
        e2e_seed_data.admin.email,
        e2e_seed_data.passwords[e2e_seed_data.admin.email],
    )
    resp = admin_api.post(
        f"/api/v1/orders/{order_json['id']}/reassign/",
        {
            "salon_id": str(e2e_seed_data.salon_b.id),
            "comment": "Балансировка нагрузки",
        },
        format="json",
    )
    assert resp.status_code == 200, resp.content
    assert _extract_id(resp.json()["assigned_salon"]) == str(e2e_seed_data.salon_b.id)

    last_hist = (
        OrderStatusHistory.objects.filter(order_id=order_json["id"])
        .order_by("changed_at")
        .last()
    )
    assert last_hist is not None
    assert last_hist.actor_id == e2e_seed_data.admin.id


def test_t1_f5_clarification_response_with_replacement_photo_and_agree_as_is(
    e2e_seed_data: Any,
    e2e_image_factory: Any,
    api_client_factory: Any,
    upload_photo_via_api: Any,
) -> None:
    """Verify clarification-response handles both replacement photo and agree_as_is."""
    c1_api = api_client_factory(
        e2e_seed_data.client_1.email,
        e2e_seed_data.passwords[e2e_seed_data.client_1.email],
    )
    mgr_a_api = api_client_factory(
        e2e_seed_data.manager_a.email,
        e2e_seed_data.passwords[e2e_seed_data.manager_a.email],
    )
    img = e2e_image_factory("JPEG")

    order_1, _ = _create_online_order_via_api(
        c1_api,
        upload_photo_via_api,
        img,
        str(e2e_seed_data.salon_a.id),
        str(e2e_seed_data.service_standard.id),
    )
    order_2, _ = _create_online_order_via_api(
        c1_api,
        upload_photo_via_api,
        img,
        str(e2e_seed_data.salon_a.id),
        str(e2e_seed_data.service_standard.id),
    )

    for oid in (order_1["id"], order_2["id"]):
        r1 = mgr_a_api.post(
            f"/api/v1/orders/{oid}/status/",
            {"status": OrderStatus.IN_PROGRESS},
            format="json",
        )
        assert r1.status_code == 200, r1.content
        r2 = mgr_a_api.post(
            f"/api/v1/orders/{oid}/status/",
            {
                "status": OrderStatus.NEEDS_CLARIFICATION,
                "comment": "Низкое разрешение",
            },
            format="json",
        )
        assert r2.status_code == 200, r2.content

    new_photo = upload_photo_via_api(
        c1_api, e2e_image_factory("PNG", size=(1200, 900)), "hi_res.png", "image/png"
    )
    assert new_photo.status_code in (200, 201), new_photo.content
    resp_1 = c1_api.post(
        f"/api/v1/orders/{order_1['id']}/clarification-response/",
        {
            "photo_id": new_photo.json()["id"],
            "comment": "Загрузил качественное фото",
        },
        format="json",
    )
    assert resp_1.status_code == 200, resp_1.content
    assert resp_1.json()["status"] == OrderStatus.IN_PROGRESS

    resp_2 = c1_api.post(
        f"/api/v1/orders/{order_2['id']}/clarification-response/",
        {"agree_as_is": True, "comment": "Согласен печатать как есть"},
        format="json",
    )
    assert resp_2.status_code == 200, resp_2.content
    assert resp_2.json()["status"] == OrderStatus.IN_PROGRESS


def test_t1_f5_services_and_active_salons_endpoints(
    e2e_seed_data: Any,
    api_client_factory: Any,
) -> None:
    """Verify /api/v1/services/ and /api/v1/salons/ return active catalog items."""
    client_api = api_client_factory(
        e2e_seed_data.client_1.email,
        e2e_seed_data.passwords[e2e_seed_data.client_1.email],
    )

    services_resp = client_api.get("/api/v1/services/")
    assert services_resp.status_code == 200, services_resp.content
    service_ids = {s["id"] for s in services_resp.json()}
    assert {
        str(e2e_seed_data.service_standard.id),
        str(e2e_seed_data.service_wide.id),
    }.issubset(service_ids)

    salons_resp = client_api.get("/api/v1/salons/")
    assert salons_resp.status_code == 200, salons_resp.content
    salon_ids = {s["id"] for s in salons_resp.json()}
    assert str(e2e_seed_data.salon_a.id) in salon_ids
    assert str(e2e_seed_data.salon_b.id) in salon_ids
    assert str(e2e_seed_data.salon_hidden.id) not in salon_ids


def test_t1_f5_admin_creates_salon_manager_via_users_api(
    e2e_seed_data: Any,
    api_client_factory: Any,
) -> None:
    """Verify ADMIN can create a salon-bound MANAGER via /api/v1/users/ who can log in."""
    admin_api = api_client_factory(
        e2e_seed_data.admin.email,
        e2e_seed_data.passwords[e2e_seed_data.admin.email],
    )
    create_resp = admin_api.post(
        "/api/v1/users/",
        {
            "name": "Новый Менеджер А",
            "email": "new.manager@youphoto.ru",
            "phone": "+79009998877",
            "password": "NewManager123!",
            "role": UserRole.MANAGER,
            "salon": str(e2e_seed_data.salon_a.id),
            "salon_id": str(e2e_seed_data.salon_a.id),
        },
        format="json",
    )
    assert create_resp.status_code in (200, 201), create_resp.content

    anon_api = api_client_factory()
    login_resp = anon_api.post(
        "/api/v1/auth/login/",
        {"login": "new.manager@youphoto.ru", "password": "NewManager123!"},
        format="json",
    )
    assert login_resp.status_code == 200, login_resp.content
    user_payload = login_resp.json()["user"]
    assert user_payload["role"] == UserRole.MANAGER
    assert str(user_payload["salon_id"]) == str(e2e_seed_data.salon_a.id)
