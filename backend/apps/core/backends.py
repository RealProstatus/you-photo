"""Пользовательский бэкенд аутентификации по email или номеру телефона (U-C-02)."""

from __future__ import annotations

from django.contrib.auth import get_user_model
from django.contrib.auth.backends import ModelBackend
from django.db.models import Q


class EmailOrPhoneBackend(ModelBackend):
    """Аутентификация пользователя по email (без учета регистра) или телефону."""

    def authenticate(self, request, username=None, password=None, **kwargs):
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
            # Митигация timing-атаки, аналогичная стандартному ModelBackend
            user_model().set_password(password)
            return None

        if user.check_password(password) and self.user_can_authenticate(user):
            return user
        return None

    def user_can_authenticate(self, user) -> bool:
        return bool(
            getattr(user, "is_active", False)
            and not getattr(user, "is_blocked", False)
        )


PhoneOrEmailBackend = EmailOrPhoneBackend
