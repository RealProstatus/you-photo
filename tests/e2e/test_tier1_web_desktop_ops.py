"""Tier 1 E2E tests for Features 6-11: Web Portal, Hardware, Desktop MVP, Invariants, Ops/CI."""

from __future__ import annotations

import inspect
import re
import time
from pathlib import Path

import pytest
from apps.core.enums import (
    DOCUMENT_STANDARD_SPECS,
    ORDER_STATUS_LABELS_RU,
    DocumentStandard,
    OrderStatus,
    OrderType,
    UserRole,
)
from apps.core.exceptions import InvalidPickupCodeError, InvalidStatusTransitionError

PROJECT_ROOT = Path(__file__).resolve().parents[2]


def _seed_get(seed: object, key: str):
    return seed[key] if isinstance(seed, dict) else getattr(seed, key)


def _seed_password(seed: object, role_key: str = "client_1") -> str:
    passwords = _seed_get(seed, "passwords")
    if isinstance(passwords, dict):
        user = _seed_get(seed, role_key)
        return (
            passwords.get(user.email)
            or passwords.get(role_key)
            or passwords.get("default")
            or next(iter(passwords.values()))
        )
    return str(passwords)


def _extract_input_name(html: str, candidates: tuple[str, ...], default: str) -> str:
    for name in candidates:
        if re.search(rf'name=["\']{re.escape(name)}["\']', html):
            return name
    return default


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


def _build_with_signature(cls, view_obj=None, **available):
    sig = inspect.signature(cls.__init__)
    kwargs = {}
    for param_name, param in sig.parameters.items():
        if param_name == "self":
            continue
        if param_name in available:
            kwargs[param_name] = available[param_name]
        elif "view" in param_name and view_obj is not None:
            kwargs[param_name] = view_obj
        elif "api" in param_name and "api_client" in available:
            kwargs[param_name] = available["api_client"]
        elif "camera" in param_name and "camera_service" in available:
            kwargs[param_name] = available["camera_service"]
        elif "printer" in param_name and "printer_service" in available:
            kwargs[param_name] = available["printer_service"]
        elif "crop" in param_name and "cropper" in available:
            kwargs[param_name] = available["cropper"]
        elif param.default is inspect.Parameter.empty and view_obj is not None:
            kwargs[param_name] = view_obj
    return cls(**kwargs)


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


def _create_online_order_via_api(
    api_client_factory,
    upload_photo_via_api,
    e2e_image_factory,
    seed,
    client_key: str = "client_1",
    salon_key: str = "salon_a",
    service_key: str = "service_standard",
):
    user = _seed_get(seed, client_key)
    pwd = _seed_password(seed, client_key)
    salon = _seed_get(seed, salon_key)
    service = _seed_get(seed, service_key)
    client = api_client_factory(user.email, pwd)
    upload_resp = upload_photo_via_api(
        client, e2e_image_factory(fmt="JPEG"), "order_photo.jpg", "image/jpeg"
    )
    assert upload_resp.status_code in (200, 201), upload_resp.content
    photo_data = upload_resp.json()
    payload = {
        "pickup_salon_id": str(salon.id),
        "pickup_salon": str(salon.id),
        "items": [
            {
                "photo_id": photo_data["id"],
                "photo": photo_data["id"],
                "service_id": str(service.id),
                "service": str(service.id),
                "width_cm": 10.0,
                "height_cm": 15.0,
                "quantity": 2,
                "material": (service.allowed_materials or ["Глянцевая бумага"])[0],
                "options": [],
            }
        ],
    }
    resp = client.post("/api/v1/orders/", data=payload, format="json")
    assert resp.status_code in (200, 201), resp.content
    return resp.json(), photo_data


# =============================================================================
# Feature 6: Client Web App Auth & Profile (US-08, U-C-02) - 5 tests
# =============================================================================


@pytest.mark.django_db(transaction=True)
@pytest.mark.integration
def test_t1_f6_register_page_renders_russian_form_and_pdn_consent(e2e_env, resolve_web_url):
    from django.test import Client

    client = Client()
    resp = client.get(resolve_web_url("register"))
    assert resp.status_code == 200
    html = resp.content.decode("utf-8")
    assert "pdn_consent" in html
    assert "Зарегистрироваться" in html
    for field in ("name", "phone", "email", "password", "password_confirm"):
        assert field in html


@pytest.mark.django_db(transaction=True)
@pytest.mark.integration
def test_t1_f6_register_valid_creates_client_with_hashed_password_and_logs_in(
    e2e_env, resolve_web_url
):
    from django.contrib.auth import get_user_model
    from django.test import Client

    user_model = get_user_model()
    client = Client()
    raw_pwd = "StrongPass123!"
    resp = client.post(
        resolve_web_url("register"),
        data={
            "name": "Анна Смирнова",
            "phone": "+79990001122",
            "email": "anna.smirnova@example.com",
            "password": raw_pwd,
            "password_confirm": raw_pwd,
            "pdn_consent": "on",
        },
        follow=False,
    )
    assert resp.status_code in (200, 302)
    user = user_model.objects.get(email="anna.smirnova@example.com")
    assert user.role == UserRole.CLIENT
    assert user.password != raw_pwd
    assert user.check_password(raw_pwd)
    assert user.pdn_consent_at is not None
    assert "_auth_user_id" in client.session


