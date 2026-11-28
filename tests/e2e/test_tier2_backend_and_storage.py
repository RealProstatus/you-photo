"""Tier 2 opaque-box E2E boundary & error-path tests for Features 1-5."""

from __future__ import annotations

import inspect
import struct
import zlib
from datetime import timedelta
from typing import Any

import pytest
from apps.core.enums import ALLOWED_STATUS_TRANSITIONS, OrderStatus, UserRole

pytestmark = [pytest.mark.django_db(transaction=True), pytest.mark.integration]


def _extract_id(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, dict):
        raw = value.get("id")
        return str(raw) if raw is not None else None
    return str(value)


def _extract_error_text(response: Any) -> str:
    try:
        data = response.json()
    except ValueError:
        return response.content.decode("utf-8", errors="ignore")
    if isinstance(data, dict):
        parts: list[str] = []
        for val in data.values():
            if isinstance(val, list):
                parts.extend(str(item) for item in val)
            else:
                parts.append(str(val))
        return " ".join(parts)
    if isinstance(data, list):
        return " ".join(str(item) for item in data)
    return str(data)


def _build_sized_png(base_png_bytes: bytes, target_size: int) -> bytes:
    """Pad a valid PNG with a valid tEXt chunk before IEND to reach exact byte size."""
    iend_len = 12
    overhead = 16
    pad_len = target_size - len(base_png_bytes) - overhead
    if pad_len < 0:
        raise ValueError("target_size is smaller than base PNG plus chunk overhead")

    prefix = base_png_bytes[:-iend_len]
    iend = base_png_bytes[-iend_len:]
    chunk_type = b"tEXt"
    chunk_data = b"Pad\x00" + (b"A" * pad_len)
    crc = zlib.crc32(chunk_type + chunk_data) & 0xFFFFFFFF
    chunk = struct.pack(">I", len(chunk_data)) + chunk_type + chunk_data + struct.pack(">I", crc)
    result = prefix + chunk + iend
    assert len(result) == target_size
    return result


