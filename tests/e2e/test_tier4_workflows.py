"""Tier 4 E2E tests: real-world multi-step end-to-end workflows (T4.1..T4.8)."""

from __future__ import annotations

import inspect
import re
import time
from datetime import timedelta
from decimal import Decimal
from pathlib import Path

import pytest
from apps.core.enums import (
    ORDER_STATUS_LABELS_RU,
    DocumentStandard,
    OrderStatus,
    OrderType,
    UserRole,
)


def _seed(seed_data, key: str):
    if isinstance(seed_data, dict):
        return seed_data[key]
    return getattr(seed_data, key)


def _password(seed_data, key: str) -> str:
    passwords = _seed(seed_data, "passwords")
    if key in passwords:
        return passwords[key]
    user = _seed(seed_data, key)
    return passwords[user.email]


def _default_material(service) -> str:
    materials = getattr(service, "allowed_materials", None) or []
    return materials[0] if materials else "Глянцевая бумага"


def _item_payload(
    service,
    photo_id: str | None,
    width_cm: float,
    height_cm: float,
    quantity: int = 1,
) -> dict:
    payload = {
        "service_id": str(service.id),
        "width_cm": width_cm,
        "height_cm": height_cm,
        "quantity": quantity,
        "material": _default_material(service),
        "options": [],
    }
    if photo_id is not None:
        payload["photo_id"] = str(photo_id)
    return payload


def _call_resolve_detail(resolve_web_url, order_id) -> str:
    try:
        return resolve_web_url("order_detail", order_id=order_id)
    except (TypeError, KeyError):
        return resolve_web_url("order_detail", pk=order_id)


def _discover_web_path(keywords: tuple[str, ...], fallback: str) -> str:
    from django.urls import get_resolver

    def _walk(patterns, prefix: str = ""):
        for pattern in patterns:
            route = prefix + str(getattr(pattern, "pattern", ""))
            if hasattr(pattern, "url_patterns"):
                yield from _walk(pattern.url_patterns, route)
            else:
                name = getattr(pattern, "name", "") or ""
                yield "/" + route.lstrip("/"), name

    for path, name in _walk(get_resolver().url_patterns):
        if path.startswith(("/api/", "/admin/")):
            continue
        haystack = f"{path} {name}".lower()
        if any(kw in haystack for kw in keywords) and "<" not in path:
            return path
    return fallback


class _PassiveMvpView:
    """Minimal passive view spy for MVP presenter workflow tests."""

    def __init__(self) -> None:
        self.orders: list[dict] = []
        self.errors: list[str] = []
        self.warnings: list[str] = []
        self.messages: list[str] = []
        self.confirm_answer: bool = True

    def __getattr__(self, name: str):
        if name.startswith(("confirm", "ask")):
            return lambda *args, **kwargs: self.confirm_answer
        if "error" in name:
            return lambda msg="", *a, **kw: self.errors.append(str(msg))
        if "warn" in name:
            return lambda msg="", *a, **kw: self.warnings.append(str(msg))
        if "order" in name and ("set" in name or "display" in name or "show" in name):
            return lambda data=None, *a, **kw: setattr(
                self, "orders", data if isinstance(data, list) else self.orders
            )
        return lambda *args, **kwargs: self.messages.append(name)


def _construct_with_matching_kwargs(cls, **candidates):
    sig = inspect.signature(cls.__init__)
    kwargs = {}
    for name, param in sig.parameters.items():
        if name == "self":
            continue
        if name in candidates:
            kwargs[name] = candidates[name]
        elif param.default is inspect.Parameter.empty and "view" in name:
            kwargs[name] = _PassiveMvpView()
    return cls(**kwargs)


def _bind_telegram(api_client, user, chat_id: str) -> None:
    from apps.notifications.services import TelegramBotBinder

    binder = TelegramBotBinder()
    sig = inspect.signature(binder.generate_binding_link)
    first_param = next(iter(sig.parameters))
    arg = user.id if first_param == "user_id" else user
    link = binder.generate_binding_link(arg)
    token = link.split("start=")[-1].strip()
    resp = api_client.post(
        "/api/v1/notifications/telegram/webhook/",
        data={"token": token, "chat_id": chat_id},
        format="json",
    )
    if resp.status_code >= 400:
        resp = api_client.post(
            "/api/v1/notifications/telegram/webhook/",
            data={"message": {"chat": {"id": chat_id}, "text": f"/start {token}"}},
            format="json",
        )
    assert resp.status_code == 200
    user.refresh_from_db()
    assert str(user.telegram_chat_id) == str(chat_id)


def _transition(client, order_id, status: OrderStatus, comment: str = ""):
    return client.post(
        f"/api/v1/orders/{order_id}/status/",
        data={"status": status, "comment": comment},
        format="json",
    )