@pytest.mark.django_db(transaction=True)
@pytest.mark.integration
def test_t1_f6_register_with_next_redirects_to_order_create(e2e_env, resolve_web_url):
    from django.test import Client

    client = Client()
    next_url = resolve_web_url("order_create")
    register_url = f"{resolve_web_url('register')}?next={next_url}"
    resp = client.post(
        register_url,
        data={
            "name": "Ольга Кузнецова",
            "phone": "+79990003344",
            "email": "olga.k@example.com",
            "password": "ValidPassword99",
            "password_confirm": "ValidPassword99",
            "pdn_consent": "on",
            "next": next_url,
        },
        follow=False,
    )
    assert resp.status_code == 302
    assert next_url in resp["Location"]


@pytest.mark.django_db(transaction=True)
@pytest.mark.integration
def test_t1_f6_web_login_succeeds_via_email_and_via_phone(
    e2e_env, e2e_seed_data, resolve_web_url
):
    from django.test import Client

    client_user = _seed_get(e2e_seed_data, "client_1")
    password = _seed_password(e2e_seed_data, "client_1")
    login_url = resolve_web_url("login")

    get_resp = Client().get(login_url)
    assert get_resp.status_code == 200
    field_name = _extract_input_name(
        get_resp.content.decode("utf-8"), ("login", "username", "email"), "login"
    )

    for login_value in (client_user.email, client_user.phone):
        web = Client()
        resp = web.post(login_url, data={field_name: login_value, "password": password})
        assert resp.status_code in (200, 302)
        assert "_auth_user_id" in web.session
        prof = web.get(resolve_web_url("profile"))
        assert prof.status_code == 200


@pytest.mark.django_db(transaction=True)
@pytest.mark.integration
def test_t1_f6_profile_renders_telegram_binding_link_and_supports_binding(
    e2e_env, e2e_seed_data, resolve_web_url, api_client_factory
):
    from django.test import Client

    client_user = _seed_get(e2e_seed_data, "client_1")
    web = Client()
    web.force_login(client_user)
    resp = web.get(resolve_web_url("profile"))
    assert resp.status_code == 200
    html = resp.content.decode("utf-8")
    match = re.search(r"https?://t\.me/[^\s\"'<>]+\?start=([A-Za-z0-9_-]+)", html)
    assert match is not None, f"Telegram binding link missing in profile HTML: {html[:500]}"
    token = match.group(1)

    anon_api = api_client_factory()
    wh_payload = {
        "token": token,
        "chat_id": "99887766",
        "message": {"text": f"/start {token}", "chat": {"id": 99887766}},
    }
    wh_resp = anon_api.post(
        "/api/v1/notifications/telegram/webhook/",
        data=wh_payload,
        format="json",
    )
    assert wh_resp.status_code in (200, 201), wh_resp.content
    client_user.refresh_from_db()
    assert str(client_user.telegram_chat_id) == "99887766"


# =============================================================================
# Feature 7: Client Web Order Creation & Tracking (US-01, US-04) - 6 tests
# =============================================================================


@pytest.mark.django_db(transaction=True)
@pytest.mark.integration
def test_t1_f7_order_create_shows_active_salons_format_notice_no_courier_no_pay(
    e2e_env, e2e_seed_data, resolve_web_url
):
    from django.test import Client

    client_user = _seed_get(e2e_seed_data, "client_1")
    salon_a = _seed_get(e2e_seed_data, "salon_a")
    salon_b = _seed_get(e2e_seed_data, "salon_b")
    salon_hidden = _seed_get(e2e_seed_data, "salon_hidden")

    web = Client()
    web.force_login(client_user)
    resp = web.get(resolve_web_url("order_create"))
    assert resp.status_code == 200
    html = resp.content.decode("utf-8")
    assert salon_a.name in html
    assert salon_b.name in html
    assert salon_hidden.name not in html
    assert "JPEG, PNG, HEIC, TIFF" in html
    assert "50 МБ" in html
    assert "доставка курьером" not in html.lower()
    assert "оплатить онлайн" not in html.lower()


@pytest.mark.django_db(transaction=True)
@pytest.mark.integration
def test_t1_f7_web_order_creation_flow_shows_order_number_pickup_code_and_status(
    e2e_env, e2e_seed_data, e2e_image_factory, resolve_web_url
):
    from apps.core.models import Order
    from django.core.files.uploadedfile import SimpleUploadedFile
    from django.test import Client

    client_user = _seed_get(e2e_seed_data, "client_1")
    salon_a = _seed_get(e2e_seed_data, "salon_a")
    service_std = _seed_get(e2e_seed_data, "service_standard")
    material = (service_std.allowed_materials or ["Глянцевая бумага"])[0]

    web = Client()
    web.force_login(client_user)
    create_url = resolve_web_url("order_create")
    get_resp = web.get(create_url)
    assert get_resp.status_code == 200
    html = get_resp.content.decode("utf-8")

    upload_url = _discover_web_path(("upload",), create_url)
    if upload_url != create_url:
        up_file = SimpleUploadedFile("web_pic.jpg", e2e_image_factory(), content_type="image/jpeg")
        web.post(upload_url, data={"file": up_file, "files": up_file, "photo": up_file})

    submit_file = SimpleUploadedFile("web_pic.jpg", e2e_image_factory(), content_type="image/jpeg")
    target_url = create_url
    for match in re.finditer(r'<form[^>]+action=["\']([^"\']+)["\']', html):
        candidate = match.group(1)
        if not any(skip in candidate.lower() for skip in ("logout", "upload", "search")):
            target_url = candidate
            break

    resp = web.post(
        target_url,
        data={
            "pickup_salon": str(salon_a.id),
            "pickup_salon_id": str(salon_a.id),
            "service": str(service_std.id),
            "service_id": str(service_std.id),
            "width_cm": "10",
            "height_cm": "15",
            "quantity": "1",
            "material": material,
            "file": submit_file,
            "action": "submit",
        },
        follow=True,
    )
    assert resp.status_code == 200
    order = Order.objects.filter(client=client_user).order_by("-created_at").first()
    if order is None:
        # Fallback if order_create UI posts via REST /api/v1/orders/ in browser JS
        pytest.fail("Web order creation did not persist an Order for client_1")
    detail_html = resp.content.decode("utf-8")
    if order.order_number not in detail_html:
        detail_resp = web.get(resolve_web_url("order_detail", order_id=order.id))
        assert detail_resp.status_code == 200
        detail_html = detail_resp.content.decode("utf-8")
    assert order.order_number in detail_html
    assert order.pickup_code in detail_html
    assert ORDER_STATUS_LABELS_RU[OrderStatus(order.status)] in detail_html


