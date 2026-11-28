"""Django admin site registrations for the ``apps.core`` application."""

from __future__ import annotations

from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as BaseUserAdmin
from django.contrib.auth.forms import BaseUserCreationForm, UserChangeForm

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


class CoreUserCreationForm(BaseUserCreationForm):
    """Admin form for creating a new ``User`` instance."""

    class Meta(BaseUserCreationForm.Meta):
        model = User
        fields = ("email", "phone", "name", "role", "salon")


class CoreUserChangeForm(UserChangeForm):
    """Admin form for updating an existing ``User`` instance."""

    class Meta(UserChangeForm.Meta):
        model = User
        fields = "__all__"


@admin.register(User)
class UserAdmin(BaseUserAdmin):
    """Admin configuration for the custom ``User`` model."""

    form = CoreUserChangeForm
    add_form = CoreUserCreationForm

    list_display = (
        "email",
        "phone",
        "name",
        "role",
        "salon",
        "is_blocked",
        "is_active",
        "is_staff",
    )
    list_filter = ("role", "is_blocked", "is_active", "is_staff", "salon")
    search_fields = ("email", "phone", "name")
    ordering = ("email",)
    readonly_fields = ("id", "last_login", "date_joined")

    fieldsets = (
        (None, {"fields": ("id", "email", "password")}),
        (
            "Персональные данные",
            {"fields": ("name", "phone", "telegram_chat_id", "pdn_consent_at")},
        ),
        (
            "Роль и доступ",
            {
                "fields": (
                    "role",
                    "salon",
                    "is_blocked",
                    "is_active",
                    "is_staff",
                    "is_superuser",
                    "groups",
                    "user_permissions",
                )
            },
        ),
        ("Важные даты", {"fields": ("last_login", "date_joined")}),
    )

    add_fieldsets = (
        (
            None,
            {
                "classes": ("wide",),
                "fields": (
                    "email",
                    "phone",
                    "name",
                    "role",
                    "salon",
                    "password1",
                    "password2",
                ),
            },
        ),
    )


class EquipmentInline(admin.TabularInline):
    """Inline editor for ``Equipment`` attached to a ``Salon``."""

    model = Equipment
    extra = 0


@admin.register(Salon)
class SalonAdmin(admin.ModelAdmin):
    """Admin configuration for ``Salon``."""

    list_display = ("name", "address", "is_active", "current_load", "delivery_cost")
    list_filter = ("is_active",)
    search_fields = ("name", "address")
    inlines = (EquipmentInline,)


@admin.register(Equipment)
class EquipmentAdmin(admin.ModelAdmin):
    """Admin configuration for ``Equipment``."""

    list_display = (
        "name",
        "salon",
        "equipment_type",
        "max_width_cm",
        "max_height_cm",
        "is_operational",
    )
    list_filter = ("equipment_type", "is_operational", "salon")
    search_fields = ("name", "equipment_type", "salon__name")


@admin.register(Service)
class ServiceAdmin(admin.ModelAdmin):
    """Admin configuration for ``Service``."""

    list_display = (
        "name",
        "service_type",
        "base_price",
        "price_per_sq_cm",
        "required_equipment_type",
        "is_active",
    )
    list_filter = ("service_type", "is_active")
    search_fields = ("name", "service_type", "required_equipment_type")
    filter_horizontal = ("salons",)


class OrderItemInline(admin.TabularInline):
    """Inline editor for ``OrderItem`` inside ``OrderAdmin``."""

    model = OrderItem
    extra = 0
    readonly_fields = ("id",)


class OrderStatusHistoryInline(admin.TabularInline):
    """Read-only inline view of ``OrderStatusHistory`` inside ``OrderAdmin``."""

    model = OrderStatusHistory
    extra = 0
    readonly_fields = ("id", "old_status", "new_status", "actor", "comment", "changed_at")


@admin.register(Order)
class OrderAdmin(admin.ModelAdmin):
    """Admin configuration for ``Order``."""

    list_display = (
        "order_number",
        "order_type",
        "status",
        "total_price",
        "client",
        "pickup_salon",
        "assigned_salon",
        "created_at",
    )
    list_filter = ("status", "order_type", "pickup_salon", "assigned_salon")
    search_fields = ("order_number", "pickup_code", "client__email", "client__phone")
    readonly_fields = ("id", "created_at")
    inlines = (OrderItemInline, OrderStatusHistoryInline)


@admin.register(PhotoFile)
class PhotoFileAdmin(admin.ModelAdmin):
    """Admin configuration for ``PhotoFile``."""

    list_display = (
        "original_name",
        "file_format",
        "size_bytes",
        "order",
        "uploaded_by",
        "uploaded_at",
        "is_deleted_from_storage",
    )
    list_filter = ("file_format", "is_deleted_from_storage")
    search_fields = ("original_name", "file_key", "order__order_number")
    readonly_fields = ("id", "uploaded_at")


@admin.register(OrderItem)
class OrderItemAdmin(admin.ModelAdmin):
    """Admin configuration for ``OrderItem``."""

    list_display = (
        "order",
        "service",
        "width_cm",
        "height_cm",
        "quantity",
        "material",
        "item_price",
    )
    search_fields = ("order__order_number", "service__name", "material")


@admin.register(OrderStatusHistory)
class OrderStatusHistoryAdmin(admin.ModelAdmin):
    """Admin configuration for ``OrderStatusHistory``."""

    list_display = ("order", "old_status", "new_status", "actor", "changed_at")
    list_filter = ("new_status",)
    search_fields = ("order__order_number", "comment")
    readonly_fields = ("id", "changed_at")