@pytest.mark.django_db(transaction=True)
@pytest.mark.integration
def test_t4_01_full_happy_path_local_salon_and_30d_purge(
    e2e_env,
    e2e_image_factory,
    e2e_seed_data,
    api_client_factory,
    upload_photo_via_api,
    resolve_web_url,
    mock_apprise_send,
    live_server,
    tmp_path,
):
    """T4.1: Web reg -> TG bind -> upload -> price -> order -> desktop print -> pickup -> 30d."""
    from apps.core.models import Order, OrderItem, OrderStatusHistory, PhotoFile, User
    from desktop.api_client.rest_client import RestApiClient
    from desktop.hardware.printer_service import WindowsPrinterService
    from desktop.presenters.order_queue_presenter import OrderQueuePresenter
    from django.conf import settings
    from django.core.management import call_command
    from django.test import Client
    from django.utils import timezone

    salon_a = _seed(e2e_seed_data, "salon_a")
    manager_a = _seed(e2e_seed_data, "manager_a")
    svc_std = _seed(e2e_seed_data, "service_standard")

    # 1. Guest registers via Web UI.
    email = "t4_happy_client@youphoto.test"
    pwd = "HappyClientPass123!"
    web_client = Client()
    reg_resp = web_client.post(
        resolve_web_url("register"),
        data={
            "name": "Елена Счастливая",
            "phone": "+79990001122",
            "email": email,
            "password": pwd,
            "password_confirm": pwd,
            "pdn_consent": "on",
        },
        follow=False,
    )
    assert reg_resp.status_code in (301, 302)
    client_user = User.objects.get(email=email)
    assert client_user.role == UserRole.CLIENT

    # 2. Bind Telegram via webhook.
    c_api = api_client_factory(email, pwd)
    _bind_telegram(c_api, client_user, "44001122")

    # 3. Upload JPEG & TIFF, calculate price <= 3s, submit online order.
    jpeg_bytes = e2e_image_factory(fmt="JPEG", size=(800, 600))
    tiff_bytes = e2e_image_factory(fmt="TIFF", size=(1000, 750))
    p1 = upload_photo_via_api(c_api, jpeg_bytes, "family.jpg", "image/jpeg")
    p2 = upload_photo_via_api(c_api, tiff_bytes, "portrait.tiff", "image/tiff")
    assert p1.status_code in (200, 201)
    assert p2.status_code in (200, 201)
    p1_id = p1.json()["id"]
    p2_id = p2.json()["id"]

    items = [
        _item_payload(svc_std, p1_id, 10, 15, quantity=2),
        _item_payload(svc_std, p2_id, 15, 20, quantity=1),
    ]
    t0 = time.monotonic()
    calc = c_api.post("/api/v1/orders/calculate-price/", data={"items": items}, format="json")
    assert calc.status_code == 200
    assert (time.monotonic() - t0) <= 3.0
    assert Decimal(str(calc.json()["total_price"])) > Decimal(0)

    create_resp = c_api.post(
        "/api/v1/orders/",
        data={"pickup_salon_id": str(salon_a.id), "items": items},
        format="json",
    )
    assert create_resp.status_code == 201
    order_json = create_resp.json()
    order_id = order_json["id"]
    order = Order.objects.get(id=order_id)
    assert order.status == OrderStatus.ASSIGNED_TO_SALON
    assert order.assigned_salon_id == salon_a.id

    # 4. Desktop OrderQueuePresenter accepts, downloads photos, prints, marks READY_FOR_PICKUP.
    rest = RestApiClient(base_url=f"{live_server.url}/api/v1")
    rest.login(manager_a.email, _password(e2e_seed_data, "manager_a"))
    printer_svc = WindowsPrinterService()
    printers = printer_svc.list_available_printers()
    assert len(printers) >= 1

    presenter = _construct_with_matching_kwargs(
        OrderQueuePresenter,
        api_client=rest,
        rest_client=rest,
        client=rest,
        printer_service=printer_svc,
        view=_PassiveMvpView(),
    )
    queue = presenter.refresh_queue()
    assert any(str(q["id"]) == str(order_id) for q in queue)

    presenter.change_order_status(order_id, OrderStatus.IN_PROGRESS)
    dl_dir = tmp_path / "t4_01_photos"
    dl_dir.mkdir(parents=True, exist_ok=True)
    files = presenter.download_order_photos(order_id, str(dl_dir))
    assert len(files) == 2
    assert {Path(f).read_bytes() for f in files} == {jpeg_bytes, tiff_bytes}

    presenter.change_order_status(order_id, OrderStatus.PRINTING)
    print_res = presenter.print_order(order_id, printers[0].name)
    assert print_res is not None
    if isinstance(print_res, list):
        assert len(print_res) >= 1
        assert all(r.is_success is True for r in print_res)
    else:
        assert print_res.is_success is True

    presenter.change_order_status(order_id, OrderStatus.READY_FOR_PICKUP)
    order.refresh_from_db()
    assert order.status == OrderStatus.READY_FOR_PICKUP
    assert len(mock_apprise_send.calls) >= 1

    # 5. Complete pickup with 6-digit pickup_code.
    completed = presenter.complete_pickup(order_id, order.pickup_code)
    assert completed is True or (
        isinstance(completed, dict) and completed.get("status") == OrderStatus.COMPLETED
    )
    order.refresh_from_db()
    assert order.status == OrderStatus.COMPLETED
    assert order.completed_at is not None

    # 6. Time-shift >30 days and run purge_expired_photos.
    p1_obj = PhotoFile.objects.get(id=p1_id)
    p2_obj = PhotoFile.objects.get(id=p2_id)
    storage_root = Path(settings.MEDIA_ROOT) / "storage"
    assert (storage_root / p1_obj.file_key).exists()
    assert (storage_root / p2_obj.file_key).exists()

    Order.objects.filter(id=order_id).update(completed_at=timezone.now() - timedelta(days=31))
    PhotoFile.objects.filter(id__in=[p1_id, p2_id]).update(
        delete_after=timezone.now() - timedelta(hours=2)
    )
    call_command("purge_expired_photos")

    p1_obj.refresh_from_db()
    p2_obj.refresh_from_db()
    assert p1_obj.is_deleted_from_storage is True
    assert p2_obj.is_deleted_from_storage is True
    assert not (storage_root / p1_obj.file_key).exists()
    assert not (storage_root / p2_obj.file_key).exists()
    assert Order.objects.filter(id=order_id).exists()
    assert OrderItem.objects.filter(order_id=order_id).count() == 2
    assert OrderStatusHistory.objects.filter(order_id=order_id).count() >= 5


