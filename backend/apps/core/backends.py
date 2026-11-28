"""Custom authentication backend supporting email or phone number login (U-C-02)."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from django.contrib.auth import get_user_model
from django.contrib.auth.backends import ModelBackend
from django.db.models import Q

if TYPE_CHECKING:
    from apps.core.models import User


class EmailOrPhoneBackend(ModelBackend):
    """Authenticate a user by case-insensitive email or exact phone number."""

    def authenticate(
        self,
        request: Any,
        username: str | None = None,
        password: str | None = None,
        **kwargs: Any,
    ) -> User | None:
        """Authenticate a user using email or phone credentials.

        Args:
            request: The current HTTP request, if available.
            username: Email or phone string passed as the primary login field.
            password: Raw password to verify.
            **kwargs: Optional ``login``, ``email``, or ``phone`` keyword fallbacks.

        Returns:
            The authenticated ``User`` instance, or ``None`` if authentication fails.
        """
        login_value = (
            username
            or kwargs.get("login")
            or kwargs.get("email")
            or kwargs.get("phone")
        )
        if not login_value or password is None:
            return None

        cleaned = str(login_value).strip()
        if not cleaned:
            return None

        user_model = get_user_model()
        user = user_model.objects.filter(
            Q(email__iexact=cleaned) | Q(phone=cleaned)
        ).first()
        if user is None:
            # Mitigate timing attacks by hashing the password even when no user is found.
            user_model().set_password(password)
            return None

        if user.check_password(password) and self.user_can_authenticate(user):
            return user
        return None

    def user_can_authenticate(self, user: Any) -> bool:
        """Return whether the user is active and not blocked.

        Args:
            user: User instance to check.

        Returns:
            ``True`` if the user is active and not blocked, ``False`` otherwise.
        """
        return bool(
            getattr(user, "is_active", False)
            and not getattr(user, "is_blocked", False)
        )


PhoneOrEmailBackend = EmailOrPhoneBackend
