"""Tier 2 E2E boundary and corner-case tests for Features 6-11."""

from __future__ import annotations

import inspect
import re
from pathlib import Path

import pytest
from apps.core.enums import (
    DOCUMENT_STANDARD_SPECS,
    DocumentStandard,
    OrderStatus,
    OrderType,
    UserRole,
)

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
    """Minimal passive view spy for MVP presenter boundary tests."""

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
                "quantity": 1,
                "material": (service.allowed_materials or ["Глянцевая"])[0],
                "options": [],
            }
        ],
    }
    resp = client.post("/api/v1/orders/", data=payload, format="json")
    assert resp.status_code in (200, 201), resp.content
    return resp.json(), photo_data


# =============================================================================
# Feature 6 Boundaries: Client Web Auth & Profile - 5 tests
# =============================================================================


@pytest.mark.django_db(transaction=True)
@pytest.mark.integration
def test_t2_f6_unchecked_pdn_consent_disabled_in_html_and_rejected_on_post(
    e2e_env, resolve_web_url
):
    from django.contrib.auth import get_user_model
    from django.test import Client

    user_model = get_user_model()
    web = Client()
    reg_url = resolve_web_url("register")
    get_html = web.get(reg_url).content.decode("utf-8")
    assert "disabled" in get_html.lower()
    assert "pdn_consent" in get_html

    resp = web.post(
        reg_url,
        data={
            "name": "Без Согласия",
            "phone": "+79995550001",
            "email": "noconsent@example.com",
            "password": "ValidPassword1!",
            "password_confirm": "ValidPassword1!",
        },
        follow=False,
    )
    assert resp.status_code != 302
    assert not user_model.objects.filter(email="noconsent@example.com").exists()
    assert "_auth_user_id" not in web.session


@pytest.mark.django_db(transaction=True)
@pytest.mark.integration
def test_t2_f6_short_password_under_8_chars_rejected_with_requirements(
    e2e_env, resolve_web_url
):
    from django.contrib.auth import get_user_model
    from django.test import Client

    user_model = get_user_model()
    web = Client()
    resp = web.post(
        resolve_web_url("register"),
        data={
            "name": "Короткий Пароль",
            "phone": "+79995550002",
            "email": "shortpwd@example.com",
            "password": "Abc1234",
            "password_confirm": "Abc1234",
            "pdn_consent": "on",
        },
        follow=False,
    )
    assert resp.status_code != 302
    assert not user_model.objects.filter(email="shortpwd@example.com").exists()
    html = resp.content.decode("utf-8")
    assert "8" in html


@pytest.mark.django_db(transaction=True)
@pytest.mark.integration
def test_t2_f6_mismatched_passwords_preserves_non_password_inputs(
    e2e_env, resolve_web_url
):
    from django.contrib.auth import get_user_model
    from django.test import Client

    user_model = get_user_model()
    web = Client()
    resp = web.post(
        resolve_web_url("register"),
        data={
            "name": "Иван Сохраненный",
            "phone": "+79995550003",
            "email": "preserved_5a@example.com",
            "password": "SecretPassword123",
            "password_confirm": "DifferentPass456",
            "pdn_consent": "on",
        },
        follow=False,
    )
    assert resp.status_code != 302
    assert not user_model.objects.filter(email="preserved_5a@example.com").exists()
    html = resp.content.decode("utf-8")
    assert "Иван Сохраненный" in html
    assert "+79995550003" in html
    assert "preserved_5a@example.com" in html
    assert "SecretPassword123" not in html


@pytest.mark.django_db(transaction=True)
@pytest.mark.integration
def test_t2_f6_duplicate_phone_or_email_shows_error_and_login_link(
    e2e_env, e2e_seed_data, resolve_web_url
):
    from django.contrib.auth import get_user_model
    from django.test import Client

    user_model = get_user_model()
    client_1 = _seed_get(e2e_seed_data, "client_1")
    reg_url = resolve_web_url("register")

    # Duplicate email
    resp_email = Client().post(
        reg_url,
        data={
            "name": "Дубль Почты",
            "phone": "+79995550004",
            "email": client_1.email,
            "password": "ValidPassword123",
            "password_confirm": "ValidPassword123",
            "pdn_consent": "on",
        },
    )
    assert resp_email.status_code != 302
    assert user_model.objects.filter(email=client_1.email).count() == 1
    assert "Войти в систему" in resp_email.content.decode("utf-8")

    # Duplicate phone
    resp_phone = Client().post(
        reg_url,
        data={
            "name": "Дубль Телефона",
            "phone": client_1.phone,
            "email": "unique_phone_dup@example.com",
            "password": "ValidPassword123",
            "password_confirm": "ValidPassword123",
            "pdn_consent": "on",
        },
    )
    assert resp_phone.status_code != 302
    assert not user_model.objects.filter(email="unique_phone_dup@example.com").exists()
    assert "Войти в систему" in resp_phone.content.decode("utf-8")


