"""Tier 3 E2E tests: pairwise cross-feature combinations (Features 1..11)."""

from __future__ import annotations

import inspect
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


def _bind_telegram_via_webhook(api_client, user, chat_id: str) -> str:
    from apps.notifications.services import TelegramBotBinder

    binder = TelegramBotBinder()
    sig = inspect.signature(binder.generate_binding_link)
    first_param = next(iter(sig.parameters))
    arg = user.id if first_param == "user_id" else user
    link = binder.generate_binding_link(arg)
    assert "start=" in link
    token = link.split("start=")[-1].strip()
    assert token

    resp = api_client.post(
        "/api/v1/notifications/telegram/webhook/",
        data={"token": token, "chat_id": chat_id},
        format="json",
    )
    if resp.status_code >= 400:
        resp = api_client.post(
            "/api/v1/notifications/telegram/webhook/",
            data={
                "message": {
                    "chat": {"id": chat_id},
                    "text": f"/start {token}",
                }
            },
            format="json",
        )
    assert resp.status_code == 200
    user.refresh_from_db()
    assert str(user.telegram_chat_id) == str(chat_id)
    return token


class _PassiveMvpView:
    """Minimal passive view spy for MVP presenter testing without blocking dialogs."""

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


def _create_online_order(client, pickup_salon, items: list[dict]) -> dict:
    resp = client.post(
        "/api/v1/orders/",
        data={"pickup_salon_id": str(pickup_salon.id), "items": items},
        format="json",
    )
    assert resp.status_code == 201, resp.content
    return resp.json()


def _transition_status_via_api(
    client, order_id, status: OrderStatus | str, comment: str = ""
):
    return client.post(
        f"/api/v1/orders/{order_id}/status/",
        data={"status": str(status), "comment": comment},
        format="json",
    )


@pytest.mark.django_db(transaction=True)
@pytest.mark.integration
def test_t3_01_web_register_next_redirect_and_telegram_binding(
    e2e_env, e2e_seed_data, api_client_factory, resolve_web_url
):
    """F3 + F6: Anonymous web visit -> ?next=/orders/create/ -> registration -> Telegram bind."""
    from apps.core.models import User
    from django.test import Client

    web_client = Client()
    create_url = resolve_web_url("order_create")
    anon_resp = web_client.get(create_url, follow=False)
    assert anon_resp.status_code in (301, 302)
    assert f"next={create_url}" in anon_resp.url or "login" in anon_resp.url

    register_url = f"{resolve_web_url('register')}?next={create_url}"
    email = "t3_reg_next@youphoto.test"
    phone = "+79991112233"
    reg_resp = web_client.post(
        register_url,
        data={
            "name": "Анна Тестова",
            "phone": phone,
            "email": email,
            "password": "StrongPass123!",
            "password_confirm": "StrongPass123!",
            "pdn_consent": "on",
        },
        follow=False,
    )
    assert reg_resp.status_code in (301, 302)
    assert create_url in reg_resp.url

    order_page = web_client.get(create_url)
    assert order_page.status_code == 200
    assert "JPEG, PNG, HEIC, TIFF до 50 МБ" in order_page.content.decode("utf-8")

    user = User.objects.get(email=email)
    assert user.role == UserRole.CLIENT
    assert user.pdn_consent_at is not None

    profile_resp = web_client.get(resolve_web_url("profile"))
    assert profile_resp.status_code == 200

    anon_api = api_client_factory()
    _bind_telegram_via_webhook(anon_api, user, "99887766")