@pytest.mark.django_db(transaction=True)
@pytest.mark.integration
def test_t4_02_cross_salon_routing_and_inter_salon_transit(
    e2e_env,
    e2e_image_factory,
    e2e_seed_data,
    api_client_factory,
    upload_photo_via_api,
    mock_apprise_send,
):
    """T4.2: Pickup Salon A + wide item -> routed unsplit to Salon B -> IN_TRANSIT -> Salon A."""
    from apps.core.models import Order

    client1 = _seed(e2e_seed_data, "client_1")
    manager_a = _seed(e2e_seed_data, "manager_a")
    manager_b = _seed(e2e_seed_data, "manager_b")
    salon_a = _seed(e2e_seed_data, "salon_a")
    salon_b = _seed(e2e_seed_data, "salon_b")
    svc_std = _seed(e2e_seed_data, "service_standard")
    svc_wide = _seed(e2e_seed_data, "service_wide")

    client1.telegram_chat_id = "22003344"
    client1.save(update_fields=["telegram_chat_id"])

    c_api = api_client_factory(client1.email, _password(e2e_seed_data, "client_1"))
    ma_api = api_client_factory(manager_a.email, _password(e2e_seed_data, "manager_a"))
    mb_api = api_client_factory(manager_b.email, _password(e2e_seed_data, "manager_b"))

    p_std = upload_photo_via_api(
        c_api, e2e_image_factory(fmt="JPEG"), "std.jpg", "image/jpeg"
    ).json()["id"]
    p_wide = upload_photo_via_api(
        c_api, e2e_image_factory(fmt="PNG"), "wide.png", "image/png"
    ).json()["id"]

    # Mixed order (standard + wide) must NOT split across salons; goes wholly to salon_b.
    order_resp = c_api.post(
        "/api/v1/orders/",
        data={
            "pickup_salon_id": str(salon_a.id),
            "items": [
                _item_payload(svc_std, p_std, 10, 15, quantity=2),
                _item_payload(svc_wide, p_wide, 40, 60, quantity=1),
            ],
        },
        format="json",
    )
    assert order_resp.status_code == 201
    order_id = order_resp.json()["id"]
    order = Order.objects.get(id=order_id)
    assert order.assigned_salon_id == salon_b.id
    assert order.pickup_salon_id == salon_a.id
    assert order.items.count() == 2

    # Salon B fulfills -> IN_TRANSIT.
    assert _transition(mb_api, order_id, OrderStatus.IN_PROGRESS).status_code == 200
    assert _transition(mb_api, order_id, OrderStatus.PRINTING).status_code == 200
    transit_resp = _transition(mb_api, order_id, OrderStatus.READY_FOR_PICKUP)
    if transit_resp.status_code >= 400:
        transit_resp = _transition(mb_api, order_id, OrderStatus.IN_TRANSIT)
    assert transit_resp.status_code == 200
    order.refresh_from_db()
    assert order.status == OrderStatus.IN_TRANSIT

    # Salon B cannot complete pickup before arrival at Salon A.
    premature = mb_api.post(
        f"/api/v1/orders/{order_id}/complete-pickup/",
        data={"pickup_code": order.pickup_code},
        format="json",
    )
    assert premature.status_code in (400, 403)

    # Salon A receives -> READY_FOR_PICKUP -> COMPLETED.
    assert _transition(ma_api, order_id, OrderStatus.READY_FOR_PICKUP).status_code == 200
    assert len(mock_apprise_send.calls) >= 1

    wrong_code = "999999" if order.pickup_code != "999999" else "111111"
    assert (
        ma_api.post(
            f"/api/v1/orders/{order_id}/complete-pickup/",
            data={"pickup_code": wrong_code},
            format="json",
        ).status_code
        == 400
    )

    ok_resp = ma_api.post(
        f"/api/v1/orders/{order_id}/complete-pickup/",
        data={"pickup_code": order.pickup_code},
        format="json",
    )
    assert ok_resp.status_code == 200
    order.refresh_from_db()
    assert order.status == OrderStatus.COMPLETED