def _create_online_order_via_api(
    client: Any,
    upload_fn: Any,
    image_bytes: bytes,
    pickup_salon_id: str,
    service_id: str,
    width_cm: float = 10.0,
    height_cm: float = 15.0,
    material: str = "Глянцевая",
    options: list[str] | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    upload_resp = upload_fn(client, image_bytes, filename="tier2_photo.jpg")
    assert upload_resp.status_code in (200, 201), upload_resp.content
    photo_data = upload_resp.json()

    order_resp = client.post(
        "/api/v1/orders/",
        {
            "pickup_salon_id": pickup_salon_id,
            "items": [
                {
                    "photo_id": photo_data["id"],
                    "service_id": service_id,
                    "width_cm": width_cm,
                    "height_cm": height_cm,
                    "quantity": 1,
                    "material": material,
                    "options": options if options is not None else [],
                }
            ],
        },
        format="json",
    )
    assert order_resp.status_code == 201, order_resp.content
    return order_resp.json(), photo_data


def _generate_binding_token(user: Any) -> str:
    from apps.notifications.services import TelegramBotBinder

    binder = TelegramBotBinder()
    sig = inspect.signature(binder.generate_binding_link)
    first_param = next(iter(sig.parameters))
    arg = user.id if first_param == "user_id" else user
    link = binder.generate_binding_link(arg)
    return link.split("?start=", 1)[1]


# ---------------------------------------------------------------------------
# Feature 1 Boundaries & Corner Cases (>= 5 tests)
# ---------------------------------------------------------------------------


def test_t2_f1_duplicate_email_rejected(e2e_seed_data: Any) -> None:
    """Verify creating a second User with an existing email is rejected."""
    from django.contrib.auth import get_user_model
    from django.core.exceptions import ValidationError
    from django.db import IntegrityError

    User = get_user_model()
    with pytest.raises((IntegrityError, ValidationError)):
        User.objects.create_user(
            email=e2e_seed_data.client_1.email,
            phone="+79009990001",
            name="Дубль Почты",
            password="SomePassword123!",
            role=UserRole.CLIENT,
        )


def test_t2_f1_duplicate_phone_rejected(e2e_seed_data: Any) -> None:
    """Verify creating a second User with an existing phone is rejected."""
    from django.contrib.auth import get_user_model
    from django.core.exceptions import ValidationError
    from django.db import IntegrityError

    User = get_user_model()
    with pytest.raises((IntegrityError, ValidationError)):
        User.objects.create_user(
            email="unique.phone.check@youphoto.ru",
            phone=e2e_seed_data.client_1.phone,
            name="Дубль Телефона",
            password="SomePassword123!",
            role=UserRole.CLIENT,
        )


def test_t2_f1_client_user_salon_optional_and_nullable(e2e_seed_data: Any) -> None:
    """Verify User.salon is optional for CLIENT and sets NULL cleanly if salon deleted."""
    from apps.core.models import Salon
    from django.contrib.auth import get_user_model

    User = get_user_model()
    assert e2e_seed_data.client_1.salon is None

    temp_salon = Salon.objects.create(
        name="Временный Салон",
        address="ул. Тестовая, 1",
        is_active=True,
    )
    temp_mgr = User.objects.create_user(
        email="temp.mgr@youphoto.ru",
        phone="+79009990002",
        name="Временный Менеджер",
        password="TempPassword123!",
        role=UserRole.MANAGER,
        salon=temp_salon,
    )
    temp_salon.delete()
    temp_mgr.refresh_from_db()
    assert temp_mgr.salon is None


def test_t2_f1_order_pickup_code_format_and_order_number_uniqueness(
    e2e_seed_data: Any,
    e2e_image_factory: Any,
    api_client_factory: Any,
    upload_photo_via_api: Any,
) -> None:
    """Verify orders have 6-digit numeric pickup_code and unique order_number."""
    from apps.core.models import Order
    from django.db import IntegrityError

    c1_api = api_client_factory(
        e2e_seed_data.client_1.email,
        e2e_seed_data.passwords[e2e_seed_data.client_1.email],
    )
    img = e2e_image_factory("JPEG")

    orders = []
    for _ in range(3):
        order_json, _ = _create_online_order_via_api(
            c1_api,
            upload_photo_via_api,
            img,
            str(e2e_seed_data.salon_a.id),
            str(e2e_seed_data.service_standard.id),
        )
        orders.append(order_json)

    order_numbers = {o["order_number"] for o in orders}
    assert len(order_numbers) == 3
    for o in orders:
        code = str(o["pickup_code"])
        assert len(code) == 6 and code.isdigit(), f"Expected 6-digit pickup_code, got {code!r}"

    with pytest.raises(IntegrityError):
        Order.objects.create(
            order_number=orders[0]["order_number"],
            pickup_code="123456",
            pickup_salon=e2e_seed_data.salon_a,
        )


def test_t2_f1_completed_status_is_terminal_zero_outgoing_transitions(
    e2e_seed_data: Any,
) -> None:
    """Verify ALLOWED_STATUS_TRANSITIONS[COMPLETED] has zero outgoing transitions."""
    from apps.core.models import Order

    assert ALLOWED_STATUS_TRANSITIONS[OrderStatus.COMPLETED] == set()
    completed_order = Order.objects.create(
        order_number="YP-TERM-0001",
        pickup_code="654321",
        status=OrderStatus.COMPLETED,
        pickup_salon=e2e_seed_data.salon_a,
        assigned_salon=e2e_seed_data.salon_a,
    )
    for target in OrderStatus:
        assert target not in ALLOWED_STATUS_TRANSITIONS[OrderStatus.COMPLETED]
        if hasattr(completed_order, "can_transition_to"):
            sig = inspect.signature(completed_order.can_transition_to)
            if len(sig.parameters) >= 2:
                assert not completed_order.can_transition_to(target, e2e_seed_data.admin)
            else:
                assert not completed_order.can_transition_to(target)


# ---------------------------------------------------------------------------
# Feature 2 Boundaries & Corner Cases (>= 5 tests)
# ---------------------------------------------------------------------------


def test_t2_f2_empty_zero_byte_file_rejected(
    e2e_seed_data: Any,
    api_client_factory: Any,
    upload_photo_via_api: Any,
) -> None:
    """Verify uploading a 0-byte file is rejected with 400 and Russian error."""
    c1_api = api_client_factory(
        e2e_seed_data.client_1.email,
        e2e_seed_data.passwords[e2e_seed_data.client_1.email],
    )
    resp = upload_photo_via_api(c1_api, b"", "empty.jpg", "image/jpeg")
    assert resp.status_code == 400, resp.content
    err_text = _extract_error_text(resp)
    assert any(ord(ch) >= 0x0400 for ch in err_text), f"Expected Russian error, got {err_text!r}"


def test_t2_f2_exact_50mb_boundary_accepted(
    e2e_seed_data: Any,
    e2e_image_factory: Any,
    api_client_factory: Any,
    upload_photo_via_api: Any,
) -> None:
    """Verify uploading an exact 50 MB (50 * 1024 * 1024 bytes) valid PNG succeeds."""
    c1_api = api_client_factory(
        e2e_seed_data.client_1.email,
        e2e_seed_data.passwords[e2e_seed_data.client_1.email],
    )
    base_png = e2e_image_factory("PNG", size=(100, 100))
    exact_50mb = _build_sized_png(base_png, 50 * 1024 * 1024)

    resp = upload_photo_via_api(c1_api, exact_50mb, "exact_50mb.png", "image/png")
    assert resp.status_code in (200, 201), resp.content
    assert resp.json()["size_bytes"] == 50 * 1024 * 1024


def test_t2_f2_over_50mb_rejected_with_russian_50mb_message(
    e2e_seed_data: Any,
    e2e_image_factory: Any,
    api_client_factory: Any,
    upload_photo_via_api: Any,
) -> None:
    """Verify 50 MB + 1 byte and 51 MB uploads are rejected with Russian 50 МБ message."""
    c1_api = api_client_factory(
        e2e_seed_data.client_1.email,
        e2e_seed_data.passwords[e2e_seed_data.client_1.email],
    )
    base_png = e2e_image_factory("PNG", size=(100, 100))

    for oversize in (50 * 1024 * 1024 + 1, 51 * 1024 * 1024):
        oversize_png = _build_sized_png(base_png, oversize)
        resp = upload_photo_via_api(c1_api, oversize_png, "too_big.png", "image/png")
        assert resp.status_code == 400, f"Expected 400 for size={oversize}, got {resp.status_code}"
        err_text = _extract_error_text(resp)
        assert "50 МБ" in err_text, f"Expected '50 МБ' in Russian error, got {err_text!r}"


def test_t2_f2_disallowed_formats_and_fake_jpg_rejected(
    e2e_seed_data: Any,
    e2e_image_factory: Any,
    api_client_factory: Any,
    upload_photo_via_api: Any,
) -> None:
    """Verify BMP, GIF, WEBP, PDF, and spoofed .jpg non-image bytes are rejected with 400."""
    c1_api = api_client_factory(
        e2e_seed_data.client_1.email,
        e2e_seed_data.passwords[e2e_seed_data.client_1.email],
    )
    invalid_samples = [
        ("sample.bmp", "image/bmp", e2e_image_factory("BMP")),
        ("sample.gif", "image/gif", e2e_image_factory("GIF")),
        ("sample.webp", "image/webp", e2e_image_factory("WEBP")),
        ("doc.pdf", "application/pdf", b"%PDF-1.4\n1 0 obj\n<<>>\nendobj\n%%EOF"),
        ("fake.jpg", "image/jpeg", b"MZ\x90\x00not-a-valid-jpeg-payload-at-all"),
    ]
    for filename, content_type, payload in invalid_samples:
        resp = upload_photo_via_api(c1_api, payload, filename, content_type)
        assert resp.status_code == 400, f"{filename} unexpectedly accepted: {resp.status_code}"
        err_text = _extract_error_text(resp)
        assert any(ord(ch) >= 0x0400 for ch in err_text)


@pytest.mark.security
def test_t2_f2_unauthorized_photo_download_returns_403(
    e2e_seed_data: Any,
    e2e_image_factory: Any,
    api_client_factory: Any,
    upload_photo_via_api: Any,
) -> None:
    """Verify other CLIENT and unrelated salon MANAGER receive 403 on photo download."""
    c1_api = api_client_factory(
        e2e_seed_data.client_1.email,
        e2e_seed_data.passwords[e2e_seed_data.client_1.email],
    )
    _, photo_data = _create_online_order_via_api(
        c1_api,
        upload_photo_via_api,
        e2e_image_factory("JPEG"),
        str(e2e_seed_data.salon_a.id),
        str(e2e_seed_data.service_standard.id),
    )

    c2_api = api_client_factory(
        e2e_seed_data.client_2.email,
        e2e_seed_data.passwords[e2e_seed_data.client_2.email],
    )
    mgr_b_api = api_client_factory(
        e2e_seed_data.manager_b.email,
        e2e_seed_data.passwords[e2e_seed_data.manager_b.email],
    )

    download_url = f"/api/v1/photos/{photo_data['id']}/download/"
    assert c2_api.get(download_url).status_code == 403
    assert mgr_b_api.get(download_url).status_code == 403


# ---------------------------------------------------------------------------
# Feature 3 Boundaries & Corner Cases (>= 5 tests)
# ---------------------------------------------------------------------------


def test_t2_f3_expired_binding_token_rejected(
    e2e_seed_data: Any,
    api_client_factory: Any,
) -> None:
    """Verify expired (>30 min) Telegram binding token is rejected and does not bind."""
    from apps.notifications.models import TelegramBindingToken
    from django.utils import timezone

    token = _generate_binding_token(e2e_seed_data.client_1)
    field_names = {f.name for f in TelegramBindingToken._meta.fields}
    updates: dict[str, Any] = {}
    if "expires_at" in field_names:
        updates["expires_at"] = timezone.now() - timedelta(minutes=1)
    if "created_at" in field_names:
        updates["created_at"] = timezone.now() - timedelta(minutes=35)
    assert updates, "TelegramBindingToken must track expiration timestamp"
    TelegramBindingToken.objects.filter(user=e2e_seed_data.client_1).update(**updates)

    anon_api = api_client_factory()
    resp = anon_api.post(
        "/api/v1/notifications/telegram/webhook/",
        {
            "token": token,
            "chat_id": "333444555",
            "message": {"chat": {"id": 333444555}, "text": f"/start {token}"},
        },
        format="json",
    )
    if resp.status_code == 200:
        body = resp.json()
        assert body.get("bound") is False or body.get("ok") is False or body.get("success") is False
    else:
        assert resp.status_code in (400, 404, 410)

    e2e_seed_data.client_1.refresh_from_db()
    assert not e2e_seed_data.client_1.telegram_chat_id


def test_t2_f3_reused_one_time_binding_token_rejected(
    e2e_seed_data: Any,
    api_client_factory: Any,
) -> None:
    """Verify Telegram binding token is strictly one-time and second reuse fails."""
    token = _generate_binding_token(e2e_seed_data.client_1)
    anon_api = api_client_factory()

    first = anon_api.post(
        "/api/v1/notifications/telegram/webhook/",
        {
            "token": token,
            "chat_id": "111000111",
            "message": {"chat": {"id": 111000111}, "text": f"/start {token}"},
        },
        format="json",
    )
    assert first.status_code == 200, first.content
    e2e_seed_data.client_1.refresh_from_db()
    assert str(e2e_seed_data.client_1.telegram_chat_id) == "111000111"

    second = anon_api.post(
        "/api/v1/notifications/telegram/webhook/",
        {
            "token": token,
            "chat_id": "222000222",
            "message": {"chat": {"id": 222000222}, "text": f"/start {token}"},
        },
        format="json",
    )
    if second.status_code == 200:
        body = second.json()
        assert body.get("bound") is False or body.get("ok") is False or body.get("success") is False
    else:
        assert second.status_code in (400, 404, 410)

    e2e_seed_data.client_1.refresh_from_db()
    assert str(e2e_seed_data.client_1.telegram_chat_id) == "111000111"


def test_t2_f3_unknown_invalid_binding_token_rejected(
    e2e_seed_data: Any,
    api_client_factory: Any,
) -> None:
    """Verify unknown/invalid Telegram binding token is rejected cleanly."""
    anon_api = api_client_factory()
    bogus = "nonexistent-invalid-token-999999"
    resp = anon_api.post(
        "/api/v1/notifications/telegram/webhook/",
        {
            "token": bogus,
            "chat_id": "999111222",
            "message": {"chat": {"id": 999111222}, "text": f"/start {bogus}"},
        },
        format="json",
    )
    if resp.status_code == 200:
        body = resp.json()
        assert body.get("bound") is False or body.get("ok") is False or body.get("success") is False
    else:
        assert resp.status_code in (400, 404)

    e2e_seed_data.client_1.refresh_from_db()
    assert not e2e_seed_data.client_1.telegram_chat_id


def test_t2_f3_empty_telegram_chat_id_does_not_error(
    e2e_seed_data: Any,
    e2e_image_factory: Any,
    api_client_factory: Any,
    upload_photo_via_api: Any,
    mock_apprise_send: Any,
) -> None:
    """Verify status transition succeeds without error when client has empty telegram_chat_id."""
    e2e_seed_data.client_1.telegram_chat_id = ""
    e2e_seed_data.client_1.save(update_fields=["telegram_chat_id"])

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

    mock_apprise_send.reset()
    mgr_a_api = api_client_factory(
        e2e_seed_data.manager_a.email,
        e2e_seed_data.passwords[e2e_seed_data.manager_a.email],
    )
    resp = mgr_a_api.post(
        f"/api/v1/orders/{order_json['id']}/status/",
        {"status": OrderStatus.IN_PROGRESS},
        format="json",
    )
    assert resp.status_code == 200, resp.content
    assert len(mock_apprise_send.calls) == 0


def test_t2_f3_apprise_failure_does_not_rollback_order_status_tx(
    e2e_seed_data: Any,
    e2e_image_factory: Any,
    api_client_factory: Any,
    upload_photo_via_api: Any,
    mock_apprise_send: Any,
) -> None:
    """Verify Apprise failure (False or exception) never rolls back DB order status transition."""
    from apps.core.models import Order, OrderStatusHistory

    e2e_seed_data.client_1.telegram_chat_id = "888777666"
    e2e_seed_data.client_1.save(update_fields=["telegram_chat_id"])

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

    mgr_a_api = api_client_factory(
        e2e_seed_data.manager_a.email,
        e2e_seed_data.passwords[e2e_seed_data.manager_a.email],
    )

    mock_apprise_send.set_fail(True)
    r1 = mgr_a_api.post(
        f"/api/v1/orders/{order_json['id']}/status/",
        {"status": OrderStatus.IN_PROGRESS},
        format="json",
    )
    assert r1.status_code == 200, r1.content

    mock_apprise_send.set_fail(False)
    mock_apprise_send.set_exception(RuntimeError("Telegram Bot API unreachable"))
    r2 = mgr_a_api.post(
        f"/api/v1/orders/{order_json['id']}/status/",
        {"status": OrderStatus.PRINTING},
        format="json",
    )
    assert r2.status_code == 200, r2.content

    order = Order.objects.get(id=order_json["id"])
    assert order.status == OrderStatus.PRINTING
    assert OrderStatusHistory.objects.filter(
        order=order, new_status=OrderStatus.PRINTING
    ).exists()


# ---------------------------------------------------------------------------
# Feature 4 Boundaries & Corner Cases (>= 6 tests)
# ---------------------------------------------------------------------------


def test_t2_f4_calculate_price_rejects_oversized_print(
    e2e_seed_data: Any,
    api_client_factory: Any,
) -> None:
    """Verify calculate-price rejects dimensions exceeding all salons' operational equipment."""
    c1_api = api_client_factory(
        e2e_seed_data.client_1.email,
        e2e_seed_data.passwords[e2e_seed_data.client_1.email],
    )
    resp = c1_api.post(
        "/api/v1/orders/calculate-price/",
        {
            "items": [
                {
                    "service_id": str(e2e_seed_data.service_wide.id),
                    "width_cm": 200.0,
                    "height_cm": 200.0,
                    "quantity": 1,
                    "material": "Баннер",
                    "options": [],
                }
            ]
        },
        format="json",
    )
    assert resp.status_code == 400, resp.content
    err_text = _extract_error_text(resp)
    assert any(ord(ch) >= 0x0400 for ch in err_text)


def test_t2_f4_no_capable_salon_routes_to_pending_routing(
    e2e_seed_data: Any,
    e2e_image_factory: Any,
    api_client_factory: Any,
    upload_photo_via_api: Any,
) -> None:
    """Verify US-02 1a: order with no capable operational salon enters PENDING_ROUTING."""
    e2e_seed_data.eq_b_wide.is_operational = False
    e2e_seed_data.eq_b_wide.save(update_fields=["is_operational"])

    c1_api = api_client_factory(
        e2e_seed_data.client_1.email,
        e2e_seed_data.passwords[e2e_seed_data.client_1.email],
    )
    order_json, _ = _create_online_order_via_api(
        c1_api,
        upload_photo_via_api,
        e2e_image_factory("JPEG"),
        str(e2e_seed_data.salon_a.id),
        str(e2e_seed_data.service_wide.id),
        width_cm=60.0,
        height_cm=90.0,
        material="Холст",
        options=[],
    )
    assert order_json["status"] == OrderStatus.PENDING_ROUTING
    assert _extract_id(order_json["assigned_salon"]) is None


def test_t2_f4_admin_reassign_to_incapable_salon_rejected_400_or_409(
    e2e_seed_data: Any,
    e2e_image_factory: Any,
    api_client_factory: Any,
    upload_photo_via_api: Any,
) -> None:
    """Verify US-02 3a: ADMIN reassign to salon lacking equipment/service returns 400 or 409."""
    from apps.core.models import Order

    c1_api = api_client_factory(
        e2e_seed_data.client_1.email,
        e2e_seed_data.passwords[e2e_seed_data.client_1.email],
    )
    order_json, _ = _create_online_order_via_api(
        c1_api,
        upload_photo_via_api,
        e2e_image_factory("JPEG"),
        str(e2e_seed_data.salon_a.id),
        str(e2e_seed_data.service_wide.id),
        width_cm=60.0,
        height_cm=90.0,
        material="Холст",
        options=[],
    )
    assert _extract_id(order_json["assigned_salon"]) == str(e2e_seed_data.salon_b.id)

    admin_api = api_client_factory(
        e2e_seed_data.admin.email,
        e2e_seed_data.passwords[e2e_seed_data.admin.email],
    )
    resp = admin_api.post(
        f"/api/v1/orders/{order_json['id']}/reassign/",
        {"salon_id": str(e2e_seed_data.salon_a.id)},
        format="json",
    )
    assert resp.status_code in (400, 409), resp.content

    order = Order.objects.get(id=order_json["id"])
    assert order.assigned_salon_id == e2e_seed_data.salon_b.id


def test_t2_f4_in_progress_to_pending_routing_clears_salon_and_reroutes(
    e2e_seed_data: Any,
    e2e_image_factory: Any,
    api_client_factory: Any,
    upload_photo_via_api: Any,
) -> None:
    """Verify US-03 1a: IN_PROGRESS -> PENDING_ROUTING clears salon and triggers re-routing."""
    from apps.core.models import Order, OrderStatusHistory

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

    mgr_a_api = api_client_factory(
        e2e_seed_data.manager_a.email,
        e2e_seed_data.passwords[e2e_seed_data.manager_a.email],
    )
    r_in_prog = mgr_a_api.post(
        f"/api/v1/orders/{order_json['id']}/status/",
        {"status": OrderStatus.IN_PROGRESS},
        format="json",
    )
    assert r_in_prog.status_code == 200, r_in_prog.content

    # Simulate equipment breakdown at salon A before returning order to routing.
    e2e_seed_data.eq_a_std.is_operational = False
    e2e_seed_data.eq_a_std.save(update_fields=["is_operational"])

    r_reroute = mgr_a_api.post(
        f"/api/v1/orders/{order_json['id']}/status/",
        {
            "status": OrderStatus.PENDING_ROUTING,
            "comment": "Поломка принтера в салоне А",
        },
        format="json",
    )
    assert r_reroute.status_code == 200, r_reroute.content

    order = Order.objects.get(id=order_json["id"])
    assert order.assigned_salon_id == e2e_seed_data.salon_b.id
    assert order.status == OrderStatus.ASSIGNED_TO_SALON
    assert OrderStatusHistory.objects.filter(
        order=order, new_status=OrderStatus.PENDING_ROUTING
    ).exists()


def test_t2_f4_invalid_fsm_transitions_rejected(
    e2e_seed_data: Any,
    e2e_image_factory: Any,
    api_client_factory: Any,
    upload_photo_via_api: Any,
) -> None:
    """Verify US-03: invalid transitions (ASSIGNED/NEW -> COMPLETED, COMPLETED -> NEW) fail 400."""
    from apps.core.models import Order

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

    admin_api = api_client_factory(
        e2e_seed_data.admin.email,
        e2e_seed_data.passwords[e2e_seed_data.admin.email],
    )
    # Invalid shortcut ASSIGNED_TO_SALON -> COMPLETED
    bad_shortcut = admin_api.post(
        f"/api/v1/orders/{order_json['id']}/status/",
        {"status": OrderStatus.COMPLETED},
        format="json",
    )
    assert bad_shortcut.status_code == 400, bad_shortcut.content

    # Invalid NEW -> COMPLETED
    new_order = Order.objects.create(
        order_number="YP-INV-NEW-01",
        pickup_code="112233",
        status=OrderStatus.NEW,
        client=e2e_seed_data.client_1,
        pickup_salon=e2e_seed_data.salon_a,
    )
    bad_new = admin_api.post(
        f"/api/v1/orders/{new_order.id}/status/",
        {"status": OrderStatus.COMPLETED},
        format="json",
    )
    assert bad_new.status_code == 400, bad_new.content
    new_order.refresh_from_db()
    assert new_order.status == OrderStatus.NEW

    # Invalid COMPLETED -> NEW
    done_order = Order.objects.create(
        order_number="YP-INV-DONE-01",
        pickup_code="445566",
        status=OrderStatus.COMPLETED,
        client=e2e_seed_data.client_1,
        pickup_salon=e2e_seed_data.salon_a,
        assigned_salon=e2e_seed_data.salon_a,
    )
    bad_done = admin_api.post(
        f"/api/v1/orders/{done_order.id}/status/",
        {"status": OrderStatus.NEW},
        format="json",
    )
    assert bad_done.status_code == 400, bad_done.content
    done_order.refresh_from_db()
    assert done_order.status == OrderStatus.COMPLETED


def test_t2_f4_wrong_pickup_code_and_defect_at_pickup_transitions(
    e2e_seed_data: Any,
    e2e_image_factory: Any,
    api_client_factory: Any,
    upload_photo_via_api: Any,
) -> None:
    """Verify US-06 5a (wrong pickup_code -> 400) and US-06 7a (defect -> NEEDS_CLARIFICATION)."""
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

    mgr_a_api = api_client_factory(
        e2e_seed_data.manager_a.email,
        e2e_seed_data.passwords[e2e_seed_data.manager_a.email],
    )
    for target in (OrderStatus.IN_PROGRESS, OrderStatus.PRINTING, OrderStatus.READY_FOR_PICKUP):
        r = mgr_a_api.post(
            f"/api/v1/orders/{order_json['id']}/status/",
            {"status": target},
            format="json",
        )
        assert r.status_code == 200, r.content

    wrong_code = "000000" if order_json["pickup_code"] != "000000" else "999999"
    bad_pickup = mgr_a_api.post(
        f"/api/v1/orders/{order_json['id']}/complete-pickup/",
        {"pickup_code": wrong_code},
        format="json",
    )
    assert bad_pickup.status_code == 400, bad_pickup.content

    detail = mgr_a_api.get(f"/api/v1/orders/{order_json['id']}/")
    assert detail.status_code == 200
    assert detail.json()["status"] == OrderStatus.READY_FOR_PICKUP

    defect_resp = mgr_a_api.post(
        f"/api/v1/orders/{order_json['id']}/status/",
        {
            "status": OrderStatus.NEEDS_CLARIFICATION,
            "comment": "Обнаружен брак при выдаче",
        },
        format="json",
    )
    assert defect_resp.status_code == 200, defect_resp.content
    assert defect_resp.json()["status"] == OrderStatus.NEEDS_CLARIFICATION


def test_t2_f4_in_transit_damaged_arrival_transitions_to_needs_clarification(
    e2e_seed_data: Any,
    e2e_image_factory: Any,
    api_client_factory: Any,
    upload_photo_via_api: Any,
) -> None:
    """Verify US-06 2a: pickup-salon manager flags damaged IN_TRANSIT order."""
    from apps.core.models import Order

    c1_api = api_client_factory(
        e2e_seed_data.client_1.email,
        e2e_seed_data.passwords[e2e_seed_data.client_1.email],
    )
    order_json, _ = _create_online_order_via_api(
        c1_api,
        upload_photo_via_api,
        e2e_image_factory("JPEG"),
        str(e2e_seed_data.salon_a.id),
        str(e2e_seed_data.service_wide.id),
        width_cm=40.0,
        height_cm=60.0,
        material="Холст",
    )
    assert _extract_id(order_json["pickup_salon"]) == str(e2e_seed_data.salon_a.id)
    assert _extract_id(order_json["assigned_salon"]) == str(e2e_seed_data.salon_b.id)

    mgr_b_api = api_client_factory(
        e2e_seed_data.manager_b.email,
        e2e_seed_data.passwords[e2e_seed_data.manager_b.email],
    )
    for target in (OrderStatus.IN_PROGRESS, OrderStatus.PRINTING, OrderStatus.IN_TRANSIT):
        r = mgr_b_api.post(
            f"/api/v1/orders/{order_json['id']}/status/",
            {"status": target},
            format="json",
        )
        assert r.status_code == 200, r.content

    # Assigned salon (manager_b) is forbidden from flagging arrival damage at pickup salon.
    wrong_mgr_resp = mgr_b_api.post(
        f"/api/v1/orders/{order_json['id']}/status/",
        {
            "status": OrderStatus.NEEDS_CLARIFICATION,
            "comment": "Заказ поступил поврежденным при доставке",
        },
        format="json",
    )
    assert wrong_mgr_resp.status_code == 403, wrong_mgr_resp.content

    mgr_a_api = api_client_factory(
        e2e_seed_data.manager_a.email,
        e2e_seed_data.passwords[e2e_seed_data.manager_a.email],
    )
    damage_comment = "Заказ поступил поврежденным при доставке"
    damage_resp = mgr_a_api.post(
        f"/api/v1/orders/{order_json['id']}/status/",
        {
            "status": OrderStatus.NEEDS_CLARIFICATION,
            "comment": damage_comment,
        },
        format="json",
    )
    assert damage_resp.status_code == 200, damage_resp.content

    order = Order.objects.get(id=order_json["id"])
    assert order.status == OrderStatus.NEEDS_CLARIFICATION
    assert damage_comment in order.clarification_comment


# ---------------------------------------------------------------------------
# Feature 5 Boundaries & Corner Cases (>= 6 tests)
# ---------------------------------------------------------------------------


@pytest.mark.security
def test_t2_f5_blocked_user_login_rejected(
    e2e_seed_data: Any,
    api_client_factory: Any,
) -> None:
    """Verify is_blocked=True user cannot log in via email or phone."""
    anon_api = api_client_factory()
    blocked_pw = e2e_seed_data.passwords[e2e_seed_data.blocked_user.email]

    for login_str in (e2e_seed_data.blocked_user.email, e2e_seed_data.blocked_user.phone):
        resp = anon_api.post(
            "/api/v1/auth/login/",
            {"login": login_str, "password": blocked_pw},
            format="json",
        )
        assert resp.status_code in (400, 401, 403), resp.content
        body = resp.json()
        assert not body.get("token")


@pytest.mark.security
def test_t2_f5_client_forbidden_from_admin_endpoints_403(
    e2e_seed_data: Any,
    e2e_image_factory: Any,
    api_client_factory: Any,
    upload_photo_via_api: Any,
) -> None:
    """Verify CLIENT receives 403 Forbidden on salon creation, user creation, and reassign."""
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

    salon_resp = c1_api.post(
        "/api/v1/salons/",
        {"name": "Нелегитимный Салон", "address": "ул. Хакерская, 1"},
        format="json",
    )
    assert salon_resp.status_code == 403

    user_resp = c1_api.post(
        "/api/v1/users/",
        {
            "name": "Фейк Админ",
            "email": "fake.admin@youphoto.ru",
            "phone": "+79009997766",
            "password": "Password123!",
            "role": UserRole.ADMIN,
        },
        format="json",
    )
    assert user_resp.status_code == 403

    reassign_resp = c1_api.post(
        f"/api/v1/orders/{order_json['id']}/reassign/",
        {"salon_id": str(e2e_seed_data.salon_b.id)},
        format="json",
    )
    assert reassign_resp.status_code == 403


@pytest.mark.security
def test_t2_f5_manager_forbidden_from_other_salon_catalog_403(
    e2e_seed_data: Any,
    api_client_factory: Any,
) -> None:
    """Verify MANAGER of salon A receives 403 when accessing or updating salon B catalog."""
    mgr_a_api = api_client_factory(
        e2e_seed_data.manager_a.email,
        e2e_seed_data.passwords[e2e_seed_data.manager_a.email],
    )
    catalog_url = f"/api/v1/salons/{e2e_seed_data.salon_b.id}/catalog/"

    assert mgr_a_api.get(catalog_url).status_code == 403
    patch_resp = mgr_a_api.patch(
        catalog_url,
        {
            "equipment_id": str(e2e_seed_data.eq_b_std.id),
            "is_operational": False,
        },
        format="json",
    )
    assert patch_resp.status_code == 403


def test_t2_f5_needs_clarification_without_comment_rejected_400(
    e2e_seed_data: Any,
    e2e_image_factory: Any,
    api_client_factory: Any,
    upload_photo_via_api: Any,
) -> None:
    """Verify transitioning to NEEDS_CLARIFICATION without a comment returns 400."""
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

    mgr_a_api = api_client_factory(
        e2e_seed_data.manager_a.email,
        e2e_seed_data.passwords[e2e_seed_data.manager_a.email],
    )
    r_prog = mgr_a_api.post(
        f"/api/v1/orders/{order_json['id']}/status/",
        {"status": OrderStatus.IN_PROGRESS},
        format="json",
    )
    assert r_prog.status_code == 200, r_prog.content

    bad_clarify = mgr_a_api.post(
        f"/api/v1/orders/{order_json['id']}/status/",
        {"status": OrderStatus.NEEDS_CLARIFICATION, "comment": "   "},
        format="json",
    )
    assert bad_clarify.status_code == 400, bad_clarify.content

    detail = mgr_a_api.get(f"/api/v1/orders/{order_json['id']}/")
    assert detail.json()["status"] == OrderStatus.IN_PROGRESS


def test_t2_f5_delete_salon_with_active_orders_blocked_409(
    e2e_seed_data: Any,
    e2e_image_factory: Any,
    api_client_factory: Any,
    upload_photo_via_api: Any,
) -> None:
    """Verify DELETE /api/v1/salons/<id>/ with active orders returns 409 conflict."""
    from apps.core.models import Salon

    c1_api = api_client_factory(
        e2e_seed_data.client_1.email,
        e2e_seed_data.passwords[e2e_seed_data.client_1.email],
    )
    _create_online_order_via_api(
        c1_api,
        upload_photo_via_api,
        e2e_image_factory("JPEG"),
        str(e2e_seed_data.salon_a.id),
        str(e2e_seed_data.service_standard.id),
    )

    admin_api = api_client_factory(
        e2e_seed_data.admin.email,
        e2e_seed_data.passwords[e2e_seed_data.admin.email],
    )
    del_resp = admin_api.delete(f"/api/v1/salons/{e2e_seed_data.salon_a.id}/")
    assert del_resp.status_code == 409, del_resp.content
    assert Salon.objects.filter(id=e2e_seed_data.salon_a.id).exists()


def test_t2_f5_disable_equipment_with_active_orders_warns_unless_force_reroute(
    e2e_seed_data: Any,
    e2e_image_factory: Any,
    api_client_factory: Any,
    upload_photo_via_api: Any,
) -> None:
    """Verify US-05 4a: disabling equipment with active orders blocks 409 unless force_reroute."""
    from apps.core.models import Equipment, Order

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
    catalog_url = f"/api/v1/salons/{e2e_seed_data.salon_a.id}/catalog/"

    blocked_resp = admin_api.patch(
        catalog_url,
        {
            "equipment_id": str(e2e_seed_data.eq_a_std.id),
            "is_operational": False,
            "equipment": [
                {
                    "id": str(e2e_seed_data.eq_a_std.id),
                    "name": e2e_seed_data.eq_a_std.name,
                    "equipment_type": e2e_seed_data.eq_a_std.equipment_type,
                    "max_width_cm": e2e_seed_data.eq_a_std.max_width_cm,
                    "max_height_cm": e2e_seed_data.eq_a_std.max_height_cm,
                    "is_operational": False,
                }
            ],
            "service_ids": [str(e2e_seed_data.service_standard.id)],
            "force_reroute": False,
        },
        format="json",
    )
    assert blocked_resp.status_code == 409, blocked_resp.content

    eq = Equipment.objects.get(id=e2e_seed_data.eq_a_std.id)
    assert eq.is_operational is True

    reroute_resp = admin_api.patch(
        catalog_url,
        {
            "equipment_id": str(e2e_seed_data.eq_a_std.id),
            "is_operational": False,
            "equipment": [
                {
                    "id": str(e2e_seed_data.eq_a_std.id),
                    "name": e2e_seed_data.eq_a_std.name,
                    "equipment_type": e2e_seed_data.eq_a_std.equipment_type,
                    "max_width_cm": e2e_seed_data.eq_a_std.max_width_cm,
                    "max_height_cm": e2e_seed_data.eq_a_std.max_height_cm,
                    "is_operational": False,
                }
            ],
            "service_ids": [str(e2e_seed_data.service_standard.id)],
            "force_reroute": True,
        },
        format="json",
    )
    assert reroute_resp.status_code == 200, reroute_resp.content

    eq.refresh_from_db()
    assert eq.is_operational is False

    order = Order.objects.get(id=order_json["id"])
    assert order.assigned_salon_id != e2e_seed_data.salon_a.id
    assert order.status in (OrderStatus.PENDING_ROUTING, OrderStatus.ASSIGNED_TO_SALON)


def test_t2_f5_invalid_salon_or_catalog_payload_rejected_400(
    e2e_seed_data: Any,
    api_client_factory: Any,
) -> None:
    """Verify US-05 3a: invalid salon creation and catalog equipment payloads return 400."""
    import re

    from apps.core.models import Equipment, Salon

    admin_api = api_client_factory(
        e2e_seed_data.admin.email,
        e2e_seed_data.passwords[e2e_seed_data.admin.email],
    )
    initial_salon_count = Salon.objects.count()
    initial_eq_count = Equipment.objects.count()

    bad_salon_resp = admin_api.post(
        "/api/v1/salons/",
        {"name": "", "address": "", "delivery_cost": "-50.00"},
        format="json",
    )
    assert bad_salon_resp.status_code == 400, bad_salon_resp.content
    salon_err = _extract_error_text(bad_salon_resp)
    assert re.search(r"[А-Яа-яЁё]", salon_err) is not None
    assert Salon.objects.count() == initial_salon_count

    bad_catalog_resp = admin_api.patch(
        f"/api/v1/salons/{e2e_seed_data.salon_a.id}/catalog/",
        {
            "equipment": [
                {
                    "name": "",
                    "equipment_type": "PHOTO_PRINTER",
                    "max_width_cm": -10.0,
                    "max_height_cm": 0.0,
                    "is_operational": True,
                }
            ],
            "service_ids": [str(e2e_seed_data.service_standard.id)],
        },
        format="json",
    )
    assert bad_catalog_resp.status_code == 400, bad_catalog_resp.content
    cat_err = _extract_error_text(bad_catalog_resp)
    assert re.search(r"[А-Яа-яЁё]", cat_err) is not None
    assert Equipment.objects.count() == initial_eq_count