@pytest.mark.django_db(transaction=True)
@pytest.mark.integration
def test_t3_02_upload_pricing_online_order_and_auto_routing(
    e2e_env, e2e_image_factory, e2e_seed_data, api_client_factory, upload_photo_via_api
):
    """F2 + F4 + F5: Upload JPEG+TIFF -> calculate price <=3s -> order routes to salon B."""
    from apps.core.models import Order

    client1 = _seed(e2e_seed_data, "client_1")
    salon_a = _seed(e2e_seed_data, "salon_a")
    salon_b = _seed(e2e_seed_data, "salon_b")
    svc_std = _seed(e2e_seed_data, "service_standard")
    svc_wide = _seed(e2e_seed_data, "service_wide")

    api = api_client_factory(client1.email, _password(e2e_seed_data, "client_1"))

    jpeg_resp = upload_photo_via_api(
        api, e2e_image_factory(fmt="JPEG"), "photo1.jpg", "image/jpeg"
    )
    tiff_resp = upload_photo_via_api(
        api, e2e_image_factory(fmt="TIFF"), "photo2.tiff", "image/tiff"
    )
    assert jpeg_resp.status_code in (200, 201)
    assert tiff_resp.status_code in (200, 201)
    jpeg_data = jpeg_resp.json()
    tiff_data = tiff_resp.json()
    assert jpeg_data["file_format"] == "JPEG"
    assert tiff_data["file_format"] == "TIFF"

    calc_items = [
        _item_payload(svc_std, jpeg_data["id"], 10, 15, quantity=3),
        _item_payload(svc_wide, tiff_data["id"], 40, 60, quantity=1),
    ]
    t0 = time.monotonic()
    calc_resp = api.post(
        "/api/v1/orders/calculate-price/",
        data={"items": calc_items},
        format="json",
    )
    elapsed = time.monotonic() - t0
    assert calc_resp.status_code == 200
    assert elapsed <= 3.0
    calc_json = calc_resp.json()
    assert Decimal(str(calc_json["total_price"])) > Decimal(0)
    assert len(calc_json["items"]) == 2

    order_json = _create_online_order(api, salon_a, calc_items)
    order = Order.objects.get(id=order_json["id"])
    assert order.status == OrderStatus.ASSIGNED_TO_SALON
    assert order.pickup_salon_id == salon_a.id
    assert order.assigned_salon_id == salon_b.id
    assert len(order.pickup_code) == 6
    assert order.pickup_code.isdigit()
    assert order.items.count() == 2