@pytest.mark.django_db(transaction=True)
@pytest.mark.integration
def test_t4_03_equipment_breakdown_and_admin_manual_reassignment(
    e2e_env, e2e_image_factory, e2e_seed_data, api_client_factory, upload_photo_via_api
):
    """T4.3: IN_PROGRESS breakdown -> PENDING_ROUTING -> invalid reassign rejected -> Salon B."""
    from apps.core.models import Order, OrderStatusHistory

    admin = _seed(e2e_seed_data, "admin")
    client1 = _seed(e2e_seed_data, "client_1")
    manager_a = _seed(e2e_seed_data, "manager_a")
    manager_b = _seed(e2e_seed_data, "manager_b")
    salon_a = _seed(e2e_seed_data, "salon_a")
    salon_b = _seed(e2e_seed_data, "salon_b")
    eq_a_std = _seed(e2e_seed_data, "eq_a_std")
    eq_b_std = _seed(e2e_seed_data, "eq_b_std")
    svc_std = _seed(e2e_seed_data, "service_standard")

    c_api = api_client_factory(client1.email, _password(e2e_seed_data, "client_1"))
    ma_api = api_client_factory(manager_a.email, _password(e2e_seed_data, "manager_a"))
    mb_api = api_client_factory(manager_b.email, _password(e2e_seed_data, "manager_b"))
    ad_api = api_client_factory(admin.email, _password(e2e_seed_data, "admin"))

    photo_id = upload_photo_via_api(
        c_api, e2e_image_factory(fmt="JPEG"), "equip_fail.jpg", "image/jpeg"
    ).json()["id"]
    order_resp = c_api.post(
        "/api/v1/orders/",
        data={
            "pickup_salon_id": str(salon_a.id),
            "items": [_item_payload(svc_std, photo_id, 10, 15, quantity=1)],
        },
        format="json",
    )
    assert order_resp.status_code == 201
    order_id = order_resp.json()["id"]
    assert _transition(ma_api, order_id, OrderStatus.IN_PROGRESS).status_code == 200

    # Both standard printers offline -> PENDING_ROUTING awaits Admin reassignment.
    eq_a_std.is_operational = False
    eq_a_std.save(update_fields=["is_operational"])
    eq_b_std.is_operational = False
    eq_b_std.save(update_fields=["is_operational"])

    assert (
        _transition(
            ma_api, order_id, OrderStatus.PENDING_ROUTING, comment="Поломка оборудования"
        ).status_code
        == 200
    )
    order = Order.objects.get(id=order_id)
    assert order.status == OrderStatus.PENDING_ROUTING
    assert order.assigned_salon is None

    # Admin tries to assign to broken salon_a -> EquipmentMismatchError (400/409).
    bad_reassign = ad_api.post(
        f"/api/v1/orders/{order_id}/reassign/",
        data={"salon_id": str(salon_a.id), "comment": "Попытка на неисправный салон"},
        format="json",
    )
    assert bad_reassign.status_code in (400, 409)

    # Restore salon_b equipment -> Admin manually reassigns to salon_b.
    eq_b_std.is_operational = True
    eq_b_std.save(update_fields=["is_operational"])

    ok_reassign = ad_api.post(
        f"/api/v1/orders/{order_id}/reassign/",
        data={"salon_id": str(salon_b.id), "comment": "Назначено на салон B"},
        format="json",
    )
    assert ok_reassign.status_code == 200
    order.refresh_from_db()
    assert order.status == OrderStatus.ASSIGNED_TO_SALON
    assert order.assigned_salon_id == salon_b.id
    assert OrderStatusHistory.objects.filter(order=order, actor=admin).exists()

    # Salon B completes and sends to Salon A for pickup.
    assert _transition(mb_api, order_id, OrderStatus.IN_PROGRESS).status_code == 200
    assert _transition(mb_api, order_id, OrderStatus.PRINTING).status_code == 200
    assert _transition(mb_api, order_id, OrderStatus.IN_TRANSIT).status_code == 200
    assert _transition(ma_api, order_id, OrderStatus.READY_FOR_PICKUP).status_code == 200
    assert (
        ma_api.post(
            f"/api/v1/orders/{order_id}/complete-pickup/",
            data={"pickup_code": order.pickup_code},
            format="json",
        ).status_code
        == 200
    )
    order.refresh_from_db()
    assert order.status == OrderStatus.COMPLETED


