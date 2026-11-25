"""Основные доменные модели системы заказов YouPhoto (UUID PK)."""

from __future__ import annotations

import hmac
import uuid
from datetime import datetime
from decimal import Decimal, InvalidOperation

from django.contrib.auth.models import (
    AbstractBaseUser,
    BaseUserManager,
    PermissionsMixin,
)
from django.db import models
from django.utils import timezone

from apps.core.enums import (
    ALLOWED_STATUS_TRANSITIONS,
    OrderStatus,
    OrderType,
    UserRole,
)
from apps.core.exceptions import InvalidStatusTransitionError


class Salon(models.Model):
    """Фотосалон (точка выполнения и/или выдачи заказов)."""

    ACTIVE_LOAD_STATUSES = (
        OrderStatus.ASSIGNED_TO_SALON,
        OrderStatus.IN_PROGRESS,
        OrderStatus.PRINTING,
        OrderStatus.FINISHING,
    )

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField(max_length=120, verbose_name="Название")
    address = models.CharField(max_length=255, verbose_name="Адрес")
    is_active = models.BooleanField(default=True, db_index=True, verbose_name="Активен")
    current_load = models.PositiveIntegerField(default=0, verbose_name="Текущая загрузка")
    delivery_cost = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        default=Decimal("150.00"),
        verbose_name="Стоимость доставки",
    )

    class Meta:
        verbose_name = "Салон"
        verbose_name_plural = "Салоны"
        ordering = ("name",)

    def get_current_load(self) -> int:
        if self._state.adding:
            return int(self.current_load or 0)
        if self.assigned_orders.exists():
            return self.assigned_orders.filter(status__in=self.ACTIVE_LOAD_STATUSES).count()
        return int(self.current_load or 0)

    def increment_load(self) -> None:
        self.current_load = int(self.current_load or 0) + 1
        if not self._state.adding:
            self.save(update_fields=["current_load"])

    def decrement_load(self) -> None:
        self.current_load = max(0, int(self.current_load or 0) - 1)
        if not self._state.adding:
            self.save(update_fields=["current_load"])

    def can_fulfill(self, item: OrderItem) -> bool:
        if not self.is_active or self._state.adding or item is None or item.service_id is None:
            return False
        if (item.width_cm or 0) <= 0 or (item.height_cm or 0) <= 0:
            return False
        if not self.services.filter(pk=item.service_id, is_active=True).exists():
            return False

        eq_qs = self.equipment.filter(is_operational=True)
        required_type = (item.service.required_equipment_type or "").strip()
        if required_type:
            eq_qs = eq_qs.filter(equipment_type=required_type)
        return any(eq.supports_format(item.width_cm, item.height_cm) for eq in eq_qs)

    def can_fulfill_order(self, order: Order) -> bool:
        if not self.is_active or self._state.adding or order is None or order._state.adding:
            return False
        items = list(order.items.select_related("service").all())
        if not items:
            return False
        return all(self.can_fulfill(item) for item in items)

    def __str__(self) -> str:
        return f"{self.name} ({self.address})"


class UserManager(BaseUserManager):
    """Менеджер пользователей с входом по email/телефону."""

    def create_user(
        self,
        email: str,
        password: str | None = None,
        phone: str = "",
        name: str = "",
        **extra_fields,
    ) -> User:
        cleaned_email = str(email or "").strip()
        if not cleaned_email:
            raise ValueError("Email обязателен для создания пользователя.")
        normalized_email = self.normalize_email(cleaned_email).strip().lower()
        extra_fields.setdefault("role", UserRole.CLIENT)
        extra_fields.setdefault("is_active", True)
        extra_fields.setdefault("is_blocked", False)
        extra_fields.setdefault("is_staff", False)

        user = self.model(
            email=normalized_email,
            phone=str(phone or "").strip(),
            name=str(name or "").strip(),
            **extra_fields,
        )
        if password is not None:
            user.set_password(password)
        else:
            user.set_unusable_password()
        user.save(using=self._db)
        return user

    def create_superuser(
        self,
        email: str,
        password: str | None = None,
        phone: str = "",
        name: str = "Администратор",
        **extra_fields,
    ) -> User:
        extra_fields.setdefault("role", UserRole.ADMIN)
        extra_fields.setdefault("is_staff", True)
        extra_fields.setdefault("is_superuser", True)
        extra_fields.setdefault("is_active", True)
        extra_fields.setdefault("is_blocked", False)

        if extra_fields.get("is_staff") is not True:
            raise ValueError("Суперпользователь должен иметь is_staff=True.")
        if extra_fields.get("is_superuser") is not True:
            raise ValueError("Суперпользователь должен иметь is_superuser=True.")

        return self.create_user(
            email=email,
            password=password,
            phone=phone,
            name=name,
            **extra_fields,
        )