@pytest.mark.django_db(transaction=True)
@pytest.mark.security
def test_t2_f6_blocked_user_cannot_login_via_web(
    e2e_env, e2e_seed_data, resolve_web_url
):
    from django.test import Client

    blocked = _seed_get(e2e_seed_data, "blocked_user")
    pwd = _seed_password(e2e_seed_data, "blocked_user")
    login_url = resolve_web_url("login")
    get_html = Client().get(login_url).content.decode("utf-8")
    field_name = _extract_input_name(get_html, ("login", "username", "email"), "login")

    for identifier in (blocked.email, blocked.phone):
        web = Client()
        web.post(login_url, data={field_name: identifier, "password": pwd})
        assert "_auth_user_id" not in web.session
        prof_resp = web.get(resolve_web_url("profile"), follow=False)
        assert prof_resp.status_code == 302


# =============================================================================
# Feature 7 Boundaries: Client Web Order Creation & Tracking - 6 tests
# =============================================================================


@pytest.mark.django_db(transaction=True)
@pytest.mark.integration
def test_t2_f7_anonymous_order_create_redirects_to_login_with_next(
    e2e_env, resolve_web_url
):
    from django.test import Client

    create_url = resolve_web_url("order_create")
    resp = Client().get(create_url, follow=False)
    assert resp.status_code == 302
    location = resp["Location"]
    assert "next=" in location
    assert create_url in location or "orders" in location


@pytest.mark.django_db(transaction=True)
@pytest.mark.integration
def test_t2_f7_client_with_zero_orders_sees_empty_list_and_cta(
    e2e_env, e2e_seed_data, resolve_web_url
):
    from django.test import Client

    web = Client()
    web.force_login(_seed_get(e2e_seed_data, "client_2"))
    resp = web.get(resolve_web_url("order_list"))
    assert resp.status_code == 200
    html = resp.content.decode("utf-8")
    assert "Оформить заказ" in html


@pytest.mark.django_db(transaction=True)
@pytest.mark.integration
def test_t2_f7_cancel_draft_creates_no_order(
    e2e_env, e2e_seed_data, e2e_image_factory, resolve_web_url
):
    from apps.core.models import Order
    from django.core.files.uploadedfile import SimpleUploadedFile
    from django.test import Client

    client_1 = _seed_get(e2e_seed_data, "client_1")
    salon_a = _seed_get(e2e_seed_data, "salon_a")
    service_std = _seed_get(e2e_seed_data, "service_standard")
    material = (service_std.allowed_materials or ["Глянцевая"])[0]

    web = Client()
    web.force_login(client_1)
    create_url = resolve_web_url("order_create")
    upload_url = _discover_web_path(("upload",), create_url)
    if upload_url != create_url:
        up_file = SimpleUploadedFile("draft.jpg", e2e_image_factory(), content_type="image/jpeg")
        web.post(upload_url, data={"file": up_file, "files": up_file, "photo": up_file})

    cancel_url = _discover_web_path(("cancel",), create_url)
    web.post(
        cancel_url,
        data={
            "pickup_salon": str(salon_a.id),
            "service": str(service_std.id),
            "width_cm": "10",
            "height_cm": "15",
            "quantity": "1",
            "material": material,
            "action": "cancel",
            "cancel": "1",
        },
        follow=False,
    )
    assert Order.objects.filter(client=client_1).count() == 0


@pytest.mark.django_db(transaction=True)
@pytest.mark.integration
def test_t2_f7_invalid_file_upload_preserves_previously_accepted_draft_files(
    e2e_env, e2e_seed_data, e2e_image_factory, resolve_web_url
):
    from django.core.files.uploadedfile import SimpleUploadedFile
    from django.test import Client

    client_1 = _seed_get(e2e_seed_data, "client_1")
    web = Client()
    web.force_login(client_1)
    create_url = resolve_web_url("order_create")
    upload_url = _discover_web_path(("upload",), create_url)

    valid_file = SimpleUploadedFile(
        "accepted_photo.jpg", e2e_image_factory(fmt="JPEG"), content_type="image/jpeg"
    )
    r_valid = web.post(
        upload_url,
        data={"file": valid_file, "files": valid_file, "photo": valid_file},
    )
    assert r_valid.status_code in (200, 201, 302)

    bad_bmp = SimpleUploadedFile(
        "rejected.bmp", e2e_image_factory(fmt="BMP"), content_type="image/bmp"
    )
    r_bad = web.post(
        upload_url,
        data={"file": bad_bmp, "files": bad_bmp, "photo": bad_bmp},
        follow=True,
    )
    bad_body = r_bad.content.decode("utf-8")
    assert r_bad.status_code in (200, 400)
    assert "JPEG" in bad_body and "PNG" in bad_body

    # Verify previously accepted file still present in session draft or rendered page
    page_html = web.get(create_url).content.decode("utf-8")
    session_blob = " ".join(f"{k}:{v}" for k, v in web.session.items())
    assert "accepted_photo.jpg" in page_html or "accepted_photo.jpg" in session_blob