@pytest.mark.django_db(transaction=True)
@pytest.mark.integration
def test_t4_04_photo_clarification_loop_replacement_and_agree_as_is(
    e2e_env,
    e2e_image_factory,
    e2e_seed_data,
    api_client_factory,
    upload_photo_via_api,
    resolve_web_url,
    mock_apprise_send,
):
    """T4.4: Clarification loop branch A (new photo upload) and branch B (agree as-is)."""
    from apps.core.models import Order
    from django.test import Client

    client1 = _seed(e2e_seed_data, "client_1")
    manager_a = _seed(e2e_seed_data, "manager_a")
    salon_a = _seed(e2e_seed_data, "salon_a")
    svc_std = _seed(e2e_seed_data, "service_standard")

    client1.telegram_chat_id = "66007788"
    client1.save(update_fields=["telegram_chat_id"])

    c_api = api_client_factory(client1.email, _password(e2e_seed_data, "client_1"))
    m_api = api_client_factory(manager_a.email, _password(e2e_seed_data, "manager_a"))

    def _make_in_progress_order(filename: str) -> str:
        p = upload_photo_via_api(
            c_api, e2e_image_factory(fmt="JPEG"), filename, "image/jpeg"
        ).json()["id"]
        o_resp = c_api.post(
            "/api/v1/orders/",
            data={
                "pickup_salon_id": str(salon_a.id),
                "items": [_item_payload(svc_std, p, 10, 15, quantity=1)],
            },
            format="json",
        )
        assert o_resp.status_code == 201
        oid = o_resp.json()["id"]
        assert _transition(m_api, oid, OrderStatus.IN_PROGRESS).status_code == 200
        return oid

    # Branch A: Replacement photo upload.
    order_a_id = _make_in_progress_order("dark.jpg")
    comment_a = "Слишком темное фото, загрузите светлее"
    assert (
        _transition(
            m_api, order_a_id, OrderStatus.NEEDS_CLARIFICATION, comment=comment_a
        ).status_code
        == 200
    )
    assert len(mock_apprise_send.calls) >= 1

    web_client = Client()
    web_client.force_login(client1)
    detail_html = web_client.get(_call_resolve_detail(resolve_web_url, order_a_id)).content.decode(
        "utf-8"
    )
    assert ORDER_STATUS_LABELS_RU[OrderStatus.NEEDS_CLARIFICATION] in detail_html
    assert comment_a in detail_html
    assert "Согласен печатать как есть" in detail_html

    replacement_id = upload_photo_via_api(
        c_api, e2e_image_factory(fmt="PNG", size=(1600, 1200)), "bright.png", "image/png"
    ).json()["id"]
    resp_a = c_api.post(
        f"/api/v1/orders/{order_a_id}/clarification-response/",
        data={"photo_id": replacement_id, "comment": "Заменил на светлое фото"},
        format="json",
    )
    assert resp_a.status_code == 200
    order_a = Order.objects.get(id=order_a_id)
    assert order_a.status == OrderStatus.IN_PROGRESS

    assert _transition(m_api, order_a_id, OrderStatus.PRINTING).status_code == 200
    assert _transition(m_api, order_a_id, OrderStatus.READY_FOR_PICKUP).status_code == 200
    assert (
        m_api.post(
            f"/api/v1/orders/{order_a_id}/complete-pickup/",
            data={"pickup_code": order_a.pickup_code},
            format="json",
        ).status_code
        == 200
    )
    order_a.refresh_from_db()
    assert order_a.status == OrderStatus.COMPLETED

    # Branch B: Client agrees "as-is".
    order_b_id = _make_in_progress_order("soft_edges.jpg")
    comment_b = "Есть легкое размытие по краям"
    assert (
        _transition(
            m_api, order_b_id, OrderStatus.NEEDS_CLARIFICATION, comment=comment_b
        ).status_code
        == 200
    )
    resp_b = c_api.post(
        f"/api/v1/orders/{order_b_id}/clarification-response/",
        data={"agree_as_is": True, "comment": "Согласен печатать как есть"},
        format="json",
    )
    assert resp_b.status_code == 200
    order_b = Order.objects.get(id=order_b_id)
    assert order_b.status == OrderStatus.IN_PROGRESS
    assert _transition(m_api, order_b_id, OrderStatus.PRINTING).status_code == 200
    assert _transition(m_api, order_b_id, OrderStatus.READY_FOR_PICKUP).status_code == 200
    assert (
        m_api.post(
            f"/api/v1/orders/{order_b_id}/complete-pickup/",
            data={"pickup_code": order_b.pickup_code},
            format="json",
        ).status_code
        == 200
    )
    order_b.refresh_from_db()
    assert order_b.status == OrderStatus.COMPLETED