class User(AbstractBaseUser, PermissionsMixin):
    """Пользователь системы (Клиент, Менеджер салона, Администратор)."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField(max_length=150, blank=True, default="", verbose_name="Имя")
    email = models.EmailField(unique=True, verbose_name="Email")
    phone = models.CharField(max_length=20, unique=True, verbose_name="Телефон")
    role = models.CharField(
        max_length=20,
        choices=UserRole.choices(),
        default=UserRole.CLIENT,
        db_index=True,
        verbose_name="Роль",
    )
    is_blocked = models.BooleanField(default=False, verbose_name="Заблокирован")
    is_active = models.BooleanField(default=True, verbose_name="Активен")
    is_staff = models.BooleanField(default=False, verbose_name="Доступ к админ-панели")
    telegram_chat_id = models.CharField(
        max_length=64,
        blank=True,
        null=True,
        verbose_name="Telegram Chat ID",
    )
    pdn_consent_at = models.DateTimeField(
        null=True,
        blank=True,
        verbose_name="Дата согласия на обработку ПДн",
    )
    salon = models.ForeignKey(
        Salon,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="managers",
        verbose_name="Салон",
    )
    date_joined = models.DateTimeField(default=timezone.now, verbose_name="Дата регистрации")

    objects = UserManager()

    USERNAME_FIELD = "email"
    REQUIRED_FIELDS = ("name", "phone")

    class Meta:
        verbose_name = "Пользователь"
        verbose_name_plural = "Пользователи"
        ordering = ("email",)

    def has_role(self, role: UserRole | str) -> bool:
        return self.role == str(role)

    def bind_telegram(self, chat_id: str) -> None:
        cleaned = str(chat_id or "").strip()
        self.telegram_chat_id = cleaned or None
        if not self._state.adding:
            self.save(update_fields=["telegram_chat_id"])

    def get_telegram_chat_id(self) -> str:
        return self.telegram_chat_id or ""

    def __str__(self) -> str:
        if self.name:
            return f"{self.name} ({self.email})"
        return self.email


class Equipment(models.Model):
    """Оборудование салона (принтер, плоттер, минилаб и др.)."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    salon = models.ForeignKey(
        Salon,
        on_delete=models.CASCADE,
        related_name="equipment",
        verbose_name="Салон",
    )
    name = models.CharField(max_length=150, verbose_name="Название оборудования")
    equipment_type = models.CharField(max_length=80, verbose_name="Тип оборудования")
    max_width_cm = models.FloatField(verbose_name="Макс. ширина (см)")
    max_height_cm = models.FloatField(verbose_name="Макс. высота (см)")
    is_operational = models.BooleanField(default=True, db_index=True, verbose_name="Исправно")

    class Meta:
        verbose_name = "Оборудование"
        verbose_name_plural = "Оборудование"
        ordering = ("salon__name", "name")

    def supports_format(self, width_cm: float, height_cm: float) -> bool:
        if (
            width_cm is None
            or height_cm is None
            or self.max_width_cm is None
            or self.max_height_cm is None
        ):
            return False
        if width_cm <= 0 or height_cm <= 0 or self.max_width_cm <= 0 or self.max_height_cm <= 0:
            return False

        req_min, req_max = sorted((float(width_cm), float(height_cm)))
        cap_min, cap_max = sorted((float(self.max_width_cm), float(self.max_height_cm)))
        return req_min <= cap_min and req_max <= cap_max

    def set_operational(self, state: bool) -> None:
        self.is_operational = bool(state)
        if not self._state.adding:
            self.save(update_fields=["is_operational"])

    def __str__(self) -> str:
        return f"{self.name} ({self.salon.name})"