@pytest.mark.django_db(transaction=True)
@pytest.mark.integration
def test_t1_f7_order_list_shows_clients_orders(
    e2e_env, e2e_seed_data, api_client_factory, upload_photo_via_api, e2e_image_factory,
    resolve_web_url,
):
    from django.test import Client

    order_1, _ = _create_online_order_via_api(
        api_client_factory, upload_photo_via_api, e2e_image_factory, e2e_seed_data, "client_1"
    )
    order_2, _ = _create_online_order_via_api(
        api_client_factory, upload_photo_via_api, e2e_image_factory, e2e_seed_data, "client_2"
    )

    web = Client()
    web.force_login(_seed_get(e2e_seed_data, "client_1"))
    resp = web.get(resolve_web_url("order_list"))
    assert resp.status_code == 200
    html = resp.content.decode("utf-8")
    assert order_1["order_number"] in html
    assert ORDER_STATUS_LABELS_RU[OrderStatus(order_1["status"])] in html
    assert order_2["order_number"] not in html


@pytest.mark.django_db(transaction=True)
@pytest.mark.integration
def test_t1_f7_order_detail_shows_items_pickup_salon_code_and_history(
    e2e_env, e2e_seed_data, api_client_factory, upload_photo_via_api, e2e_image_factory,
    resolve_web_url,
):
    from django.test import Client

    order_data, _ = _create_online_order_via_api(
        api_client_factory, upload_photo_via_api, e2e_image_factory, e2e_seed_data, "client_1"
    )
    salon_a = _seed_get(e2e_seed_data, "salon_a")
    service_std = _seed_get(e2e_seed_data, "service_standard")

    web = Client()
    web.force_login(_seed_get(e2e_seed_data, "client_1"))
    resp = web.get(resolve_web_url("order_detail", order_id=order_data["id"]))
    assert resp.status_code == 200
    html = resp.content.decode("utf-8")
    assert order_data["order_number"] in html
    assert order_data["pickup_code"] in html
    assert salon_a.name in html
    assert service_std.name in html
    assert ORDER_STATUS_LABELS_RU[OrderStatus(order_data["status"])] in html


@pytest.mark.django_db(transaction=True)
@pytest.mark.integration
def test_t1_f7_status_poller_js_configures_5000ms_polling_and_toast(
    e2e_env, e2e_seed_data, api_client_factory, upload_photo_via_api, e2e_image_factory,
    resolve_web_url,
):
    from django.test import Client

    poller_path = PROJECT_ROOT / "backend" / "static" / "js" / "status_poller.js"
    assert poller_path.is_file()
    js_src = poller_path.read_text(encoding="utf-8")
    assert "5000" in js_src
    assert "/api/v1/orders/" in js_src
    assert "toast" in js_src.lower()

    order_data, _ = _create_online_order_via_api(
        api_client_factory, upload_photo_via_api, e2e_image_factory, e2e_seed_data, "client_1"
    )
    web = Client()
    web.force_login(_seed_get(e2e_seed_data, "client_1"))
    detail_resp = web.get(resolve_web_url("order_detail", order_id=order_data["id"]))
    detail_html = detail_resp.content.decode("utf-8")
    assert "status_poller.js" in detail_html


@pytest.mark.django_db(transaction=True)
@pytest.mark.integration
def test_t1_f7_bootstrap5_responsive_meta_and_grid_in_templates(e2e_env, resolve_web_url):
    from django.test import Client

    base_html = (PROJECT_ROOT / "backend" / "templates" / "base.html").read_text(encoding="utf-8")
    assert "viewport" in base_html
    assert "width=device-width" in base_html
    assert "bootstrap" in base_html.lower()

    css_path = PROJECT_ROOT / "backend" / "static" / "css" / "main.css"
    assert css_path.is_file()

    login_html = Client().get(resolve_web_url("login")).content.decode("utf-8")
    assert "viewport" in login_html
    assert any(cls in login_html for cls in ("container", "row", "col-"))


# =============================================================================
# Feature 8: Hardware Camera, Cropper & Printer (US-07, Module 6) - 5 tests
# =============================================================================