@pytest.mark.django_db(transaction=True)
@pytest.mark.desktop
def test_t4_05_in_salon_walk_in_document_photo_workflow(
    e2e_env,
    e2e_image_factory,
    e2e_seed_data,
    live_server,
    tmp_path,
    monkeypatch,
):
    """T4.5 (US-07): Disconnected camera 3a -> import -> 413x531 crop -> print fail 8a -> retry."""
    from apps.core.models import Order
    from desktop.api_client.rest_client import RestApiClient
    from desktop.hardware.camera_service import (
        CameraConnectionError,
        DocumentCropper,
        UsbCameraService,
    )
    from desktop.hardware.dto import PrintResult
    from desktop.hardware.printer_service import WindowsPrinterService
    from desktop.presenters.document_photo_presenter import DocumentPhotoPresenter
    from desktop.presenters.order_queue_presenter import OrderQueuePresenter
    from PIL import Image

    manager_a = _seed(e2e_seed_data, "manager_a")
    salon_a = _seed(e2e_seed_data, "salon_a")

    rest = RestApiClient(base_url=f"{live_server.url}/api/v1")
    rest.login(manager_a.email, _password(e2e_seed_data, "manager_a"))

    # 1. Verify disconnected camera (3a) raises CameraConnectionError and creates no order.
    empty_cam_dir = tmp_path / "empty_cam"
    empty_cam_dir.mkdir(parents=True, exist_ok=True)
    disconnected_cam = _construct_with_matching_kwargs(
        UsbCameraService,
        mock_mode=True,
        mock_connected=False,
        import_dir=str(empty_cam_dir),
        mock_dir=str(empty_cam_dir),
        source_dir=empty_cam_dir,
        connected=False,
    )
    if hasattr(disconnected_cam, "is_camera_connected"):
        monkeypatch.setattr(disconnected_cam, "is_camera_connected", lambda: False)

    initial_orders = Order.objects.count()
    with pytest.raises(CameraConnectionError):
        disconnected_cam.import_latest_photo()
    assert Order.objects.count() == initial_orders

    # 2. Connected mock camera with synthetic portrait -> import & crop 413x531 @ 300 DPI.
    cam_dir = tmp_path / "connected_cam"
    cam_dir.mkdir(parents=True, exist_ok=True)
    portrait_file = cam_dir / "IMG_PASSPORT.jpg"
    portrait_file.write_bytes(e2e_image_factory(fmt="JPEG", size=(1600, 1200)))

    camera = _construct_with_matching_kwargs(
        UsbCameraService,
        mock_mode=True,
        mock_connected=True,
        mock_image_path=str(portrait_file),
        import_dir=str(cam_dir),
        mock_dir=str(cam_dir),
        source_dir=cam_dir,
        cropper=DocumentCropper(),
    )
    if hasattr(camera, "set_mock_photo"):
        camera.set_mock_photo(str(portrait_file))
    elif hasattr(camera, "mock_photo_path"):
        camera.mock_photo_path = str(portrait_file)

    cropper = DocumentCropper()
    printer = WindowsPrinterService()
    printers = printer.list_available_printers()
    assert len(printers) >= 1
    printer_name = printers[0].name

    view = _PassiveMvpView()
    presenter = _construct_with_matching_kwargs(
        DocumentPhotoPresenter,
        api_client=rest,
        rest_client=rest,
        client=rest,
        camera_service=camera,
        camera=camera,
        cropper=cropper,
        printer_service=printer,
        printer=printer,
        view=view,
    )
    imported = presenter.import_from_camera()
    assert Path(imported.local_file_path).exists()

    cropped = presenter.apply_auto_crop(DocumentStandard.RF_PASSPORT_35x45)
    with Image.open(cropped) as cropped_img:
        assert cropped_img.size == (413, 531)

    # 3. Simulate 1st print failure (8a), then retry success.
    orig_send = printer.send_to_print
    attempts = {"count": 0}

    def _flaky_send(job):
        attempts["count"] += 1
        if attempts["count"] == 1:
            return PrintResult(
                is_success=False,
                spooler_job_id=0,
                error_message="Принтер недоступен или остановлен. Проверьте питание.",
            )
        return orig_send(job)

    monkeypatch.setattr(printer, "send_to_print", _flaky_send)

    first_try = presenter.confirm_and_print(copies=4, printer_name=printer_name)
    first_print = first_try[-1] if isinstance(first_try, tuple) else first_try
    assert first_print is not None or len(view.errors) > 0
    if hasattr(first_print, "is_success"):
        assert first_print.is_success is False

    # Retry print without creating a duplicate IN_SALON order.
    if hasattr(presenter, "retry_print"):
        retry_res = presenter.retry_print(printer_name=printer_name)
        assert retry_res.is_success is True
    else:
        existing_order = (
            Order.objects.filter(order_type=OrderType.IN_SALON).order_by("-created_at").first()
        )
        assert existing_order is not None
        queue_presenter = _construct_with_matching_kwargs(
            OrderQueuePresenter,
            api_client=rest,
            rest_client=rest,
            client=rest,
            printer_service=printer,
            printer=printer,
            view=_PassiveMvpView(),
        )
        retry_res = queue_presenter.print_order(str(existing_order.id), printer_name)
        assert retry_res is not None
        if isinstance(retry_res, list):
            assert len(retry_res) >= 1
            assert all(r.is_success is True for r in retry_res)
        else:
            assert retry_res.is_success is True

    in_salon_qs = Order.objects.filter(order_type=OrderType.IN_SALON).order_by("-created_at")
    assert in_salon_qs.count() == 1
    in_salon_order = in_salon_qs.first()
    assert in_salon_order is not None
    assert in_salon_order.client is None
    assert in_salon_order.pickup_salon_id == salon_a.id
    assert in_salon_order.assigned_salon_id == salon_a.id
    assert in_salon_order.status == OrderStatus.PRINTING

    # 4. Deferred pickup (10a): PRINTING -> READY_FOR_PICKUP -> COMPLETED via pickup_code.
    rest.update_order_status(str(in_salon_order.id), OrderStatus.READY_FOR_PICKUP)
    in_salon_order.refresh_from_db()
    assert in_salon_order.status == OrderStatus.READY_FOR_PICKUP

    rest.complete_pickup(str(in_salon_order.id), in_salon_order.pickup_code)
    in_salon_order.refresh_from_db()
    assert in_salon_order.status == OrderStatus.COMPLETED