class Service(models.Model):
    """Каталожная услуга фотопечати/постобработки."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField(max_length=150, verbose_name="Название услуги")
    service_type = models.CharField(max_length=80, db_index=True, verbose_name="Тип услуги")
    base_price = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        verbose_name="Базовая цена",
    )
    price_per_sq_cm = models.DecimalField(
        max_digits=10,
        decimal_places=4,
        default=Decimal("0.0000"),
        verbose_name="Цена за кв. см",
    )
    allowed_materials = models.JSONField(
        default=list,
        blank=True,
        verbose_name="Допустимые материалы",
    )
    allowed_options = models.JSONField(
        default=list,
        blank=True,
        verbose_name="Допустимые опции",
    )
    required_equipment_type = models.CharField(
        max_length=80,
        blank=True,
        default="",
        verbose_name="Требуемый тип оборудования",
    )
    salons = models.ManyToManyField(
        Salon,
        related_name="services",
        blank=True,
        verbose_name="Салоны",
    )
    is_active = models.BooleanField(default=True, db_index=True, verbose_name="Активна")

    class Meta:
        verbose_name = "Услуга"
        verbose_name_plural = "Услуги"
        ordering = ("name",)

    @staticmethod
    def _parse_option_price(value: object) -> Decimal | None:
        if value is None or isinstance(value, bool):
            return None
        if isinstance(value, (list, dict, tuple, set)):
            return None
        if isinstance(value, str):
            raw_str = value.strip()
            if not raw_str:
                return None
        else:
            raw_str = str(value)
        try:
            dec = Decimal(raw_str)
        except (InvalidOperation, ValueError, TypeError):
            return None
        if not dec.is_finite():
            return None
        return dec

    def get_allowed_options_map(self) -> dict[str, Decimal]:
        raw = self.allowed_options
        result: dict[str, Decimal] = {}
        if isinstance(raw, dict):
            for key, value in raw.items():
                name = str(key).strip() if key is not None else ""
                if not name:
                    continue
                price = self._parse_option_price(value)
                if price is not None:
                    result[name] = price
        elif isinstance(raw, list):
            for entry in raw:
                if isinstance(entry, dict):
                    raw_name = entry.get("name")
                    name = str(raw_name).strip() if raw_name is not None else ""
                    if not name:
                        continue
                    price = self._parse_option_price(entry.get("price", 0))
                    if price is not None:
                        result[name] = price
                elif isinstance(entry, str):
                    name = entry.strip()
                    if name:
                        result[name] = Decimal("0.00")
        return result

    def get_allowed_material_names(self) -> list[str]:
        raw = self.allowed_materials
        if isinstance(raw, dict):
            return [str(k).strip() for k in raw if str(k).strip()]
        if isinstance(raw, list):
            names: list[str] = []
            for entry in raw:
                if isinstance(entry, dict):
                    name = str(entry.get("name", "")).strip()
                    if name:
                        names.append(name)
                elif isinstance(entry, str) and entry.strip():
                    names.append(entry.strip())
            return names
        return []

    def __str__(self) -> str:
        return self.name


class Order(models.Model):
    """Заказ клиента или заказ в салоне (фото на документы)."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    order_number = models.CharField(max_length=32, unique=True, verbose_name="Номер заказа")
    pickup_code = models.CharField(max_length=12, verbose_name="Код выдачи")
    order_type = models.CharField(
        max_length=20,
        choices=OrderType.choices(),
        default=OrderType.ONLINE,
        verbose_name="Тип заказа",
    )
    status = models.CharField(
        max_length=32,
        choices=OrderStatus.choices(),
        default=OrderStatus.NEW,
        db_index=True,
        verbose_name="Статус",
    )
    total_price = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        default=Decimal("0.00"),
        verbose_name="Итоговая стоимость",
    )
    client = models.ForeignKey(
        User,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="orders",
        verbose_name="Клиент",
    )
    pickup_salon = models.ForeignKey(
        Salon,
        on_delete=models.PROTECT,
        related_name="pickup_orders",
        verbose_name="Салон выдачи",
    )
    assigned_salon = models.ForeignKey(
        Salon,
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="assigned_orders",
        verbose_name="Салон-исполнитель",
    )
    clarification_comment = models.TextField(
        blank=True,
        default="",
        verbose_name="Комментарий уточнения",
    )
    created_at = models.DateTimeField(
        auto_now_add=True,
        db_index=True,
        verbose_name="Дата создания",
    )
    completed_at = models.DateTimeField(
        null=True,
        blank=True,
        verbose_name="Дата завершения",
    )

    class Meta:
        verbose_name = "Заказ"
        verbose_name_plural = "Заказы"
        ordering = ("-created_at",)
        indexes = (
            models.Index(fields=["assigned_salon", "status"]),
            models.Index(fields=["pickup_salon", "status"]),
        )

    def get_status(self) -> OrderStatus:
        return OrderStatus(self.status)

    def verify_pickup_code(self, code: str) -> bool:
        if not self.pickup_code:
            return False
        return hmac.compare_digest(
            self.pickup_code.encode("utf-8"),
            str(code or "").strip().encode("utf-8"),
        )

    def can_transition_to(
        self,
        next_status: OrderStatus | str,
        actor: User | None = None,
    ) -> bool:
        try:
            current = OrderStatus(self.status)
            target = OrderStatus(next_status)
        except ValueError:
            return False

        if target not in ALLOWED_STATUS_TRANSITIONS.get(current, set()):
            return False

        if (
            current in {OrderStatus.PRINTING, OrderStatus.FINISHING}
            and self.assigned_salon_id is not None
            and self.pickup_salon_id is not None
        ):
            if target == OrderStatus.READY_FOR_PICKUP and (
                self.assigned_salon_id != self.pickup_salon_id
            ):
                return False
            if target == OrderStatus.IN_TRANSIT and (
                self.assigned_salon_id == self.pickup_salon_id
            ):
                return False

        if actor is None:
            return True

        if not actor.is_active or actor.is_blocked:
            return False

        if actor.role == UserRole.ADMIN:
            return True

        if actor.role == UserRole.CLIENT:
            return (
                current == OrderStatus.NEEDS_CLARIFICATION
                and target == OrderStatus.IN_PROGRESS
                and self.client_id == actor.id
            )

        if actor.role == UserRole.MANAGER:
            actor_salon_id = actor.salon_id
            if actor_salon_id is None:
                return False

            if current == OrderStatus.NEW:
                return False
            if current == OrderStatus.PENDING_ROUTING and target == OrderStatus.ASSIGNED_TO_SALON:
                return False
            if current == OrderStatus.ASSIGNED_TO_SALON and target == OrderStatus.ASSIGNED_TO_SALON:
                return False

            if current in {OrderStatus.IN_TRANSIT, OrderStatus.READY_FOR_PICKUP}:
                return actor_salon_id == self.pickup_salon_id

            if current == OrderStatus.NEEDS_CLARIFICATION and target == OrderStatus.IN_PROGRESS:
                return actor_salon_id in {self.assigned_salon_id, self.pickup_salon_id}

            return actor_salon_id == self.assigned_salon_id

        return False

    def assign_salon(self, salon: Salon) -> None:
        self.assigned_salon = salon
        if self.status in {OrderStatus.NEW, OrderStatus.PENDING_ROUTING}:
            self.status = OrderStatus.ASSIGNED_TO_SALON
        if not self._state.adding:
            self.save()

    def change_status(
        self,
        next_status: OrderStatus | str,
        actor: User | None = None,
        comment: str = "",
    ) -> OrderStatusHistory | None:
        if not self.can_transition_to(next_status, actor=actor):
            raise InvalidStatusTransitionError(
                f"Недопустимый переход статуса заказа из «{self.status}» в «{next_status}»."
            )

        comment_text = str(comment or "")
        old_status = self.status
        target = OrderStatus(next_status)
        self.status = target.value
        if target == OrderStatus.COMPLETED and self.completed_at is None:
            self.completed_at = timezone.now()
        if target == OrderStatus.NEEDS_CLARIFICATION and comment_text:
            self.clarification_comment = comment_text

        if self._state.adding:
            return None

        self.save()
        return OrderStatusHistory.objects.create(
            order=self,
            old_status=old_status,
            new_status=target.value,
            actor=actor,
            comment=comment_text,
        )

    def __str__(self) -> str:
        return f"Заказ {self.order_number} ({self.status})"