@pytest.mark.desktop
def test_t1_f8_usb_camera_service_mock_lists_devices_and_imports_photo(
    e2e_env, e2e_image_factory, tmp_path
):
    from desktop.hardware.camera_service import UsbCameraService
    from desktop.hardware.dto import ImportedPhoto

    cam_dir = tmp_path / "dcim"
    cam_dir.mkdir()
    sample = cam_dir / "IMG_0001.jpg"
    sample.write_bytes(e2e_image_factory(fmt="JPEG", size=(1024, 768)))

    cam = _build_with_signature(
        UsbCameraService,
        mock_mode=True,
        import_dir=cam_dir,
        mock_dir=cam_dir,
        source_dir=cam_dir,
        mock_image_path=sample,
        mock_connected=True,
    )
    assert cam.is_camera_connected() is True
    if hasattr(cam, "list_devices"):
        devices = cam.list_devices()
        assert len(devices) >= 1
    imported = cam.import_latest_photo()
    assert isinstance(imported, ImportedPhoto)
    assert Path(imported.local_file_path).is_file()
    assert imported.width_px > 0
    assert imported.height_px > 0
    assert imported.captured_at is not None


@pytest.mark.desktop
def test_t1_f8_document_cropper_crops_rf_passport_35x45_to_413x531_300dpi(
    e2e_env, e2e_image_factory, tmp_path
):
    from desktop.hardware.camera_service import DocumentCropper
    from PIL import Image

    src = tmp_path / "portrait_rf.jpg"
    src.write_bytes(e2e_image_factory(fmt="JPEG", size=(1200, 1600)))
    cropper = DocumentCropper()
    out_path = Path(cropper.crop_and_resize(str(src), DocumentStandard.RF_PASSPORT_35x45))
    assert out_path.is_file()
    with Image.open(out_path) as img:
        assert img.size == (413, 531)
        dpi = img.info.get("dpi")
        if dpi:
            assert round(dpi[0]) == 300 and round(dpi[1]) == 300


@pytest.mark.desktop
def test_t1_f8_document_cropper_crops_us_visa_50x50_to_591x591_300dpi(
    e2e_env, e2e_image_factory, tmp_path
):
    from desktop.hardware.camera_service import DocumentCropper
    from PIL import Image

    src = tmp_path / "portrait_us.jpg"
    src.write_bytes(e2e_image_factory(fmt="JPEG", size=(1200, 1200)))
    cropper = DocumentCropper()
    out_path = Path(cropper.crop_and_resize(str(src), DocumentStandard.US_VISA_50x50))
    assert out_path.is_file()
    with Image.open(out_path) as img:
        assert img.size == (591, 591)
        dpi = img.info.get("dpi")
        if dpi:
            assert round(dpi[0]) == 300 and round(dpi[1]) == 300


@pytest.mark.desktop
def test_t1_f8_document_cropper_manual_crop_box_and_export_reimport(
    e2e_env, tmp_path
):
    from desktop.hardware.camera_service import DocumentCropper, UsbCameraService
    from PIL import Image

    # Paint outer border black and inner crop region bright green to verify manual crop_box
    img = Image.new("RGB", (1000, 1000), color=(0, 0, 0))
    for x in range(200, 613):
        for y in range(200, 731):
            img.putpixel((x, y), (20, 220, 40))
    src = tmp_path / "manual.jpg"
    img.save(src, format="JPEG", quality=95)

    cropper = DocumentCropper()
    cropped_path = Path(
        cropper.crop_and_resize(
            str(src), DocumentStandard.RF_PASSPORT_35x45, crop_box=(200, 200, 613, 731)
        )
    )
    with Image.open(cropped_path) as out_img:
        assert out_img.size == (413, 531)
        center_rgb = out_img.getpixel((206, 265))
        assert center_rgb[1] > 170 and center_rgb[0] < 70

    # External editor export & re-import (US-07 5б)
    edited_path = tmp_path / "externally_edited.jpg"
    with Image.open(cropped_path) as out_img:
        out_img.save(edited_path, format="JPEG", dpi=(300, 300))

    cam = _build_with_signature(UsbCameraService, mock_mode=True, mock_connected=True)
    if hasattr(cam, "import_from_path"):
        reimported = cam.import_from_path(edited_path)
        assert Path(reimported.local_file_path).is_file()
        assert (reimported.width_px, reimported.height_px) == (413, 531)
    else:
        re_out = Path(cropper.crop_and_resize(str(edited_path), DocumentStandard.RF_PASSPORT_35x45))
        with Image.open(re_out) as final_img:
            assert final_img.size == (413, 531)


@pytest.mark.desktop
def test_t1_f8_windows_printer_service_mock_lists_and_prints_job(
    e2e_env, e2e_image_factory, tmp_path
):
    from desktop.hardware.dto import PrinterInfo, PrintJob, PrintResult
    from desktop.hardware.printer_service import WindowsPrinterService

    printer_svc = _build_with_signature(WindowsPrinterService, mock_mode=True, mock_online=True)
    printers = printer_svc.list_available_printers()
    assert len(printers) >= 1
    assert isinstance(printers[0], PrinterInfo)
    online_printers = [p for p in printers if p.is_online]
    assert len(online_printers) >= 1

    print_file = tmp_path / "doc_ready.jpg"
    print_file.write_bytes(e2e_image_factory(fmt="JPEG", size=(413, 531)))
    job = PrintJob(
        file_path=str(print_file),
        printer_name=online_printers[0].name,
        width_cm=3.5,
        height_cm=4.5,
        copies=2,
    )
    result = printer_svc.send_to_print(job)
    assert isinstance(result, PrintResult)
    assert result.is_success is True
    assert result.spooler_job_id > 0
    assert result.error_message == ""