@pytest.mark.django_db(transaction=True)
@pytest.mark.integration
def test_t3_03_auto_routing_load_tiebreak_and_saturation(
    e2e_env, e2e_image_factory, e2e_seed_data, api_client_factory, upload_photo_via_api
):
    """F1 + F4: (load * 100) + delivery_cost prefers local salon first, then less-loaded salon."""
    from apps.core.models import Order

    client1 = _seed(e2e_seed_data, "client_1")
    salon_a = _seed(e2e_seed_data, "salon_a")
    salon_b = _seed(e2e_seed_data, "salon_b")
    svc_std = _seed(e2e_seed_data, "service_standard")

    api = api_client_factory(client1.email, _password(e2e_seed_data, "client_1"))
    photo_id = upload_photo_via_api(
        api, e2e_image_factory(fmt="JPEG"), "tiebreak.jpg", "image/jpeg"
    ).json()["id"]

    # With 0 load on both salons, local salon_a has delivery_cost=0 < salon_b.delivery_cost.
    first_order = _create_online_order(
        api, salon_a, [_item_payload(svc_std, photo_id, 10, 15, quantity=1)]
    )
    assert str(Order.objects.get(id=first_order["id"]).assigned_salon_id) == str(salon_a.id)

    # Saturate salon_a so (load_a * 100) > salon_b.delivery_cost.
    needed_load = max(2, int(float(salon_b.delivery_cost) // 100) + 2)
    for idx in range(needed_load):
        Order.objects.create(
            order_number=f"YP-SAT-{idx:04d}",
            pickup_code=f"10000{idx}",
            order_type=OrderType.ONLINE,
            status=OrderStatus.IN_PROGRESS,
            total_price=Decimal("100.00"),
            client=client1,
            pickup_salon=salon_a,
            assigned_salon=salon_a,
        )

    overflow_order = _create_online_order(
        api, salon_a, [_item_payload(svc_std, photo_id, 10, 15, quantity=1)]
    )
    assert str(Order.objects.get(id=overflow_order["id"]).assigned_salon_id) == str(salon_b.id)


@pytest.mark.django_db(transaction=True)
@pytest.mark.integration
def test_t3_04_status_change_telegram_notification_and_polling(
    e2e_env,
    e2e_image_factory,
    e2e_seed_data,
    api_client_factory,
    upload_photo_via_api,
    mock_apprise_send,
    resolve_web_url,
):
    """F3 + F4 + F7: Status updates send Telegram notification & update /api/v1/orders/<id>/."""
    from django.test import Client

    client1 = _seed(e2e_seed_data, "client_1")
    manager_a = _seed(e2e_seed_data, "manager_a")
    salon_a = _seed(e2e_seed_data, "salon_a")
    svc_std = _seed(e2e_seed_data, "service_standard")

    client1.telegram_chat_id = "11223344"
    client1.save(update_fields=["telegram_chat_id"])

    c_api = api_client_factory(client1.email, _password(e2e_seed_data, "client_1"))
    m_api = api_client_factory(manager_a.email, _password(e2e_seed_data, "manager_a"))

    photo_id = upload_photo_via_api(
        c_api, e2e_image_factory(fmt="JPEG"), "poll.jpg", "image/jpeg"
    ).json()["id"]
    order_data = _create_online_order(
        c_api, salon_a, [_item_payload(svc_std, photo_id, 10, 15, quantity=1)]
    )
    order_id = order_data["id"]

    for next_status in (
        OrderStatus.IN_PROGRESS,
        OrderStatus.PRINTING,
        OrderStatus.READY_FOR_PICKUP,
    ):
        resp = _transition_status_via_api(m_api, order_id, next_status)
        assert resp.status_code == 200

    assert len(mock_apprise_send.calls) >= 1
    poll_resp = c_api.get(f"/api/v1/orders/{order_id}/")
    assert poll_resp.status_code == 200
    poll_json = poll_resp.json()
    assert poll_json["status"] == OrderStatus.READY_FOR_PICKUP
    assert poll_json["status_label"] == ORDER_STATUS_LABELS_RU[OrderStatus.READY_FOR_PICKUP]
    assert len(poll_json["history"]) >= 4

    web_client = Client()
    web_client.force_login(client1)
    detail_resp = web_client.get(_call_resolve_detail(resolve_web_url, order_id))
    assert detail_resp.status_code == 200
    html = detail_resp.content.decode("utf-8")
    assert ORDER_STATUS_LABELS_RU[OrderStatus.READY_FOR_PICKUP] in html
    assert "status_poller.js" in html


@pytest.mark.django_db(transaction=True)
@pytest.mark.integration
def test_t3_05_telegram_api_outage_preserves_status_transition(
    e2e_env,
    e2e_image_factory,
    e2e_seed_data,
    api_client_factory,
    upload_photo_via_api,
    mock_apprise_send,
):
    """F3 + F4: Apprise / Telegram outage logs failure without rolling back DB transition."""
    from apps.core.models import Order, OrderStatusHistory

    client1 = _seed(e2e_seed_data, "client_1")
    manager_a = _seed(e2e_seed_data, "manager_a")
    salon_a = _seed(e2e_seed_data, "salon_a")
    svc_std = _seed(e2e_seed_data, "service_standard")

    client1.telegram_chat_id = "555666777"
    client1.save(update_fields=["telegram_chat_id"])

    c_api = api_client_factory(client1.email, _password(e2e_seed_data, "client_1"))
    m_api = api_client_factory(manager_a.email, _password(e2e_seed_data, "manager_a"))

    photo_id = upload_photo_via_api(
        c_api, e2e_image_factory(fmt="JPEG"), "outage.jpg", "image/jpeg"
    ).json()["id"]
    order_id = _create_online_order(
        c_api, salon_a, [_item_payload(svc_std, photo_id, 10, 15, quantity=1)]
    )["id"]

    mock_apprise_send.set_exception(RuntimeError("Telegram Bot API unreachable"))

    resp = _transition_status_via_api(m_api, order_id, OrderStatus.IN_PROGRESS)
    assert resp.status_code == 200

    order = Order.objects.get(id=order_id)
    assert order.status == OrderStatus.IN_PROGRESS
    assert OrderStatusHistory.objects.filter(
        order=order, new_status=OrderStatus.IN_PROGRESS
    ).exists()


@pytest.mark.django_db(transaction=True)
@pytest.mark.integration
def test_t3_06_equipment_breakdown_reroutes_pending_order(
    e2e_env, e2e_image_factory, e2e_seed_data, api_client_factory, upload_photo_via_api
):
    """F4 + F5: IN_PROGRESS -> PENDING_ROUTING on equipment breakdown auto-reroutes to salon B."""
    from apps.core.models import Order, OrderStatusHistory

    client1 = _seed(e2e_seed_data, "client_1")
    manager_a = _seed(e2e_seed_data, "manager_a")
    salon_a = _seed(e2e_seed_data, "salon_a")
    salon_b = _seed(e2e_seed_data, "salon_b")
    eq_a_std = _seed(e2e_seed_data, "eq_a_std")
    svc_std = _seed(e2e_seed_data, "service_standard")

    c_api = api_client_factory(client1.email, _password(e2e_seed_data, "client_1"))
    m_api = api_client_factory(manager_a.email, _password(e2e_seed_data, "manager_a"))

    photo_id = upload_photo_via_api(
        c_api, e2e_image_factory(fmt="JPEG"), "breakdown.jpg", "image/jpeg"
    ).json()["id"]
    order_id = _create_online_order(
        c_api, salon_a, [_item_payload(svc_std, photo_id, 10, 15, quantity=1)]
    )["id"]

    assert _transition_status_via_api(m_api, order_id, OrderStatus.IN_PROGRESS).status_code == 200

    eq_a_std.is_operational = False
    eq_a_std.save(update_fields=["is_operational"])

    reroute_resp = _transition_status_via_api(
        m_api, order_id, OrderStatus.PENDING_ROUTING, comment="Поломка принтера в салоне"
    )
    assert reroute_resp.status_code == 200

    order = Order.objects.get(id=order_id)
    assert order.status == OrderStatus.ASSIGNED_TO_SALON
    assert order.assigned_salon_id == salon_b.id
    assert OrderStatusHistory.objects.filter(
        order=order, new_status=OrderStatus.PENDING_ROUTING
    ).exists()


@pytest.mark.django_db(transaction=True)
@pytest.mark.integration
def test_t3_07_clarification_request_telegram_and_replacement_upload(
    e2e_env,
    e2e_image_factory,
    e2e_seed_data,
    api_client_factory,
    upload_photo_via_api,
    mock_apprise_send,
):
    """F2 + F3 + F4 + F7: NEEDS_CLARIFICATION -> Telegram alert -> client uploads new photo."""
    from apps.core.models import Order

    client1 = _seed(e2e_seed_data, "client_1")
    manager_a = _seed(e2e_seed_data, "manager_a")
    salon_a = _seed(e2e_seed_data, "salon_a")
    svc_std = _seed(e2e_seed_data, "service_standard")

    client1.telegram_chat_id = "777888999"
    client1.save(update_fields=["telegram_chat_id"])

    c_api = api_client_factory(client1.email, _password(e2e_seed_data, "client_1"))
    m_api = api_client_factory(manager_a.email, _password(e2e_seed_data, "manager_a"))

    orig_photo = upload_photo_via_api(
        c_api, e2e_image_factory(fmt="JPEG", size=(300, 300)), "blur.jpg", "image/jpeg"
    ).json()
    order_id = _create_online_order(
        c_api, salon_a, [_item_payload(svc_std, orig_photo["id"], 10, 15, quantity=1)]
    )["id"]
    assert _transition_status_via_api(m_api, order_id, OrderStatus.IN_PROGRESS).status_code == 200

    clarify_comment = "Низкое разрешение фото, загрузите оригинал"
    clarify_resp = _transition_status_via_api(
        m_api, order_id, OrderStatus.NEEDS_CLARIFICATION, comment=clarify_comment
    )
    assert clarify_resp.status_code == 200
    assert len(mock_apprise_send.calls) >= 1

    order = Order.objects.get(id=order_id)
    assert order.status == OrderStatus.NEEDS_CLARIFICATION
    assert clarify_comment in order.clarification_comment

    new_photo = upload_photo_via_api(
        c_api, e2e_image_factory(fmt="PNG", size=(2400, 1800)), "orig.png", "image/png"
    ).json()
    resp = c_api.post(
        f"/api/v1/orders/{order_id}/clarification-response/",
        data={"photo_id": new_photo["id"], "comment": "Загружен оригинал"},
        format="json",
    )
    assert resp.status_code == 200
    order.refresh_from_db()
    assert order.status == OrderStatus.IN_PROGRESS


@pytest.mark.django_db(transaction=True)
@pytest.mark.integration
def test_t3_08_cross_salon_transit_receiving_and_pickup_completion(
    e2e_env, e2e_image_factory, e2e_seed_data, api_client_factory, upload_photo_via_api
):
    """F4 + F5 + F9: Salon B prints -> IN_TRANSIT -> Salon A receives -> pickup_code completes."""
    from apps.core.models import Order

    client1 = _seed(e2e_seed_data, "client_1")
    manager_a = _seed(e2e_seed_data, "manager_a")
    manager_b = _seed(e2e_seed_data, "manager_b")
    salon_a = _seed(e2e_seed_data, "salon_a")
    salon_b = _seed(e2e_seed_data, "salon_b")
    svc_wide = _seed(e2e_seed_data, "service_wide")

    c_api = api_client_factory(client1.email, _password(e2e_seed_data, "client_1"))
    ma_api = api_client_factory(manager_a.email, _password(e2e_seed_data, "manager_a"))
    mb_api = api_client_factory(manager_b.email, _password(e2e_seed_data, "manager_b"))

    photo_id = upload_photo_via_api(
        c_api, e2e_image_factory(fmt="JPEG"), "wide.jpg", "image/jpeg"
    ).json()["id"]
    order_json = _create_online_order(
        c_api, salon_a, [_item_payload(svc_wide, photo_id, 40, 60, quantity=1)]
    )
    order_id = order_json["id"]
    pickup_code = order_json["pickup_code"]

    order = Order.objects.get(id=order_id)
    assert order.assigned_salon_id == salon_b.id
    assert order.pickup_salon_id == salon_a.id

    assert _transition_status_via_api(mb_api, order_id, OrderStatus.IN_PROGRESS).status_code == 200
    assert _transition_status_via_api(mb_api, order_id, OrderStatus.PRINTING).status_code == 200
    ready_resp = _transition_status_via_api(mb_api, order_id, OrderStatus.READY_FOR_PICKUP)
    if ready_resp.status_code >= 400:
        ready_resp = _transition_status_via_api(mb_api, order_id, OrderStatus.IN_TRANSIT)
    assert ready_resp.status_code == 200
    order.refresh_from_db()
    assert order.status == OrderStatus.IN_TRANSIT

    assert (
        _transition_status_via_api(ma_api, order_id, OrderStatus.READY_FOR_PICKUP).status_code
        == 200
    )

    bad_pickup = ma_api.post(
        f"/api/v1/orders/{order_id}/complete-pickup/",
        data={"pickup_code": "000000" if pickup_code != "000000" else "999999"},
        format="json",
    )
    assert bad_pickup.status_code == 400
    order.refresh_from_db()
    assert order.status == OrderStatus.READY_FOR_PICKUP

    ok_pickup = ma_api.post(
        f"/api/v1/orders/{order_id}/complete-pickup/",
        data={"pickup_code": pickup_code},
        format="json",
    )
    assert ok_pickup.status_code == 200
    order.refresh_from_db()
    assert order.status == OrderStatus.COMPLETED
    assert order.completed_at is not None
    for photo in order.photos.all():
        assert photo.delete_after is not None
        assert photo.delete_after >= order.completed_at + timedelta(days=29)


@pytest.mark.django_db(transaction=True)
@pytest.mark.desktop
def test_t3_09_desktop_rest_client_queue_presenter_and_photo_download(
    e2e_env,
    e2e_image_factory,
    e2e_seed_data,
    api_client_factory,
    upload_photo_via_api,
    live_server,
    tmp_path,
):
    """F2 + F5 + F9: Desktop RestApiClient + OrderQueuePresenter fetches queue & downloads photo."""
    from apps.core.models import Order
    from desktop.api_client.rest_client import RestApiClient
    from desktop.hardware.printer_service import WindowsPrinterService
    from desktop.presenters.order_queue_presenter import OrderQueuePresenter

    client1 = _seed(e2e_seed_data, "client_1")
    manager_a = _seed(e2e_seed_data, "manager_a")
    salon_a = _seed(e2e_seed_data, "salon_a")
    svc_std = _seed(e2e_seed_data, "service_standard")

    raw_bytes = e2e_image_factory(fmt="JPEG", size=(640, 480))
    c_api = api_client_factory(client1.email, _password(e2e_seed_data, "client_1"))
    photo_id = upload_photo_via_api(c_api, raw_bytes, "desktop_dl.jpg", "image/jpeg").json()["id"]
    order_id = _create_online_order(
        c_api, salon_a, [_item_payload(svc_std, photo_id, 10, 15, quantity=1)]
    )["id"]

    rest_client = RestApiClient(base_url=f"{live_server.url}/api/v1")
    profile = rest_client.login(manager_a.email, _password(e2e_seed_data, "manager_a"))
    user_role = profile.get("user", {}).get("role") or profile.get("role")
    assert user_role == UserRole.MANAGER or rest_client.role == UserRole.MANAGER

    printer_service = WindowsPrinterService()
    presenter = _construct_with_matching_kwargs(
        OrderQueuePresenter,
        api_client=rest_client,
        rest_client=rest_client,
        client=rest_client,
        printer_service=printer_service,
        view=_PassiveMvpView(),
    )

    queue = presenter.refresh_queue()
    assert any(str(item["id"]) == str(order_id) for item in queue)

    dl_dir = tmp_path / "downloaded"
    dl_dir.mkdir(parents=True, exist_ok=True)
    downloaded_paths = presenter.download_order_photos(order_id, str(dl_dir))
    assert len(downloaded_paths) == 1
    assert Path(downloaded_paths[0]).read_bytes() == raw_bytes

    presenter.change_order_status(order_id, OrderStatus.IN_PROGRESS)
    assert Order.objects.get(id=order_id).status == OrderStatus.IN_PROGRESS


@pytest.mark.django_db(transaction=True)
@pytest.mark.desktop
def test_t3_10_desktop_document_presenter_camera_cropper_printer(
    e2e_env,
    e2e_image_factory,
    e2e_seed_data,
    live_server,
    tmp_path,
):
    """F8 + F9: DocumentPhotoPresenter + UsbCameraService + DocumentCropper + WindowsPrinter."""
    from apps.core.models import Order
    from desktop.api_client.rest_client import RestApiClient
    from desktop.hardware.camera_service import DocumentCropper, UsbCameraService
    from desktop.hardware.printer_service import WindowsPrinterService
    from desktop.presenters.document_photo_presenter import DocumentPhotoPresenter
    from PIL import Image

    manager_a = _seed(e2e_seed_data, "manager_a")

    cam_dir = tmp_path / "camera_dcim"
    cam_dir.mkdir(parents=True, exist_ok=True)
    raw_portrait = cam_dir / "IMG_0001.jpg"
    raw_portrait.write_bytes(e2e_image_factory(fmt="JPEG", size=(1600, 1200)))

    camera = _construct_with_matching_kwargs(
        UsbCameraService,
        mock_mode=True,
        mock_connected=True,
        mock_image_path=str(raw_portrait),
        import_dir=str(cam_dir),
        mock_dir=str(cam_dir),
        source_dir=cam_dir,
        cropper=DocumentCropper(),
    )
    if hasattr(camera, "set_mock_photo"):
        camera.set_mock_photo(str(raw_portrait))
    elif hasattr(camera, "mock_photo_path"):
        camera.mock_photo_path = str(raw_portrait)

    cropper = DocumentCropper()
    cropped_path = Path(
        cropper.crop_and_resize(str(raw_portrait), DocumentStandard.RF_PASSPORT_35x45)
    )
    assert cropped_path.exists()
    with Image.open(cropped_path) as img:
        assert img.size == (413, 531)

    rest_client = RestApiClient(base_url=f"{live_server.url}/api/v1")
    rest_client.login(manager_a.email, _password(e2e_seed_data, "manager_a"))
    printer = WindowsPrinterService()
    printers = printer.list_available_printers()
    assert len(printers) >= 1

    presenter = _construct_with_matching_kwargs(
        DocumentPhotoPresenter,
        api_client=rest_client,
        rest_client=rest_client,
        client=rest_client,
        camera_service=camera,
        camera=camera,
        cropper=cropper,
        printer_service=printer,
        printer=printer,
        view=_PassiveMvpView(),
    )
    if hasattr(presenter, "current_standard"):
        presenter.current_standard = DocumentStandard.RF_PASSPORT_35x45
    elif hasattr(presenter, "select_standard"):
        presenter.select_standard(DocumentStandard.RF_PASSPORT_35x45)

    imported = presenter.import_from_camera()
    assert Path(imported.local_file_path).exists()
    cropped_by_presenter = presenter.apply_auto_crop(DocumentStandard.RF_PASSPORT_35x45)
    with Image.open(cropped_by_presenter) as out_img:
        assert out_img.size == (413, 531)

    result = presenter.confirm_and_print(copies=2, printer_name=printers[0].name)
    if isinstance(result, tuple):
        print_res = result[-1]
        assert print_res.is_success is True
    elif hasattr(result, "is_success"):
        assert result.is_success is True
    elif isinstance(result, dict):
        assert result.get("order_type") == OrderType.IN_SALON or result.get("id")
    assert Order.objects.filter(order_type=OrderType.IN_SALON).exists()


@pytest.mark.django_db(transaction=True)
@pytest.mark.integration
def test_t3_11_in_salon_document_order_no_telegram_error_and_pickup(
    e2e_env,
    e2e_image_factory,
    e2e_seed_data,
    api_client_factory,
    upload_photo_via_api,
    mock_apprise_send,
):
    """F3 + F4 + F8 + F9: IN_SALON order (client=None, PRINTING) succeeds without Telegram error."""
    from apps.core.models import Order

    manager_a = _seed(e2e_seed_data, "manager_a")
    salon_a = _seed(e2e_seed_data, "salon_a")

    m_api = api_client_factory(manager_a.email, _password(e2e_seed_data, "manager_a"))
    photo_json = upload_photo_via_api(
        m_api, e2e_image_factory(fmt="JPEG", size=(413, 531)), "doc.jpg", "image/jpeg"
    ).json()

    doc_resp = m_api.post(
        "/api/v1/orders/document-photo/",
        data={
            "salon_id": str(salon_a.id),
            "standard": DocumentStandard.RF_PASSPORT_35x45,
            "copies": 4,
            "photo_id": photo_json["id"],
            "file_key": photo_json["file_key"],
        },
        format="json",
    )
    assert doc_resp.status_code == 201, doc_resp.content
    order = Order.objects.get(id=doc_resp.json()["id"])
    assert order.order_type == OrderType.IN_SALON
    assert order.client is None
    assert order.status == OrderStatus.PRINTING
    assert order.pickup_salon_id == salon_a.id
    assert order.assigned_salon_id == salon_a.id

    assert (
        _transition_status_via_api(m_api, str(order.id), OrderStatus.READY_FOR_PICKUP).status_code
        == 200
    )
    assert len(mock_apprise_send.calls) == 0

    pickup_resp = m_api.post(
        f"/api/v1/orders/{order.id}/complete-pickup/",
        data={"pickup_code": order.pickup_code},
        format="json",
    )
    assert pickup_resp.status_code == 200
    order.refresh_from_db()
    assert order.status == OrderStatus.COMPLETED


@pytest.mark.django_db(transaction=True)
@pytest.mark.security
def test_t3_12_admin_disable_equipment_force_reroute_and_rbac(
    e2e_env, e2e_image_factory, e2e_seed_data, api_client_factory, upload_photo_via_api
):
    """F4 + F5 + F9: Disable equipment with active orders -> 409 without force_reroute; RBAC 403."""
    from apps.core.models import Order

    admin = _seed(e2e_seed_data, "admin")
    client1 = _seed(e2e_seed_data, "client_1")
    manager_b = _seed(e2e_seed_data, "manager_b")
    salon_a = _seed(e2e_seed_data, "salon_a")
    salon_b = _seed(e2e_seed_data, "salon_b")
    eq_a_std = _seed(e2e_seed_data, "eq_a_std")
    svc_std = _seed(e2e_seed_data, "service_standard")

    c_api = api_client_factory(client1.email, _password(e2e_seed_data, "client_1"))
    mb_api = api_client_factory(manager_b.email, _password(e2e_seed_data, "manager_b"))
    ad_api = api_client_factory(admin.email, _password(e2e_seed_data, "admin"))

    photo_id = upload_photo_via_api(
        c_api, e2e_image_factory(fmt="JPEG"), "equip.jpg", "image/jpeg"
    ).json()["id"]
    order_id = _create_online_order(
        c_api, salon_a, [_item_payload(svc_std, photo_id, 10, 15, quantity=1)]
    )["id"]
    assert str(Order.objects.get(id=order_id).assigned_salon_id) == str(salon_a.id)

    disable_payload = {
        "equipment_id": str(eq_a_std.id),
        "is_operational": False,
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
    }

    assert mb_api.patch(
        f"/api/v1/salons/{salon_a.id}/catalog/", data=disable_payload, format="json"
    ).status_code == 403
    assert c_api.patch(
        f"/api/v1/salons/{salon_a.id}/catalog/", data=disable_payload, format="json"
    ).status_code == 403

    conflict_resp = ad_api.patch(
        f"/api/v1/salons/{salon_a.id}/catalog/", data=disable_payload, format="json"
    )
    assert conflict_resp.status_code == 409

    force_payload = {**disable_payload, "force_reroute": True}
    ok_resp = ad_api.patch(
        f"/api/v1/salons/{salon_a.id}/catalog/", data=force_payload, format="json"
    )
    assert ok_resp.status_code == 200

    order = Order.objects.get(id=order_id)
    assert order.assigned_salon_id == salon_b.id
    assert order.status == OrderStatus.ASSIGNED_TO_SALON


@pytest.mark.django_db(transaction=True)
@pytest.mark.integration
def test_t3_13_completed_order_30d_purge_preserves_history(
    e2e_env, e2e_image_factory, e2e_seed_data, api_client_factory, upload_photo_via_api
):
    """F1 + F2 + F4: Completed order -> +30d -> purge_expired_photos deletes blob, keeps history."""
    from apps.core.models import Order, OrderItem, OrderStatusHistory, PhotoFile
    from django.conf import settings
    from django.core.management import call_command
    from django.utils import timezone

    client1 = _seed(e2e_seed_data, "client_1")
    manager_a = _seed(e2e_seed_data, "manager_a")
    salon_a = _seed(e2e_seed_data, "salon_a")
    svc_std = _seed(e2e_seed_data, "service_standard")

    c_api = api_client_factory(client1.email, _password(e2e_seed_data, "client_1"))
    m_api = api_client_factory(manager_a.email, _password(e2e_seed_data, "manager_a"))

    photo_json = upload_photo_via_api(
        c_api, e2e_image_factory(fmt="JPEG"), "purge30d.jpg", "image/jpeg"
    ).json()
    order_json = _create_online_order(
        c_api, salon_a, [_item_payload(svc_std, photo_json["id"], 10, 15, quantity=2)]
    )
    order_id = order_json["id"]

    assert _transition_status_via_api(m_api, order_id, OrderStatus.IN_PROGRESS).status_code == 200
    assert _transition_status_via_api(m_api, order_id, OrderStatus.PRINTING).status_code == 200
    assert (
        _transition_status_via_api(m_api, order_id, OrderStatus.READY_FOR_PICKUP).status_code
        == 200
    )
    assert (
        m_api.post(
            f"/api/v1/orders/{order_id}/complete-pickup/",
            data={"pickup_code": order_json["pickup_code"]},
            format="json",
        ).status_code
        == 200
    )

    photo = PhotoFile.objects.get(id=photo_json["id"])
    storage_path = Path(settings.MEDIA_ROOT) / "storage" / photo.file_key
    assert storage_path.exists()

    past_completed = timezone.now() - timedelta(days=31)
    Order.objects.filter(id=order_id).update(completed_at=past_completed)
    PhotoFile.objects.filter(id=photo.id).update(delete_after=timezone.now() - timedelta(hours=1))

    call_command("purge_expired_photos")

    photo.refresh_from_db()
    assert photo.is_deleted_from_storage is True
    assert not storage_path.exists()
    assert Order.objects.filter(id=order_id).exists()
    assert OrderItem.objects.filter(order_id=order_id).count() == 1
    assert OrderStatusHistory.objects.filter(order_id=order_id).count() >= 4


@pytest.mark.django_db(transaction=True)
@pytest.mark.security
def test_t3_14_web_registration_blocked_user_admin_toggle_and_auth_rejection(
    e2e_env, e2e_seed_data, api_client_factory, resolve_web_url
):
    """F5 + F6 + F10: Web registration hashes password; Admin blocks user; Web+API reject login."""
    from apps.core.models import User
    from django.test import Client

    admin = _seed(e2e_seed_data, "admin")
    ad_api = api_client_factory(admin.email, _password(e2e_seed_data, "admin"))

    web_client = Client()
    email = "blocked_candidate@youphoto.test"
    phone = "+79995554433"
    pwd = "SafePassword123!"
    reg_resp = web_client.post(
        resolve_web_url("register"),
        data={
            "name": "Борис Тестов",
            "phone": phone,
            "email": email,
            "password": pwd,
            "password_confirm": pwd,
            "pdn_consent": "on",
        },
        follow=False,
    )
    assert reg_resp.status_code in (301, 302)

    user = User.objects.get(email=email)
    assert user.password != pwd
    assert "$" in user.password

    anon_api = api_client_factory()
    ok_api_login = anon_api.post(
        "/api/v1/auth/login/",
        data={"login": phone, "password": pwd},
        format="json",
    )
    assert ok_api_login.status_code == 200

    # Verify non-admin cannot toggle is_blocked, then Admin blocks user.
    user_api = api_client_factory(email, pwd)
    assert (
        user_api.patch(
            f"/api/v1/users/{user.id}/", data={"is_blocked": True}, format="json"
        ).status_code
        == 403
    )
    block_resp = ad_api.patch(
        f"/api/v1/users/{user.id}/", data={"is_blocked": True}, format="json"
    )
    assert block_resp.status_code == 200
    user.refresh_from_db()
    assert user.is_blocked is True

    # Blocked user cannot log in via API or Web.
    blocked_api_login = anon_api.post(
        "/api/v1/auth/login/",
        data={"login": email, "password": pwd},
        format="json",
    )
    assert blocked_api_login.status_code in (400, 401, 403)

    web_anon = Client()
    blocked_web_login = web_anon.post(
        resolve_web_url("login"),
        data={"login": email, "password": pwd, "username": email},
        follow=False,
    )
    assert blocked_web_login.status_code in (200, 400, 401, 403)
    assert "_auth_user_id" not in web_anon.session