class PhotoFile(models.Model):
    """Загруженный файл фотографии (в MinIO/S3 или локальном хранилище)."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    order = models.ForeignKey(
        Order,
        null=True,
        blank=True,
        on_delete=models.CASCADE,
        related_name="photos",
        verbose_name="Заказ",
    )
    uploaded_by = models.ForeignKey(
        User,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="uploaded_photos",
        verbose_name="Загрузил",
    )
    file_key = models.CharField(
        max_length=512,
        db_index=True,
        verbose_name="Ключ в хранилище",
    )
    original_name = models.CharField(max_length=255, verbose_name="Исходное имя файла")
    file_format = models.CharField(max_length=16, verbose_name="Формат файла")
    size_bytes = models.BigIntegerField(verbose_name="Размер (байт)")
    uploaded_at = models.DateTimeField(auto_now_add=True, verbose_name="Дата загрузки")
    delete_after = models.DateTimeField(
        null=True,
        blank=True,
        verbose_name="Удалить после",
    )
    is_deleted_from_storage = models.BooleanField(
        default=False,
        db_index=True,
        verbose_name="Удален из хранилища",
    )

    class Meta:
        verbose_name = "Файл фотографии"
        verbose_name_plural = "Файлы фотографий"
        ordering = ("-uploaded_at",)
        indexes = (
            models.Index(fields=["is_deleted_from_storage", "delete_after"]),
        )

    def __str__(self) -> str:
        return f"{self.original_name} ({self.file_format})"


class OrderItem(models.Model):
    """Позиция заказа."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    order = models.ForeignKey(
        Order,
        on_delete=models.CASCADE,
        related_name="items",
        verbose_name="Заказ",
    )
    service = models.ForeignKey(
        Service,
        on_delete=models.PROTECT,
        related_name="order_items",
        verbose_name="Услуга",
    )
    photo = models.ForeignKey(
        PhotoFile,
        on_delete=models.PROTECT,
        related_name="order_items",
        verbose_name="Фотография",
    )
    width_cm = models.FloatField(verbose_name="Ширина (см)")
    height_cm = models.FloatField(verbose_name="Высота (см)")
    quantity = models.PositiveIntegerField(default=1, verbose_name="Тираж")
    material = models.CharField(max_length=100, verbose_name="Материал")
    options = models.JSONField(default=list, blank=True, verbose_name="Опции")
    item_price = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        default=Decimal("0.00"),
        verbose_name="Стоимость позиции",
    )

    class Meta:
        verbose_name = "Позиция заказа"
        verbose_name_plural = "Позиции заказа"

    @property
    def file_key(self) -> str:
        return self.photo.file_key

    def get_dimensions(self) -> tuple[float, float]:
        return (self.width_cm, self.height_cm)

    def get_subtotal(self) -> Decimal:
        return self.item_price

    def __str__(self) -> str:
        return f"{self.service.name} ({self.width_cm}×{self.height_cm} см × {self.quantity})"


class OrderStatusHistory(models.Model):
    """Журнал переходов статусов заказа."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    order = models.ForeignKey(
        Order,
        on_delete=models.CASCADE,
        related_name="history",
        verbose_name="Заказ",
    )
    old_status = models.CharField(
        max_length=32,
        blank=True,
        default="",
        verbose_name="Предыдущий статус",
    )
    new_status = models.CharField(
        max_length=32,
        choices=OrderStatus.choices(),
        verbose_name="Новый статус",
    )
    actor = models.ForeignKey(
        User,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="status_changes",
        verbose_name="Инициатор",
    )
    comment = models.TextField(blank=True, default="", verbose_name="Комментарий")
    changed_at = models.DateTimeField(
        auto_now_add=True,
        db_index=True,
        verbose_name="Дата изменения",
    )

    class Meta:
        verbose_name = "История статуса заказа"
        verbose_name_plural = "История статусов заказов"
        ordering = ("changed_at",)

    def get_status(self) -> OrderStatus:
        return OrderStatus(self.new_status)

    def get_timestamp(self) -> datetime:
        return self.changed_at

    def __str__(self) -> str:
        return f"{self.order.order_number}: {self.old_status} -> {self.new_status}"