# =============================================================================
# Feature 9: Desktop Employee MVP App (US-03, US-05, US-06, US-07) - 6 tests
# =============================================================================


@pytest.mark.django_db(transaction=True)
@pytest.mark.desktop
def test_t1_f9_rest_api_client_connects_to_live_server_and_authenticates_staff(
    e2e_env, e2e_seed_data, live_server
):
    from desktop.api_client.rest_client import RestApiClient

    manager_a = _seed_get(e2e_seed_data, "manager_a")
    salon_a = _seed_get(e2e_seed_data, "salon_a")
    admin = _seed_get(e2e_seed_data, "admin")

    mgr_client = RestApiClient(base_url=f"{live_server.url}/api/v1")
    mgr_data = mgr_client.login(manager_a.email, _seed_password(e2e_seed_data, "manager_a"))
    assert mgr_data
    assert mgr_client.auth_token
    assert mgr_client.role == UserRole.MANAGER
    assert str(mgr_client.salon_id) == str(salon_a.id)

    adm_client = RestApiClient(base_url=f"{live_server.url}/api/v1")
    adm_client.login(admin.phone, _seed_password(e2e_seed_data, "admin"))
    assert adm_client.role == UserRole.ADMIN


@pytest.mark.django_db(transaction=True)
@pytest.mark.desktop
def test_t1_f9_order_queue_presenter_refreshes_and_transitions_status(
    e2e_env, e2e_seed_data, api_client_factory, upload_photo_via_api, e2e_image_factory,
    live_server,
):
    from apps.core.models import Order
    from desktop.api_client.rest_client import RestApiClient
    from desktop.hardware.printer_service import WindowsPrinterService
    from desktop.presenters.order_queue_presenter import OrderQueuePresenter

    order_data, _ = _create_online_order_via_api(
        api_client_factory, upload_photo_via_api, e2e_image_factory, e2e_seed_data, "client_1"
    )
    manager_a = _seed_get(e2e_seed_data, "manager_a")
    rest = RestApiClient(base_url=f"{live_server.url}/api/v1")
    rest.login(manager_a.email, _seed_password(e2e_seed_data, "manager_a"))

    view = _PassiveMvpView()
    printer = _build_with_signature(WindowsPrinterService, mock_mode=True, mock_online=True)
    presenter = _build_with_signature(
        OrderQueuePresenter, view_obj=view, api_client=rest, printer_service=printer
    )

    queue = presenter.refresh_queue()
    assert any(str(item["id"]) == str(order_data["id"]) for item in queue)

    presenter.change_order_status(str(order_data["id"]), OrderStatus.IN_PROGRESS)
    order_obj = Order.objects.get(id=order_data["id"])
    assert order_obj.status == OrderStatus.IN_PROGRESS


@pytest.mark.django_db(transaction=True)
@pytest.mark.desktop
def test_t1_f9_order_queue_presenter_downloads_photo_and_completes_pickup(
    e2e_env, e2e_seed_data, api_client_factory, upload_photo_via_api, e2e_image_factory,
    live_server, tmp_path,
):
    from apps.core.models import Order
    from desktop.api_client.rest_client import RestApiClient
    from desktop.hardware.printer_service import WindowsPrinterService
    from desktop.presenters.order_queue_presenter import OrderQueuePresenter

    order_data, _ = _create_online_order_via_api(
        api_client_factory, upload_photo_via_api, e2e_image_factory, e2e_seed_data, "client_1"
    )
    manager_a = _seed_get(e2e_seed_data, "manager_a")
    rest = RestApiClient(base_url=f"{live_server.url}/api/v1")
    rest.login(manager_a.email, _seed_password(e2e_seed_data, "manager_a"))

    for st in (OrderStatus.IN_PROGRESS, OrderStatus.PRINTING, OrderStatus.READY_FOR_PICKUP):
        rest.update_order_status(str(order_data["id"]), st)

    view = _PassiveMvpView()
    printer = _build_with_signature(WindowsPrinterService, mock_mode=True, mock_online=True)
    presenter = _build_with_signature(
        OrderQueuePresenter, view_obj=view, api_client=rest, printer_service=printer
    )

    files = presenter.download_order_photos(str(order_data["id"]), str(tmp_path))
    assert len(files) >= 1
    assert Path(files[0]).is_file() and Path(files[0]).stat().st_size > 0

    result = presenter.complete_pickup(str(order_data["id"]), order_data["pickup_code"])
    assert result is not False
    order_obj = Order.objects.get(id=order_data["id"])
    assert order_obj.status == OrderStatus.COMPLETED
    assert order_obj.completed_at is not None