@pytest.mark.django_db(transaction=True)
@pytest.mark.integration
def test_t4_06_admin_salon_staff_and_catalog_lifecycle(
    e2e_env,
    e2e_image_factory,
    e2e_seed_data,
    api_client_factory,
    upload_photo_via_api,
    resolve_web_url,
):
    """T4.6 (US-05): Admin creates Salon C+Manager C -> RBAC 403 -> delete conflict 409 -> hide."""
    from apps.core.models import Order
    from django.test import Client

    admin = _seed(e2e_seed_data, "admin")
    client1 = _seed(e2e_seed_data, "client_1")
    salon_a = _seed(e2e_seed_data, "salon_a")
    salon_b = _seed(e2e_seed_data, "salon_b")
    eq_a_std = _seed(e2e_seed_data, "eq_a_std")
    svc_std = _seed(e2e_seed_data, "service_standard")

    ad_api = api_client_factory(admin.email, _password(e2e_seed_data, "admin"))
    c_api = api_client_factory(client1.email, _password(e2e_seed_data, "client_1"))

    # 1. Admin creates Salon C and Manager C.
    salon_c_resp = ad_api.post(
        "/api/v1/salons/",
        data={
            "name": "Салон C (Центр)",
            "address": "ул. Большая Покровская, 10",
            "is_active": True,
            "delivery_cost": "120.00",
        },
        format="json",
    )
    assert salon_c_resp.status_code in (200, 201)
    salon_c_id = salon_c_resp.json()["id"]

    mgr_c_email = "manager_c@youphoto.test"
    mgr_c_pwd = "ManagerCPass123!"
    mgr_c_resp = ad_api.post(
        "/api/v1/users/",
        data={
            "name": "Менеджер Салона C",
            "email": mgr_c_email,
            "phone": "+79997776655",
            "password": mgr_c_pwd,
            "role": UserRole.MANAGER,
            "salon": salon_c_id,
            "salon_id": salon_c_id,
        },
        format="json",
    )
    assert mgr_c_resp.status_code in (200, 201)

    mc_api = api_client_factory(mgr_c_email, mgr_c_pwd)

    # 2. Manager C cannot edit Salon A catalog (403), but can edit Salon C catalog (200/201).
    assert (
        mc_api.patch(
            f"/api/v1/salons/{salon_a.id}/catalog/",
            data={"service_ids": [str(svc_std.id)]},
            format="json",
        ).status_code
        == 403
    )
    cat_c_resp = mc_api.post(
        f"/api/v1/salons/{salon_c_id}/catalog/",
        data={
            "equipment": [
                {
                    "name": "Принтер Салона C",
                    "equipment_type": eq_a_std.equipment_type,
                    "max_width_cm": 30.0,
                    "max_height_cm": 45.0,
                    "is_operational": True,
                }
            ],
            "service_ids": [str(svc_std.id)],
        },
        format="json",
    )
    assert cat_c_resp.status_code in (200, 201)

    # 3. Create active order at Salon A -> DELETE Salon A blocked with 409.
    photo_id = upload_photo_via_api(
        c_api, e2e_image_factory(fmt="JPEG"), "salon_a_active.jpg", "image/jpeg"
    ).json()["id"]
    order_id = c_api.post(
        "/api/v1/orders/",
        data={
            "pickup_salon_id": str(salon_a.id),
            "items": [_item_payload(svc_std, photo_id, 10, 15, quantity=1)],
        },
        format="json",
    ).json()["id"]
    assert str(Order.objects.get(id=order_id).assigned_salon_id) == str(salon_a.id)

    assert ad_api.delete(f"/api/v1/salons/{salon_a.id}/").status_code == 409

    # 4. Disable Salon A equipment with force_reroute=True -> order moves to Salon B or C.
    reroute_resp = ad_api.patch(
        f"/api/v1/salons/{salon_a.id}/catalog/",
        data={
            "equipment_id": str(eq_a_std.id),
            "is_operational": False,
            "force_reroute": True,
            "equipment": [
                {
                    "id": str(eq_a_std.id),
                    "name": eq_a_std.name,
                    "equipment_type": eq_a_std.equipment_type,
                    "max_width_cm": eq_a_std.max_width_cm,
                    "max_height_cm": eq_a_std.max_height_cm,
                    "is_operational": False,
                }
            ],
        },
        format="json",
    )
    assert reroute_resp.status_code == 200
    moved_order = Order.objects.get(id=order_id)
    assert str(moved_order.assigned_salon_id) in {str(salon_b.id), str(salon_c_id)}

    # 5. Hide Salon A -> excluded from client Web pickup list.
    hide_resp = ad_api.patch(
        f"/api/v1/salons/{salon_a.id}/",
        data={"is_active": False},
        format="json",
    )
    assert hide_resp.status_code == 200

    web_client = Client()
    web_client.force_login(client1)
    create_html = web_client.get(resolve_web_url("order_create")).content.decode("utf-8")
    assert salon_a.name not in create_html
    assert salon_b.name in create_html


