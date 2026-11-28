"""Domain exceptions for the YouPhoto order system.

All user-facing exception messages must be in Russian per S-USE-01.
"""


class YouPhotoDomainError(Exception):
    """Base domain exception for the YouPhoto system."""


class InvalidStatusTransitionError(YouPhotoDomainError):
    """Raised when an order status transition is not allowed (U-M-03, sec. 5.2)."""


class EquipmentMismatchError(YouPhotoDomainError):
    """Raised when a salon lacks operational equipment or the required service (U-A-05)."""


class InvalidPickupCodeError(YouPhotoDomainError):
    """Raised when the provided order pickup code is invalid (US-06 5a)."""


class FileValidationError(YouPhotoDomainError):
    """Raised when an uploaded file fails format or 50 MB size validation (U-C-03)."""


class ActiveOrdersConflictError(YouPhotoDomainError):
    """Raised when deleting a salon, equipment, or service with active orders (U-A-02, US-05 4a)."""


class PermissionDeniedDomainError(YouPhotoDomainError):
    """Raised on role-based access control violations (S-SEC-03)."""