@pytest.mark.django_db(transaction=True)
@pytest.mark.desktop
def test_t1_f9_document_photo_presenter_imports_crops_creates_in_salon_order_and_prints(
    e2e_env, e2e_seed_data, e2e_image_factory, live_server, tmp_path
):
    from apps.core.models import Order
    from desktop.api_client.rest_client import RestApiClient
    from desktop.hardware.camera_service import DocumentCropper, UsbCameraService
    from desktop.hardware.printer_service import WindowsPrinterService
    from desktop.presenters.document_photo_presenter import DocumentPhotoPresenter

    cam_dir = tmp_path / "camera"
    cam_dir.mkdir()
    sample = cam_dir / "doc_shot.jpg"
    sample.write_bytes(e2e_image_factory(fmt="JPEG", size=(1000, 1200)))

    manager_a = _seed_get(e2e_seed_data, "manager_a")
    salon_a = _seed_get(e2e_seed_data, "salon_a")
    rest = RestApiClient(base_url=f"{live_server.url}/api/v1")
    rest.login(manager_a.email, _seed_password(e2e_seed_data, "manager_a"))

    cam = _build_with_signature(
        UsbCameraService,
        mock_mode=True,
        import_dir=cam_dir,
        mock_dir=cam_dir,
        source_dir=cam_dir,
        mock_image_path=sample,
        mock_connected=True,
    )
    cropper = DocumentCropper()
    printer = _build_with_signature(WindowsPrinterService, mock_mode=True, mock_online=True)
    printers = printer.list_available_printers()

    view = _PassiveMvpView()
    presenter = _build_with_signature(
        DocumentPhotoPresenter,
        view_obj=view,
        api_client=rest,
        camera_service=cam,
        cropper=cropper,
        printer_service=printer,
    )

    presenter.import_from_camera()
    presenter.apply_auto_crop(DocumentStandard.RF_PASSPORT_35x45)
    presenter.confirm_and_print(copies=4, printer_name=printers[0].name)

    in_salon = Order.objects.filter(order_type=OrderType.IN_SALON).order_by("-created_at").first()
    assert in_salon is not None
    assert in_salon.client is None
    assert in_salon.status == OrderStatus.PRINTING
    assert str(in_salon.assigned_salon_id) == str(salon_a.id)
    assert str(in_salon.pickup_salon_id) == str(salon_a.id)


@pytest.mark.django_db(transaction=True)
@pytest.mark.desktop
def test_t1_f9_salon_admin_presenter_manages_catalog_and_reassigns_order(
    e2e_env, e2e_seed_data, api_client_factory, upload_photo_via_api, e2e_image_factory,
    live_server,
):
    from apps.core.models import Equipment, Order
    from desktop.api_client.rest_client import RestApiClient
    from desktop.presenters.salon_admin_presenter import SalonAdminPresenter

    order_data, _ = _create_online_order_via_api(
        api_client_factory, upload_photo_via_api, e2e_image_factory, e2e_seed_data, "client_1"
    )
    manager_a = _seed_get(e2e_seed_data, "manager_a")
    admin = _seed_get(e2e_seed_data, "admin")
    salon_a = _seed_get(e2e_seed_data, "salon_a")
    salon_b = _seed_get(e2e_seed_data, "salon_b")
    service_std = _seed_get(e2e_seed_data, "service_standard")

    mgr_rest = RestApiClient(base_url=f"{live_server.url}/api/v1")
    mgr_rest.login(manager_a.email, _seed_password(e2e_seed_data, "manager_a"))
    mgr_presenter = _build_with_signature(
        SalonAdminPresenter, view_obj=_PassiveMvpView(), api_client=mgr_rest
    )
    mgr_presenter.update_equipment_and_services(
        str(salon_a.id),
        [
            {
                "name": "Принтер A-Доп",
                "equipment_type": "PHOTO_PRINTER",
                "max_width_cm": 30.0,
                "max_height_cm": 45.0,
                "is_operational": True,
            }
        ],
        [str(service_std.id)],
    )
    assert Equipment.objects.filter(salon=salon_a, name="Принтер A-Доп").exists()

    adm_rest = RestApiClient(base_url=f"{live_server.url}/api/v1")
    adm_rest.login(admin.email, _seed_password(e2e_seed_data, "admin"))
    adm_presenter = _build_with_signature(
        SalonAdminPresenter, view_obj=_PassiveMvpView(), api_client=adm_rest
    )
    adm_presenter.reassign_order(str(order_data["id"]), str(salon_b.id))
    reassigned = Order.objects.get(id=order_data["id"])
    assert str(reassigned.assigned_salon_id) == str(salon_b.id)