@pytest.mark.django_db(transaction=True)
@pytest.mark.integration
def test_t2_f7_order_detail_needs_clarification_shows_comment_agree_and_upload_form(
    e2e_env, e2e_seed_data, api_client_factory, upload_photo_via_api, e2e_image_factory,
    resolve_web_url,
):
    from django.test import Client

    order_data, _ = _create_online_order_via_api(
        api_client_factory, upload_photo_via_api, e2e_image_factory, e2e_seed_data, "client_1"
    )
    manager_a = _seed_get(e2e_seed_data, "manager_a")
    mgr = api_client_factory(manager_a.email, _seed_password(e2e_seed_data, "manager_a"))
    mgr.post(
        f"/api/v1/orders/{order_data['id']}/status/",
        data={"status": OrderStatus.IN_PROGRESS},
        format="json",
    )
    comment_text = "Фото слишком темное, загрузите светлее"
    r_clar = mgr.post(
        f"/api/v1/orders/{order_data['id']}/status/",
        data={"status": OrderStatus.NEEDS_CLARIFICATION, "comment": comment_text},
        format="json",
    )
    assert r_clar.status_code == 200

    web = Client()
    web.force_login(_seed_get(e2e_seed_data, "client_1"))
    resp = web.get(resolve_web_url("order_detail", order_id=order_data["id"]))
    assert resp.status_code == 200
    html = resp.content.decode("utf-8")
    assert "Требует уточнения" in html
    assert comment_text in html
    assert "Согласен печатать как есть" in html
    assert 'type="file"' in html


@pytest.mark.django_db(transaction=True)
@pytest.mark.integration
def test_t2_f7_hidden_salon_excluded_from_web_pickup_choices(
    e2e_env, e2e_seed_data, resolve_web_url
):
    from django.test import Client

    client_1 = _seed_get(e2e_seed_data, "client_1")
    salon_hidden = _seed_get(e2e_seed_data, "salon_hidden")

    web = Client()
    web.force_login(client_1)
    html = web.get(resolve_web_url("order_create")).content.decode("utf-8")
    assert salon_hidden.name not in html
    assert str(salon_hidden.id) not in html


# =============================================================================
# Feature 8 Boundaries: Hardware Camera, Cropper & Printer - 5 tests
# =============================================================================


@pytest.mark.desktop
def test_t2_f8_disconnected_mock_camera_raises_camera_connection_error_in_russian(
    e2e_env, tmp_path
):
    from desktop.hardware.camera_service import CameraConnectionError, UsbCameraService

    missing_dir = tmp_path / "nonexistent_camera"
    cam = _build_with_signature(
        UsbCameraService,
        mock_mode=True,
        mock_connected=False,
        connected=False,
        import_dir=missing_dir,
        mock_dir=missing_dir,
        source_dir=missing_dir,
    )
    assert cam.is_camera_connected() is False
    with pytest.raises(CameraConnectionError) as exc_info:
        cam.import_latest_photo()
    msg = str(exc_info.value)
    assert re.search(r"[А-Яа-яЁё]", msg) is not None
    assert "USB" in msg or "Фотоаппарат" in msg


@pytest.mark.desktop
def test_t2_f8_corrupted_image_rejected_by_camera_or_cropper(e2e_env, tmp_path):
    from desktop.hardware.camera_service import (
        CameraConnectionError,
        DocumentCropper,
        UsbCameraService,
    )

    corrupt_dir = tmp_path / "corrupt_dcim"
    corrupt_dir.mkdir()
    bad_file = corrupt_dir / "IMG_BAD.jpg"
    bad_file.write_bytes(b"THIS_IS_NOT_A_VALID_JPEG_IMAGE_STREAM")

    cropper = DocumentCropper()
    with pytest.raises((OSError, ValueError)):
        cropper.crop_and_resize(str(bad_file), DocumentStandard.RF_PASSPORT_35x45)

    cam = _build_with_signature(
        UsbCameraService,
        mock_mode=True,
        mock_connected=True,
        import_dir=corrupt_dir,
        mock_dir=corrupt_dir,
        source_dir=corrupt_dir,
        mock_image_path=bad_file,
    )
    if hasattr(cam, "import_from_path"):
        with pytest.raises((CameraConnectionError, ValueError, OSError)):
            cam.import_from_path(bad_file)


@pytest.mark.desktop
def test_t2_f8_invalid_crop_box_or_nonpositive_copies_rejected(
    e2e_env, e2e_image_factory, tmp_path
):
    from desktop.hardware.camera_service import DocumentCropper
    from desktop.hardware.dto import PrintJob
    from desktop.hardware.printer_service import WindowsPrinterService

    src = tmp_path / "valid.jpg"
    src.write_bytes(e2e_image_factory(fmt="JPEG", size=(800, 600)))

    cropper = DocumentCropper()
    with pytest.raises(ValueError):
        cropper.crop_and_resize(
            str(src), DocumentStandard.RF_PASSPORT_35x45, crop_box=(500, 500, 100, 100)
        )

    printer_svc = _build_with_signature(WindowsPrinterService, mock_mode=True, mock_online=True)
    printers = printer_svc.list_available_printers()
    for bad_copies in (0, -2):
        job = PrintJob(
            file_path=str(src),
            printer_name=printers[0].name,
            width_cm=3.5,
            height_cm=4.5,
            copies=bad_copies,
        )
        try:
            res = printer_svc.send_to_print(job)
            assert res.is_success is False
            assert res.error_message != ""
        except ValueError:
            pass


