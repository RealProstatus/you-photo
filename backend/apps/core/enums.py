"""Shared domain enums and allowed order status transitions for YouPhoto.

Aligned with ``backend_core_module.puml``, ``camera_module.puml``, and section 5.2
of the functional specification.
"""

from enum import StrEnum


class UserRole(StrEnum):
    """System user roles governing access control (S-SEC-03)."""

    CLIENT = "CLIENT"
    MANAGER = "MANAGER"
    ADMIN = "ADMIN"

    @classmethod
    def choices(cls) -> list[tuple[str, str]]:
        """Return Django field choices with Russian display labels."""
        return [
            (cls.CLIENT, "Клиент"),
            (cls.MANAGER, "Менеджер"),
            (cls.ADMIN, "Администратор"),
        ]


class OrderType(StrEnum):
    """Order origin channel (online portal vs. in-salon document photo)."""

    ONLINE = "ONLINE"
    IN_SALON = "IN_SALON"

    @classmethod
    def choices(cls) -> list[tuple[str, str]]:
        """Return Django field choices with Russian display labels."""
        return [
            (cls.ONLINE, "Онлайн"),
            (cls.IN_SALON, "В салоне"),
        ]


class OrderStatus(StrEnum):
    """Canonical 10-state lifecycle of a YouPhoto order."""

    NEW = "NEW"
    PENDING_ROUTING = "PENDING_ROUTING"
    ASSIGNED_TO_SALON = "ASSIGNED_TO_SALON"
    IN_PROGRESS = "IN_PROGRESS"
    PRINTING = "PRINTING"
    FINISHING = "FINISHING"
    NEEDS_CLARIFICATION = "NEEDS_CLARIFICATION"
    IN_TRANSIT = "IN_TRANSIT"
    READY_FOR_PICKUP = "READY_FOR_PICKUP"
    COMPLETED = "COMPLETED"

    @classmethod
    def choices(cls) -> list[tuple[str, str]]:
        """Return Django field choices with Russian display labels."""
        return [(status.value, ORDER_STATUS_LABELS_RU[status]) for status in cls]

    @property
    def label_ru(self) -> str:
        """Return the Russian UI display label for this status."""
        return ORDER_STATUS_LABELS_RU[self]


ORDER_STATUS_LABELS_RU: dict[OrderStatus, str] = {
    OrderStatus.NEW: "Новый",
    OrderStatus.PENDING_ROUTING: "Ожидает распределения",
    OrderStatus.ASSIGNED_TO_SALON: "Передан в салон",
    OrderStatus.IN_PROGRESS: "В работе",
    OrderStatus.PRINTING: "В печати",
    OrderStatus.FINISHING: "Оформление",
    OrderStatus.NEEDS_CLARIFICATION: "Требует уточнения",
    OrderStatus.IN_TRANSIT: "Передается в салон выдачи",
    OrderStatus.READY_FOR_PICKUP: "Готов к выдаче",
    OrderStatus.COMPLETED: "Завершен",
}

# Allowed order status transition graph per section 5.2 of the functional specification.
ALLOWED_STATUS_TRANSITIONS: dict[OrderStatus, set[OrderStatus]] = {
    OrderStatus.NEW: {
        OrderStatus.ASSIGNED_TO_SALON,
        OrderStatus.PENDING_ROUTING,
    },
    OrderStatus.PENDING_ROUTING: {
        OrderStatus.ASSIGNED_TO_SALON,
    },
    OrderStatus.ASSIGNED_TO_SALON: {
        OrderStatus.IN_PROGRESS,
        OrderStatus.ASSIGNED_TO_SALON,  # Manual reassignment by administrator (US-02 3a).
        OrderStatus.PENDING_ROUTING,  # Equipment or service removed from salon (US-05 4a).
    },
    OrderStatus.IN_PROGRESS: {
        OrderStatus.PRINTING,
        OrderStatus.FINISHING,
        OrderStatus.NEEDS_CLARIFICATION,
        OrderStatus.PENDING_ROUTING,  # Equipment failure during processing (US-03 1a).
    },
    OrderStatus.PRINTING: {
        OrderStatus.FINISHING,
        OrderStatus.READY_FOR_PICKUP,  # When assigned salon == pickup salon.
        OrderStatus.IN_TRANSIT,  # When assigned salon != pickup salon.
        OrderStatus.NEEDS_CLARIFICATION,
    },
    OrderStatus.FINISHING: {
        OrderStatus.READY_FOR_PICKUP,  # When assigned salon == pickup salon.
        OrderStatus.IN_TRANSIT,  # When assigned salon != pickup salon.
        OrderStatus.NEEDS_CLARIFICATION,
    },
    OrderStatus.IN_TRANSIT: {
        OrderStatus.READY_FOR_PICKUP,
        OrderStatus.NEEDS_CLARIFICATION,  # Order arrived damaged or incomplete (US-06 2a).
    },
    OrderStatus.READY_FOR_PICKUP: {
        OrderStatus.COMPLETED,
        OrderStatus.NEEDS_CLARIFICATION,  # Defect spotted during customer pickup (US-06 7a).
    },
    OrderStatus.NEEDS_CLARIFICATION: {
        OrderStatus.IN_PROGRESS,
    },
    OrderStatus.COMPLETED: set(),  # Terminal status.
}


class DocumentStandard(StrEnum):
    """Supported document photo standards for in-salon capture and cropping (US-07)."""

    RF_PASSPORT_35x45 = "RF_PASSPORT_35x45"
    FOREIGN_PASSPORT_35x45 = "FOREIGN_PASSPORT_35x45"
    SCHENGEN_VISA_35x45 = "SCHENGEN_VISA_35x45"
    US_VISA_50x50 = "US_VISA_50x50"

    @classmethod
    def choices(cls) -> list[tuple[str, str]]:
        """Return Django field choices with Russian display labels."""
        return [
            (cls.RF_PASSPORT_35x45, "Паспорт РФ (35×45 мм)"),
            (cls.FOREIGN_PASSPORT_35x45, "Загранпаспорт (35×45 мм)"),
            (cls.SCHENGEN_VISA_35x45, "Виза Шенген (35×45 мм)"),
            (cls.US_VISA_50x50, "Виза США (50×50 мм)"),
        ]


DOCUMENT_STANDARD_SPECS: dict[DocumentStandard, dict[str, float | int | str]] = {
    DocumentStandard.RF_PASSPORT_35x45: {
        "title": "Паспорт РФ (35×45 мм)",
        "width_cm": 3.5,
        "height_cm": 4.5,
        "width_px_300dpi": 413,
        "height_px_300dpi": 531,
    },
    DocumentStandard.FOREIGN_PASSPORT_35x45: {
        "title": "Загранпаспорт (35×45 мм)",
        "width_cm": 3.5,
        "height_cm": 4.5,
        "width_px_300dpi": 413,
        "height_px_300dpi": 531,
    },
    DocumentStandard.SCHENGEN_VISA_35x45: {
        "title": "Виза Шенген (35×45 мм)",
        "width_cm": 3.5,
        "height_cm": 4.5,
        "width_px_300dpi": 413,
        "height_px_300dpi": 531,
    },
    DocumentStandard.US_VISA_50x50: {
        "title": "Виза США (50×50 мм)",
        "width_cm": 5.0,
        "height_cm": 5.0,
        "width_px_300dpi": 591,
        "height_px_300dpi": 591,
    },
}