@pytest.mark.django_db(transaction=True)
@pytest.mark.desktop
def test_t1_f9_main_window_role_gating_and_5000ms_qtimer(
    e2e_env, e2e_seed_data, live_server
):
    from desktop.api_client.rest_client import RestApiClient
    from desktop.views.main_window import MainWindow
    from PySide6.QtCore import QTimer
    from PySide6.QtWidgets import QApplication, QPushButton, QTabWidget

    app = QApplication.instance() or QApplication([])
    assert app is not None

    manager_a = _seed_get(e2e_seed_data, "manager_a")
    admin = _seed_get(e2e_seed_data, "admin")

    mgr_rest = RestApiClient(base_url=f"{live_server.url}/api/v1")
    mgr_rest.login(manager_a.email, _seed_password(e2e_seed_data, "manager_a"))
    mgr_win = _build_with_signature(
        MainWindow,
        api_client=mgr_rest,
        role=UserRole.MANAGER,
        current_salon_id=str(mgr_rest.salon_id),
    )
    mgr_timers = [t.interval() for t in mgr_win.findChildren(QTimer) if t.interval() > 0]
    assert 5000 in mgr_timers
    admin_only_labels = (
        "Добавить салон",
        "Удалить салон",
        "Сотрудники",
        "Назначить салон вручную",
    )
    mgr_visible_buttons = {
        btn.text()
        for btn in mgr_win.findChildren(QPushButton)
        if not btn.isHidden() and btn.isEnabled()
    }
    for label in admin_only_labels:
        assert not any(label in txt for txt in mgr_visible_buttons)

    adm_rest = RestApiClient(base_url=f"{live_server.url}/api/v1")
    adm_rest.login(admin.email, _seed_password(e2e_seed_data, "admin"))
    adm_win = _build_with_signature(
        MainWindow, api_client=adm_rest, role=UserRole.ADMIN, current_salon_id=None
    )
    adm_timers = [t.interval() for t in adm_win.findChildren(QTimer) if t.interval() > 0]
    assert 5000 in adm_timers
    adm_tabs = adm_win.findChildren(QTabWidget)
    assert len(adm_tabs) >= 1
    mgr_win.close()
    adm_win.close()


# =============================================================================
# Feature 10: Security, Performance & Russian UI Invariants - 5 tests
# =============================================================================


@pytest.mark.django_db(transaction=True)
@pytest.mark.security
def test_t1_f10_passwords_hashed_never_plaintext_in_db_or_api(
    e2e_env, e2e_seed_data, api_client_factory
):
    for key in ("client_1", "manager_a", "admin"):
        user = _seed_get(e2e_seed_data, key)
        raw_pwd = _seed_password(e2e_seed_data, key)
        assert user.password != raw_pwd
        assert user.password.startswith(("pbkdf2_", "argon2", "bcrypt"))
        assert user.check_password(raw_pwd)

    admin_user = _seed_get(e2e_seed_data, "admin")
    admin_pwd = _seed_password(e2e_seed_data, "admin")
    adm_client = api_client_factory()
    login_resp = adm_client.post(
        "/api/v1/auth/login/",
        data={"login": admin_user.email, "password": admin_pwd},
        format="json",
    )
    assert login_resp.status_code == 200
    assert admin_pwd not in login_resp.content.decode("utf-8")

    adm_authed = api_client_factory(admin_user.email, admin_pwd)
    users_resp = adm_authed.get("/api/v1/users/")
    assert users_resp.status_code == 200
    body = users_resp.content.decode("utf-8")
    assert admin_pwd not in body
    assert admin_user.password not in body


@pytest.mark.django_db(transaction=True)
@pytest.mark.performance
def test_t1_f10_s_perf_01_typical_api_responses_under_2s(
    e2e_env, e2e_seed_data, api_client_factory, upload_photo_via_api, e2e_image_factory
):
    order_data, _ = _create_online_order_via_api(
        api_client_factory, upload_photo_via_api, e2e_image_factory, e2e_seed_data, "client_1"
    )
    manager_a = _seed_get(e2e_seed_data, "manager_a")
    mgr = api_client_factory(manager_a.email, _seed_password(e2e_seed_data, "manager_a"))

    t0 = time.perf_counter()
    list_resp = mgr.get("/api/v1/orders/")
    t_list = time.perf_counter() - t0
    assert list_resp.status_code == 200
    assert t_list <= 2.0

    t1 = time.perf_counter()
    detail_resp = mgr.get(f"/api/v1/orders/{order_data['id']}/")
    t_detail = time.perf_counter() - t1
    assert detail_resp.status_code == 200
    assert t_detail <= 2.0

    t2 = time.perf_counter()
    status_resp = mgr.post(
        f"/api/v1/orders/{order_data['id']}/status/",
        data={"status": OrderStatus.IN_PROGRESS},
        format="json",
    )
    t_status = time.perf_counter() - t2
    assert status_resp.status_code == 200
    assert t_status <= 2.0


@pytest.mark.django_db(transaction=True)
@pytest.mark.performance
def test_t1_f10_u_c_06_calculate_price_for_20_items_under_3s(
    e2e_env, e2e_seed_data, api_client_factory
):
    client_1 = _seed_get(e2e_seed_data, "client_1")
    service_std = _seed_get(e2e_seed_data, "service_standard")
    material = (service_std.allowed_materials or ["Глянцевая бумага"])[0]
    api = api_client_factory(client_1.email, _seed_password(e2e_seed_data, "client_1"))

    items = [
        {
            "service_id": str(service_std.id),
            "service": str(service_std.id),
            "width_cm": 10.0,
            "height_cm": 15.0,
            "quantity": (i % 5) + 1,
            "material": material,
            "options": [],
        }
        for i in range(20)
    ]
    start = time.perf_counter()
    resp = api.post("/api/v1/orders/calculate-price/", data={"items": items}, format="json")
    elapsed = time.perf_counter() - start
    assert resp.status_code == 200, resp.content
    assert elapsed <= 3.0
    data = resp.json()
    assert len(data["items"]) == 20
    assert float(data["total_price"]) > 0