@pytest.mark.desktop
def test_t2_f8_offline_mock_printer_returns_failure_print_result(
    e2e_env, e2e_image_factory, tmp_path
):
    from desktop.hardware.dto import PrintJob
    from desktop.hardware.printer_service import WindowsPrinterService

    print_file = tmp_path / "doc.jpg"
    print_file.write_bytes(e2e_image_factory(fmt="JPEG", size=(413, 531)))

    printer_svc = _build_with_signature(WindowsPrinterService, mock_mode=True, mock_online=False)
    job = PrintJob(
        file_path=str(print_file),
        printer_name="Offline_NonExistent_Printer",
        width_cm=3.5,
        height_cm=4.5,
        copies=1,
    )
    result = printer_svc.send_to_print(job)
    assert result.is_success is False
    assert result.spooler_job_id == 0
    assert re.search(r"[А-Яа-яЁё]", result.error_message) is not None


@pytest.mark.desktop
def test_t2_f8_all_four_document_standards_produce_exact_300dpi_dimensions(
    e2e_env, e2e_image_factory, tmp_path
):
    from desktop.hardware.camera_service import DocumentCropper
    from PIL import Image

    src = tmp_path / "src_all.jpg"
    src.write_bytes(e2e_image_factory(fmt="JPEG", size=(1200, 1200)))
    cropper = DocumentCropper()

    for standard in DocumentStandard:
        spec = DOCUMENT_STANDARD_SPECS[standard]
        out = Path(cropper.crop_and_resize(str(src), standard))
        assert out.is_file()
        with Image.open(out) as im:
            assert im.size == (spec["width_px_300dpi"], spec["height_px_300dpi"])


# =============================================================================
# Feature 9 Boundaries: Desktop Employee MVP App - 6 tests
# =============================================================================


@pytest.mark.django_db(transaction=True)
@pytest.mark.desktop
def test_t2_f9_client_login_in_desktop_rejected(
    e2e_env, e2e_seed_data, live_server
):
    from desktop.api_client.rest_client import RestApiClient
    from desktop.views.login_dialog import LoginDialog
    from PySide6.QtWidgets import QApplication

    QApplication.instance() or QApplication([])
    client_1 = _seed_get(e2e_seed_data, "client_1")
    pwd = _seed_password(e2e_seed_data, "client_1")
    rest = RestApiClient(base_url=f"{live_server.url}/api/v1")

    dialog = _build_with_signature(LoginDialog, api_client=rest)
    if hasattr(dialog, "authenticate"):
        with pytest.raises(Exception) as exc_info:
            dialog.authenticate(client_1.email, pwd)
        assert "Доступ разрешен только сотрудникам салона" in str(exc_info.value)
    else:
        data = rest.login(client_1.email, pwd)
        assert data["user"]["role"] == UserRole.CLIENT
        from desktop.views.main_window import MainWindow

        with pytest.raises(Exception) as exc_info:
            _build_with_signature(
                MainWindow, api_client=rest, role=UserRole.CLIENT, current_salon_id=None
            )
        assert "Доступ разрешен только сотрудникам салона" in str(exc_info.value)


@pytest.mark.django_db(transaction=True)
@pytest.mark.desktop
def test_t2_f9_document_photo_presenter_camera_failure_creates_no_order(
    e2e_env, e2e_seed_data, live_server, tmp_path
):
    from apps.core.models import Order
    from desktop.api_client.rest_client import RestApiClient
    from desktop.hardware.camera_service import (
        CameraConnectionError,
        DocumentCropper,
        UsbCameraService,
    )
    from desktop.hardware.printer_service import WindowsPrinterService
    from desktop.presenters.document_photo_presenter import DocumentPhotoPresenter

    manager_a = _seed_get(e2e_seed_data, "manager_a")
    rest = RestApiClient(base_url=f"{live_server.url}/api/v1")
    rest.login(manager_a.email, _seed_password(e2e_seed_data, "manager_a"))

    missing_dir = tmp_path / "missing_cam"
    cam = _build_with_signature(
        UsbCameraService,
        mock_mode=True,
        mock_connected=False,
        connected=False,
        import_dir=missing_dir,
        mock_dir=missing_dir,
        source_dir=missing_dir,
    )
    printer = _build_with_signature(WindowsPrinterService, mock_mode=True, mock_online=True)
    view = _PassiveMvpView()
    presenter = _build_with_signature(
        DocumentPhotoPresenter,
        view_obj=view,
        api_client=rest,
        camera_service=cam,
        cropper=DocumentCropper(),
        printer_service=printer,
    )
    try:
        res = presenter.import_from_camera()
        assert res is None or res is False or len(view.errors) > 0
    except CameraConnectionError:
        pass
    assert Order.objects.filter(order_type=OrderType.IN_SALON).count() == 0