@pytest.mark.django_db(transaction=True)
@pytest.mark.integration
def test_t4_07_client_web_responsive_and_error_recovery_journey(
    e2e_env,
    e2e_image_factory,
    e2e_seed_data,
    api_client_factory,
    upload_photo_via_api,
    resolve_web_url,
):
    """T4.7: ?next=/orders/create/ -> pdn_consent -> BMP 2a recovery -> cancel 7a -> submit."""
    from apps.core.models import Order, User
    from django.test import Client

    salon_a = _seed(e2e_seed_data, "salon_a")
    salon_hidden = _seed(e2e_seed_data, "salon_hidden")
    svc_std = _seed(e2e_seed_data, "service_standard")

    web = Client()
    create_url = resolve_web_url("order_create")

    # 1. Guest redirected with ?next=/orders/create/.
    anon_get = web.get(create_url, follow=False)
    assert anon_get.status_code in (301, 302)
    assert "next=" in anon_get.url

    # 2. Registration without pdn_consent rejected (3a); valid pdn_consent succeeds.
    email = "t4_web_journey@youphoto.test"
    pwd = "WebJourneyPass123!"
    bad_reg = web.post(
        resolve_web_url("register"),
        data={
            "name": "Ольга Веб",
            "phone": "+79993332211",
            "email": email,
            "password": pwd,
            "password_confirm": pwd,
        },
        follow=False,
    )
    assert bad_reg.status_code == 200
    assert not User.objects.filter(email=email).exists()

    ok_reg = web.post(
        f"{resolve_web_url('register')}?next={create_url}",
        data={
            "name": "Ольга Веб",
            "phone": "+79993332211",
            "email": email,
            "password": pwd,
            "password_confirm": pwd,
            "pdn_consent": "on",
        },
        follow=False,
    )
    assert ok_reg.status_code in (301, 302)
    assert create_url in ok_reg.url

    # 3. Verify responsive HTML & Russian constraints on /orders/create/.
    form_resp = web.get(create_url)
    assert form_resp.status_code == 200
    form_html = form_resp.content.decode("utf-8")
    assert "width=device-width" in form_html
    assert "JPEG, PNG, HEIC, TIFF до 50 МБ" in form_html
    assert salon_a.name in form_html
    assert salon_hidden.name not in form_html
    assert not re.search(
        r"<(?:option|label)\b[^>]*>[^<]*курьер|<input\b[^>]*(?:courier|курьер)[^>]*>",
        form_html,
        re.IGNORECASE,
    )

    # 4. Upload valid JPEG + invalid BMP (2a keeps valid JPEG).
    c_api = api_client_factory(email, pwd)
    valid_jpeg = upload_photo_via_api(
        c_api, e2e_image_factory(fmt="JPEG"), "good.jpg", "image/jpeg"
    )
    assert valid_jpeg.status_code in (200, 201)
    valid_photo_id = valid_jpeg.json()["id"]

    bmp_bytes = b"BM" + b"\x00" * 128
    invalid_bmp = upload_photo_via_api(c_api, bmp_bytes, "bad.bmp", "image/bmp")
    assert invalid_bmp.status_code == 400
    assert "JPEG" in invalid_bmp.content.decode("utf-8")

    # 5. Cancel draft (7a) -> no orders created for this user.
    user = User.objects.get(email=email)
    cancel_url = _discover_web_path(("cancel",), create_url)
    cancel_resp = web.post(
        cancel_url,
        data={"action": "cancel", "cancel": "1"},
        follow=False,
    )
    assert cancel_resp.status_code in (200, 301, 302)
    assert Order.objects.filter(client=user).count() == 0

    # 6. Recreate & submit order -> verify detail & list Russian UI + StatusPollerJS.
    submit_resp = c_api.post(
        "/api/v1/orders/",
        data={
            "pickup_salon_id": str(salon_a.id),
            "items": [_item_payload(svc_std, valid_photo_id, 10, 15, quantity=2)],
        },
        format="json",
    )
    assert submit_resp.status_code == 201
    order = Order.objects.get(id=submit_resp.json()["id"])

    list_html = web.get(resolve_web_url("order_list")).content.decode("utf-8")
    assert order.order_number in list_html
    assert ORDER_STATUS_LABELS_RU[OrderStatus.ASSIGNED_TO_SALON] in list_html

    detail_html = web.get(_call_resolve_detail(resolve_web_url, order.id)).content.decode("utf-8")
    assert order.order_number in detail_html
    assert order.pickup_code in detail_html
    assert ORDER_STATUS_LABELS_RU[OrderStatus.ASSIGNED_TO_SALON] in detail_html
    assert "status_poller.js" in detail_html

    static_poller = (
        Path(__file__).resolve().parents[2] / "backend" / "static" / "js" / "status_poller.js"
    )
    assert static_poller.exists()
    poller_src = static_poller.read_text(encoding="utf-8")
    assert "5000" in poller_src
    assert "/api/v1/orders/" in poller_src


@pytest.mark.django_db(transaction=True)
@pytest.mark.integration
def test_t4_08_devops_ci_deployment_docs_acceptance_audit(e2e_env):
    """T4.8: Audit deploy/*, .github/workflows/ci.yml, docs, migrate/seed/purge readiness."""
    from django.core.management import call_command

    root = Path(__file__).resolve().parents[2]

    dockerfile = root / "deploy" / "Dockerfile.backend"
    compose = root / "deploy" / "docker-compose.yml"
    nginx = root / "deploy" / "nginx" / "nginx.conf"
    backup = root / "deploy" / "scripts" / "backup_db.sh"
    spec = root / "deploy" / "pyinstaller" / "YouPhoto.spec"
    ci_yml = root / ".github" / "workflows" / "ci.yml"
    changelog = root / "CHANGELOG.md"
    readme = root / "README.md"
    deploy_doc = root / "docs" / "DEPLOYMENT.md"

    for required in (dockerfile, compose, nginx, backup, spec, ci_yml, changelog, readme):
        assert required.exists(), f"Missing required deliverable: {required}"

    dockerfile_txt = dockerfile.read_text(encoding="utf-8")
    assert re.search(r"python:3\.1[123]", dockerfile_txt, re.IGNORECASE)
    assert "gunicorn" in dockerfile_txt.lower()

    compose_txt = compose.read_text(encoding="utf-8").lower()
    assert "postgres" in compose_txt
    assert "minio" in compose_txt
    assert "nginx" in compose_txt
    assert "purge_expired_photos" in compose_txt

    nginx_txt = nginx.read_text(encoding="utf-8")
    assert re.search(r"client_max_body_size\s+55M\b", nginx_txt, re.IGNORECASE)
    assert "ssl_certificate" in nginx_txt or "443" in nginx_txt

    assert "pg_dump" in backup.read_text(encoding="utf-8")

    spec_txt = spec.read_text(encoding="utf-8")
    assert "main.py" in spec_txt
    assert "YouPhoto" in spec_txt

    ci_txt = ci_yml.read_text(encoding="utf-8")
    assert "ruff" in ci_txt
    assert "makemigrations --check" in ci_txt
    assert "pytest" in ci_txt
    assert "develop" in ci_txt or "main" in ci_txt

    docs_combined = readme.read_text(encoding="utf-8")
    if deploy_doc.exists():
        docs_combined += "\n" + deploy_doc.read_text(encoding="utf-8")
    assert "docker" in docs_combined.lower()
    assert "YouPhoto.exe" in docs_combined or "pyinstaller" in docs_combined.lower()
    assert len(changelog.read_text(encoding="utf-8").strip()) > 0

    call_command("makemigrations", check=True, dry_run=True)
    call_command("migrate", check=True)
    call_command("seed_initial_data")
    purged_result = call_command("purge_expired_photos")
    assert purged_result is None or isinstance(purged_result, (int, str))