@pytest.mark.django_db(transaction=True)
@pytest.mark.performance
def test_t1_f10_u_m_03_invalid_status_transition_rejected_under_2s(
    e2e_env, e2e_seed_data, api_client_factory, upload_photo_via_api, e2e_image_factory
):
    order_data, _ = _create_online_order_via_api(
        api_client_factory, upload_photo_via_api, e2e_image_factory, e2e_seed_data, "client_1"
    )
    manager_a = _seed_get(e2e_seed_data, "manager_a")
    mgr = api_client_factory(manager_a.email, _seed_password(e2e_seed_data, "manager_a"))

    start = time.perf_counter()
    resp = mgr.post(
        f"/api/v1/orders/{order_data['id']}/status/",
        data={"status": OrderStatus.COMPLETED},
        format="json",
    )
    elapsed = time.perf_counter() - start
    assert resp.status_code == 400
    assert elapsed <= 2.0
    body = resp.content.decode("utf-8")
    assert re.search(r"[А-Яа-яЁё]", body) is not None
    assert isinstance(InvalidStatusTransitionError("x"), Exception)
    assert isinstance(InvalidPickupCodeError("x"), Exception)


@pytest.mark.django_db(transaction=True)
@pytest.mark.integration
def test_t1_f10_s_use_01_russian_ui_invariants_across_enums_api_and_templates(
    e2e_env, e2e_seed_data, api_client_factory, resolve_web_url
):
    from django.test import Client

    assert len(ORDER_STATUS_LABELS_RU) == 10
    for status in OrderStatus:
        label = ORDER_STATUS_LABELS_RU[status]
        assert re.search(r"[А-Яа-яЁё]", label) is not None

    client_1 = _seed_get(e2e_seed_data, "client_1")
    api = api_client_factory(client_1.email, _seed_password(e2e_seed_data, "client_1"))
    bad_upload = api.post("/api/v1/photos/upload/", data={}, format="multipart")
    assert bad_upload.status_code == 400
    assert re.search(r"[А-Яа-яЁё]", bad_upload.content.decode("utf-8")) is not None

    web = Client()
    for route in ("register", "login"):
        html = web.get(resolve_web_url(route)).content.decode("utf-8")
        assert re.search(r"[А-Яа-яЁё]{3,}", html) is not None

    web.force_login(client_1)
    for route in ("order_create", "order_list"):
        html = web.get(resolve_web_url(route)).content.decode("utf-8")
        assert re.search(r"[А-Яа-яЁё]{3,}", html) is not None
    assert len(DOCUMENT_STANDARD_SPECS) == 4


# =============================================================================
# Feature 11: Containerization, CI/CD, PyInstaller & Run Guides - 5 tests
# =============================================================================


def test_t1_f11_dockerfile_backend_exists_with_python311_and_gunicorn():
    dockerfile = PROJECT_ROOT / "deploy" / "Dockerfile.backend"
    assert dockerfile.is_file()
    text = dockerfile.read_text(encoding="utf-8")
    assert re.search(r"FROM\s+python:3\.1[12]", text) is not None
    assert "requirements.txt" in text
    assert "gunicorn" in text.lower()


def test_t1_f11_docker_compose_defines_all_five_services():
    compose_path = PROJECT_ROOT / "deploy" / "docker-compose.yml"
    assert compose_path.is_file()
    text = compose_path.read_text(encoding="utf-8")
    low = text.lower()
    for marker in ("postgres", "minio", "nginx", "purge_expired_photos"):
        assert marker in low
    assert "services:" in low
    assert "backend" in low or "web" in low or "django" in low


def test_t1_f11_nginx_conf_configures_tls_and_55m_upload_limit():
    nginx_path = PROJECT_ROOT / "deploy" / "nginx" / "nginx.conf"
    assert nginx_path.is_file()
    text = nginx_path.read_text(encoding="utf-8")
    assert "443" in text and "ssl" in text.lower()
    assert "ssl_certificate" in text
    assert re.search(r"client_max_body_size\s+55[mM]", text) is not None
    assert "proxy_pass" in text


def test_t1_f11_backup_script_and_pyinstaller_spec_valid():
    backup_path = PROJECT_ROOT / "deploy" / "scripts" / "backup_db.sh"
    assert backup_path.is_file()
    backup_text = backup_path.read_text(encoding="utf-8")
    assert backup_text.startswith("#!")
    assert "pg_dump" in backup_text

    spec_path = PROJECT_ROOT / "deploy" / "pyinstaller" / "YouPhoto.spec"
    assert spec_path.is_file()
    spec_text = spec_path.read_text(encoding="utf-8")
    assert "main.py" in spec_text
    assert "YouPhoto" in spec_text


def test_t1_f11_ci_workflow_and_run_guides_present_and_complete():
    ci_path = PROJECT_ROOT / ".github" / "workflows" / "ci.yml"
    assert ci_path.is_file()
    ci_text = ci_path.read_text(encoding="utf-8")
    assert "develop" in ci_text and "main" in ci_text
    assert "ruff check" in ci_text
    assert "makemigrations --check --dry-run" in ci_text
    assert "pytest" in ci_text

    readme_text = (PROJECT_ROOT / "README.md").read_text(encoding="utf-8")
    deploy_doc = PROJECT_ROOT / "docs" / "DEPLOYMENT.md"
    assert deploy_doc.is_file()
    combined = f"{readme_text}\n{deploy_doc.read_text(encoding='utf-8')}".lower()
    assert "docker compose" in combined or "docker-compose" in combined
    assert "youphoto.exe" in combined or "pyinstaller" in combined