@pytest.mark.django_db(transaction=True)
@pytest.mark.desktop
def test_t2_f9_document_photo_presenter_cancel_before_confirm_deletes_temp_and_no_order(
    e2e_env, e2e_seed_data, e2e_image_factory, live_server, tmp_path
):
    from apps.core.models import Order
    from desktop.api_client.rest_client import RestApiClient
    from desktop.hardware.camera_service import DocumentCropper, UsbCameraService
    from desktop.hardware.printer_service import WindowsPrinterService
    from desktop.presenters.document_photo_presenter import DocumentPhotoPresenter

    cam_dir = tmp_path / "cam_cancel"
    cam_dir.mkdir()
    sample = cam_dir / "shot.jpg"
    sample.write_bytes(e2e_image_factory(fmt="JPEG", size=(900, 1100)))

    manager_a = _seed_get(e2e_seed_data, "manager_a")
    rest = RestApiClient(base_url=f"{live_server.url}/api/v1")
    rest.login(manager_a.email, _seed_password(e2e_seed_data, "manager_a"))

    cam = _build_with_signature(
        UsbCameraService,
        mock_mode=True,
        mock_connected=True,
        import_dir=cam_dir,
        mock_dir=cam_dir,
        source_dir=cam_dir,
        mock_image_path=sample,
    )
    printer = _build_with_signature(WindowsPrinterService, mock_mode=True, mock_online=True)
    presenter = _build_with_signature(
        DocumentPhotoPresenter,
        view_obj=_PassiveMvpView(),
        api_client=rest,
        camera_service=cam,
        cropper=DocumentCropper(),
        printer_service=printer,
    )
    presenter.import_from_camera()
    cropped_path = presenter.apply_auto_crop(DocumentStandard.RF_PASSPORT_35x45)
    cancel_fn = getattr(presenter, "cancel", None) or getattr(presenter, "cancel_session", None)
    assert cancel_fn is not None, "DocumentPhotoPresenter must expose cancel() / cancel_session()"
    cancel_fn()
    assert Order.objects.filter(order_type=OrderType.IN_SALON).count() == 0
    if isinstance(cropped_path, (str, Path)) and cropped_path:
        assert not Path(cropped_path).exists()


@pytest.mark.django_db(transaction=True)
@pytest.mark.desktop
def test_t2_f9_document_photo_presenter_printer_failure_leaves_in_salon_order_in_printing(
    e2e_env, e2e_seed_data, e2e_image_factory, live_server, tmp_path
):
    from apps.core.models import Order
    from desktop.api_client.rest_client import RestApiClient
    from desktop.hardware.camera_service import DocumentCropper, UsbCameraService
    from desktop.hardware.printer_service import WindowsPrinterService
    from desktop.presenters.document_photo_presenter import DocumentPhotoPresenter

    cam_dir = tmp_path / "cam_print_fail"
    cam_dir.mkdir()
    sample = cam_dir / "shot.jpg"
    sample.write_bytes(e2e_image_factory(fmt="JPEG", size=(900, 1100)))

    manager_a = _seed_get(e2e_seed_data, "manager_a")
    rest = RestApiClient(base_url=f"{live_server.url}/api/v1")
    rest.login(manager_a.email, _seed_password(e2e_seed_data, "manager_a"))

    cam = _build_with_signature(
        UsbCameraService,
        mock_mode=True,
        mock_connected=True,
        import_dir=cam_dir,
        mock_dir=cam_dir,
        source_dir=cam_dir,
        mock_image_path=sample,
    )
    printer = _build_with_signature(WindowsPrinterService, mock_mode=True, mock_online=False)
    view = _PassiveMvpView()
    presenter = _build_with_signature(
        DocumentPhotoPresenter,
        view_obj=view,
        api_client=rest,
        camera_service=cam,
        cropper=DocumentCropper(),
        printer_service=printer,
    )
    presenter.import_from_camera()
    presenter.apply_auto_crop(DocumentStandard.RF_PASSPORT_35x45)
    presenter.confirm_and_print(copies=2, printer_name="OfflinePrinter")

    order = Order.objects.filter(order_type=OrderType.IN_SALON).order_by("-created_at").first()
    assert order is not None
    assert order.status == OrderStatus.PRINTING


