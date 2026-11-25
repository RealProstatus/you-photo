"""Доменные исключения системы заказов YouPhoto (все сообщения на русском языке согласно S-USE-01)."""


class YouPhotoDomainError(Exception):
    """Базовое доменное исключение системы YouPhoto."""


class InvalidStatusTransitionError(YouPhotoDomainError):
    """Недопустимый переход статуса заказа (U-M-03, п. 5.2)."""


class EquipmentMismatchError(YouPhotoDomainError):
    """В выбранном салоне отсутствует исправное оборудование или услуга для выполнения заказа (U-A-05, US-02 3a)."""


class InvalidPickupCodeError(YouPhotoDomainError):
    """Неверный код выдачи заказа (US-06 5a)."""


class FileValidationError(YouPhotoDomainError):
    """Файл не подходит по формату или превышает лимит 50 МБ (U-C-03, S-INT-01)."""


class ActiveOrdersConflictError(YouPhotoDomainError):
    """У удаляемого салона, оборудования или услуги есть заказы в работе (U-A-02, US-05 4a)."""


class PermissionDeniedDomainError(YouPhotoDomainError):
    """Нарушение ролевого доступа (S-SEC-03)."""