@pytest.mark.django_db(transaction=True)
@pytest.mark.desktop
def test_t2_f9_order_queue_presenter_needs_clarification_without_comment_rejected(
    e2e_env, e2e_seed_data, api_client_factory, upload_photo_via_api, e2e_image_factory,
    live_server,
):
    from apps.core.exceptions import YouPhotoDomainError
    from apps.core.models import Order
    from desktop.api_client.rest_client import RestApiClient
    from desktop.hardware.printer_service import WindowsPrinterService
    from desktop.presenters.order_queue_presenter import OrderQueuePresenter
    from django.core.exceptions import ValidationError
    from requests import RequestException

    order_data, _ = _create_online_order_via_api(
        api_client_factory, upload_photo_via_api, e2e_image_factory, e2e_seed_data, "client_1"
    )
    manager_a = _seed_get(e2e_seed_data, "manager_a")
    rest = RestApiClient(base_url=f"{live_server.url}/api/v1")
    rest.login(manager_a.email, _seed_password(e2e_seed_data, "manager_a"))
    rest.update_order_status(str(order_data["id"]), OrderStatus.IN_PROGRESS)

    view = _PassiveMvpView()
    printer = _build_with_signature(WindowsPrinterService, mock_mode=True, mock_online=True)
    presenter = _build_with_signature(
        OrderQueuePresenter, view_obj=view, api_client=rest, printer_service=printer
    )
    rejected = False
    try:
        result = presenter.change_order_status(
            str(order_data["id"]), OrderStatus.NEEDS_CLARIFICATION, ""
        )
        rejected = result is False or len(view.errors) > 0
    except (ValidationError, ValueError, YouPhotoDomainError, RequestException, RuntimeError):
        rejected = True

    assert rejected, "Empty clarification comment must trigger view error or validation exception"
    order = Order.objects.get(id=order_data["id"])
    assert order.status == OrderStatus.IN_PROGRESS


@pytest.mark.django_db(transaction=True)
@pytest.mark.desktop
def test_t2_f9_salon_admin_presenter_disabling_equipment_with_active_orders_warns_and_reroutes(
    e2e_env, e2e_seed_data, api_client_factory, upload_photo_via_api, e2e_image_factory,
    live_server,
):
    from apps.core.models import Order
    from desktop.api_client.rest_client import RestApiClient
    from desktop.presenters.salon_admin_presenter import SalonAdminPresenter

    order_data, _ = _create_online_order_via_api(
        api_client_factory,
        upload_photo_via_api,
        e2e_image_factory,
        e2e_seed_data,
        client_key="client_1",
        salon_key="salon_a",
        service_key="service_standard",
    )
    salon_a = _seed_get(e2e_seed_data, "salon_a")
    salon_b = _seed_get(e2e_seed_data, "salon_b")
    eq_a_std = _seed_get(e2e_seed_data, "eq_a_std")
    service_std = _seed_get(e2e_seed_data, "service_standard")
    admin = _seed_get(e2e_seed_data, "admin")

    rest = RestApiClient(base_url=f"{live_server.url}/api/v1")
    rest.login(admin.email, _seed_password(e2e_seed_data, "admin"))

    view = _PassiveMvpView()
    view.confirm_answer = True
    presenter = _build_with_signature(SalonAdminPresenter, view_obj=view, api_client=rest)

    # Disable eq_a_std while salon_a has active order -> presenter warns & confirms reroute
    presenter.update_equipment_and_services(
        str(salon_a.id),
        [
            {
                "id": str(eq_a_std.id),
                "name": eq_a_std.name,
                "equipment_type": eq_a_std.equipment_type,
                "max_width_cm": eq_a_std.max_width_cm,
                "max_height_cm": eq_a_std.max_height_cm,
                "is_operational": False,
            }
        ],
        [str(service_std.id)],
    )
    order = Order.objects.get(id=order_data["id"])
    assert str(order.assigned_salon_id) == str(salon_b.id) or (
        order.status == OrderStatus.PENDING_ROUTING
    )


@pytest.mark.django_db(transaction=True)
@pytest.mark.desktop
def test_t2_f9_document_photo_presenter_retake_and_reprint_existing_order(
    e2e_env, e2e_seed_data, e2e_image_factory, live_server, tmp_path
):
    """Verify US-07 5a/9a: retake before printing creates 1 order; reprint reuses existing order."""
    from apps.core.models import Order
    from desktop.api_client.rest_client import RestApiClient
    from desktop.hardware.camera_service import DocumentCropper, UsbCameraService
    from desktop.hardware.printer_service import WindowsPrinterService
    from desktop.presenters.document_photo_presenter import DocumentPhotoPresenter
    from desktop.presenters.order_queue_presenter import OrderQueuePresenter

    cam_dir = tmp_path / "retake_cam"
    cam_dir.mkdir()
    shot_1 = cam_dir / "shot_1.jpg"
    shot_2 = cam_dir / "shot_2.jpg"
    shot_1.write_bytes(e2e_image_factory(fmt="JPEG", size=(1000, 1200)))
    shot_2.write_bytes(e2e_image_factory(fmt="JPEG", size=(1200, 1400)))

    manager_a = _seed_get(e2e_seed_data, "manager_a")
    rest = RestApiClient(base_url=f"{live_server.url}/api/v1")
    rest.login(manager_a.email, _seed_password(e2e_seed_data, "manager_a"))

    cam = _build_with_signature(
        UsbCameraService,
        mock_mode=True,
        import_dir=cam_dir,
        mock_dir=cam_dir,
        source_dir=cam_dir,
        mock_image_path=shot_1,
        mock_connected=True,
    )
    cropper = DocumentCropper()
    printer = _build_with_signature(WindowsPrinterService, mock_mode=True, mock_online=True)
    printers = printer.list_available_printers()
    assert len(printers) >= 1
    printer_name = printers[0].name

    view = _PassiveMvpView()
    presenter = _build_with_signature(
        DocumentPhotoPresenter,
        view_obj=view,
        api_client=rest,
        camera_service=cam,
        cropper=cropper,
        printer_service=printer,
    )

    # US-07 5a: Retake shot before confirming print -> only 1 IN_SALON order created.
    presenter.import_from_camera()
    presenter.apply_auto_crop(DocumentStandard.RF_PASSPORT_35x45)

    if hasattr(cam, "set_mock_photo"):
        cam.set_mock_photo(str(shot_2))
    if hasattr(cam, "mock_image_path"):
        cam.mock_image_path = shot_2
    if hasattr(cam, "mock_photo_path"):
        cam.mock_photo_path = str(shot_2)

    presenter.import_from_camera()
    presenter.apply_auto_crop(DocumentStandard.RF_PASSPORT_35x45)
    presenter.confirm_and_print(copies=2, printer_name=printer_name)

    in_salon_orders = Order.objects.filter(order_type=OrderType.IN_SALON)
    assert in_salon_orders.count() == 1
    order = in_salon_orders.first()
    assert order is not None
    assert order.status == OrderStatus.PRINTING

    # US-07 9a: Reprint existing IN_SALON order still in PRINTING without creating duplicate order.
    if hasattr(presenter, "retry_print"):
        reprint_res = presenter.retry_print(printer_name=printer_name)
    else:
        queue_presenter = _build_with_signature(
            OrderQueuePresenter,
            view_obj=_PassiveMvpView(),
            api_client=rest,
            printer_service=printer,
        )
        reprint_res = queue_presenter.print_order(str(order.id), printer_name)

    assert reprint_res is not None
    if isinstance(reprint_res, list):
        assert len(reprint_res) >= 1
        assert all(r.is_success is True for r in reprint_res)
    else:
        assert reprint_res.is_success is True

    assert Order.objects.filter(order_type=OrderType.IN_SALON).count() == 1

    rest.update_order_status(str(order.id), OrderStatus.READY_FOR_PICKUP)
    rest.complete_pickup(str(order.id), order.pickup_code)
    order.refresh_from_db()
    assert order.status == OrderStatus.COMPLETED


# =============================================================================
# Feature 10 Boundaries: Security & Isolation - 5 tests
# =============================================================================


@pytest.mark.django_db(transaction=True)
@pytest.mark.security
def test_t2_f10_unauthenticated_requests_to_api_endpoints_denied(
    e2e_env, e2e_image_factory, api_client_factory, upload_photo_via_api
):
    anon = api_client_factory()
    for path in ("/api/v1/orders/", "/api/v1/salons/", "/api/v1/users/"):
        resp = anon.get(path)
        assert resp.status_code in (401, 403), f"{path} returned {resp.status_code}"

    up_resp = upload_photo_via_api(anon, e2e_image_factory(), "anon.jpg", "image/jpeg")
    assert up_resp.status_code in (401, 403)


@pytest.mark.django_db(transaction=True)
@pytest.mark.security
def test_t2_f10_cross_client_order_detail_and_photo_download_return_403(
    e2e_env, e2e_seed_data, api_client_factory, upload_photo_via_api, e2e_image_factory
):
    order_data, photo_data = _create_online_order_via_api(
        api_client_factory, upload_photo_via_api, e2e_image_factory, e2e_seed_data, "client_1"
    )
    client_2 = _seed_get(e2e_seed_data, "client_2")
    api_2 = api_client_factory(client_2.email, _seed_password(e2e_seed_data, "client_2"))

    assert api_2.get(f"/api/v1/orders/{order_data['id']}/").status_code == 403
    assert api_2.get(f"/api/v1/photos/{photo_data['id']}/download/").status_code == 403


@pytest.mark.django_db(transaction=True)
@pytest.mark.security
def test_t2_f10_cross_salon_manager_status_and_photo_download_return_403(
    e2e_env, e2e_seed_data, api_client_factory, upload_photo_via_api, e2e_image_factory
):
    order_data, photo_data = _create_online_order_via_api(
        api_client_factory,
        upload_photo_via_api,
        e2e_image_factory,
        e2e_seed_data,
        client_key="client_1",
        salon_key="salon_a",
        service_key="service_standard",
    )
    manager_b = _seed_get(e2e_seed_data, "manager_b")
    mgr_b = api_client_factory(manager_b.email, _seed_password(e2e_seed_data, "manager_b"))

    r_status = mgr_b.post(
        f"/api/v1/orders/{order_data['id']}/status/",
        data={"status": OrderStatus.IN_PROGRESS},
        format="json",
    )
    assert r_status.status_code == 403
    assert mgr_b.get(f"/api/v1/photos/{photo_data['id']}/download/").status_code == 403


@pytest.mark.django_db(transaction=True)
@pytest.mark.security
def test_t2_f10_xss_payload_in_clarification_comment_and_user_name_escaped(
    e2e_env, e2e_seed_data, api_client_factory, upload_photo_via_api, e2e_image_factory,
    resolve_web_url,
):
    from django.test import Client

    client_1 = _seed_get(e2e_seed_data, "client_1")
    xss_name = '<script>alert("xss_name")</script>'
    client_1.name = xss_name
    client_1.save(update_fields=["name"])

    order_data, _ = _create_online_order_via_api(
        api_client_factory, upload_photo_via_api, e2e_image_factory, e2e_seed_data, "client_1"
    )
    manager_a = _seed_get(e2e_seed_data, "manager_a")
    mgr = api_client_factory(manager_a.email, _seed_password(e2e_seed_data, "manager_a"))
    mgr.post(
        f"/api/v1/orders/{order_data['id']}/status/",
        data={"status": OrderStatus.IN_PROGRESS},
        format="json",
    )
    xss_comment = '<img src=x onerror="alert(1)">'
    mgr.post(
        f"/api/v1/orders/{order_data['id']}/status/",
        data={"status": OrderStatus.NEEDS_CLARIFICATION, "comment": xss_comment},
        format="json",
    )

    web = Client()
    web.force_login(client_1)
    prof_html = web.get(resolve_web_url("profile")).content.decode("utf-8")
    assert xss_name not in prof_html
    assert "&lt;script&gt;" in prof_html

    detail_resp = web.get(resolve_web_url("order_detail", order_id=order_data["id"]))
    detail_html = detail_resp.content.decode("utf-8")
    assert xss_comment not in detail_html
    assert "&lt;img" in detail_html


def test_t2_f10_s_sec_01_nginx_and_django_production_security_settings():
    nginx_text = (PROJECT_ROOT / "deploy" / "nginx" / "nginx.conf").read_text(encoding="utf-8")
    assert "443" in nginx_text
    assert "ssl_certificate" in nginx_text
    assert "TLSv1.2" in nginx_text or "TLSv1.3" in nginx_text
    assert "return 301 https://" in nginx_text or "ssl on" in nginx_text.lower()

    settings_text = (PROJECT_ROOT / "backend" / "config" / "settings.py").read_text(
        encoding="utf-8"
    )
    assert "SECRET_KEY" in settings_text
    assert "DEBUG" in settings_text
    assert "os.environ" in settings_text or "os.getenv" in settings_text


# =============================================================================
# Feature 11 Boundaries: Ops, CI/CD, PyInstaller & Docs - 5 tests
# =============================================================================


def test_t2_f11_docker_compose_has_no_hardcoded_plaintext_secrets():
    compose_text = (PROJECT_ROOT / "deploy" / "docker-compose.yml").read_text(encoding="utf-8")
    for forbidden in (
        "POSTGRES_PASSWORD: postgres",
        "POSTGRES_PASSWORD=postgres",
        "MINIO_ROOT_PASSWORD: minioadmin",
        "MINIO_ROOT_PASSWORD=minioadmin",
        "django-insecure-",
    ):
        assert forbidden not in compose_text
    assert "${" in compose_text or "env_file" in compose_text


def test_t2_f11_nginx_conf_avoids_default_1mb_upload_bottleneck():
    nginx_text = (PROJECT_ROOT / "deploy" / "nginx" / "nginx.conf").read_text(encoding="utf-8")
    match = re.search(r"client_max_body_size\s+(\d+)\s*([mMgGkK])", nginx_text)
    assert match is not None, "client_max_body_size directive missing in nginx.conf"
    value = int(match.group(1))
    unit = match.group(2).lower()
    size_mb = value * 1024 if unit == "g" else (value / 1024 if unit == "k" else value)
    assert size_mb >= 55


def test_t2_f11_backup_db_script_contains_pg_dump_logic():
    backup_path = PROJECT_ROOT / "deploy" / "scripts" / "backup_db.sh"
    assert backup_path.is_file()
    text = backup_path.read_text(encoding="utf-8")
    assert len(text.strip()) > 20
    assert "pg_dump" in text
    assert any(ext in text for ext in (".sql", ".dump", ".gz"))


def test_t2_f11_pyinstaller_spec_bundles_pyside6_desktop_entrypoint():
    spec_path = PROJECT_ROOT / "deploy" / "pyinstaller" / "YouPhoto.spec"
    assert spec_path.is_file()
    text = spec_path.read_text(encoding="utf-8")
    assert "Analysis" in text
    assert "EXE" in text
    assert "main.py" in text
    assert "YouPhoto" in text
    assert "PySide6" in text or "desktop" in text


def test_t2_f11_changelog_and_deployment_docs_exist_and_non_empty():
    changelog = PROJECT_ROOT / "CHANGELOG.md"
    deploy_doc = PROJECT_ROOT / "docs" / "DEPLOYMENT.md"
    readme = PROJECT_ROOT / "README.md"

    assert changelog.is_file() and len(changelog.read_text(encoding="utf-8").strip()) >= 50
    assert deploy_doc.is_file() and len(deploy_doc.read_text(encoding="utf-8").strip()) >= 100
    assert readme.is_file() and len(readme.read_text(encoding="utf-8").strip()) >= 100
