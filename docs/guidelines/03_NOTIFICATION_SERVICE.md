# 03. Гайдлайн модуля: Notification Service (`backend/apps/notifications/`)

**Основание**: `notification_module.puml`, сценарии `US-03`, `US-04`, `US-06`, `US-08`, требования `FUNC 3.3`, `S-INT-02`, `S-INT-03`.

---

## 1. Структура пакета

```text
backend/apps/notifications/
├── __init__.py
├── dto.py             # NotificationMessage DTO
├── models.py          # TelegramBindingToken (одноразовые токены привязки с TTL)
├── services.py        # INotificationService, AppriseNotificationService, TelegramBotBinder
└── views.py           # Webhook / API для обработки команды /start <token> от Telegram-бота
```

---

## 2. Классы и контракты (`notification_module.puml`)

### 2.1. `NotificationMessage` (`dto.py`)
`dataclass(frozen=True)`:
- `recipient_chat_id: str`
- `title: str`
- `body: str`

### 2.2. `TelegramBotBinder` (`services.py`)
Отвечает за привязку аккаунта клиента к Telegram (`US-08` шаги 7–8, исключения `7a`, `8a`, `8б`):
- `token_ttl_minutes: int = 30`
- `create_token(user_id: UUID) -> str`:
  - Создает криптографически стойкий токен привязки для пользователя со сроком действия `token_ttl_minutes`.
- `generate_binding_link(user_id: UUID) -> str`:
  - Возвращает ссылку вида `https://t.me/<TELEGRAM_BOT_USERNAME>?start=<token>`.
- `handle_start_webhook(token: str, chat_id: str) -> bool`:
  - Проверяет существование токена и его срок действия (`expires_at > timezone.now()`).
  - Если токен не найден или истек (`US-08 8б`) — возвращает `False` (система сообщает об ошибке и предлагает повторить привязку из профиля).
  - Если токен валиден — записывает `user.telegram_chat_id = chat_id`, помечает токен использованным и возвращает `True`.

### 2.3. `INotificationService` и `AppriseNotificationService` (`services.py`)
Интерфейс `INotificationService`:
- `notify_status_changed(chat_id: str, order_number: str, new_status_label: str, comment: str = "") -> bool`
- `send_pickup_reminder(chat_id: str, order_number: str, pickup_code: str, salon_address: str = "") -> bool`
- `generate_binding_link(user_id: UUID) -> str`

Реализация `AppriseNotificationService`:
- Использует библиотеку `apprise` с URL схемы `tgram://{bot_token}/{chat_id}/`.
- **Важные правила поведения**:
  1. Если у клиента не привязан Telegram (`chat_id` пустой, `US-08 7а/8а`) или заказ оформлен в салоне без аккаунта (`US-07`), уведомление **не отправляется**, и это не вызывает ошибку.
  2. Если внешний сервис Telegram Bot API недоступен (таблица зависимостей в п. 4 Функциональной спецификации), ошибка логируется, метод возвращает `False`, но **транзакция смены статуса заказа в БД не откатывается** (актуальный статус всегда остается доступным клиенту в личном кабинете).
  3. Все тексты уведомлений формируются на русском языке (`S-USE-01`):
     - Смена статуса: *«Статус вашего заказа №{order_number} изменен на: "{new_status_label}". {comment}»*
     - Напоминание о выдаче (`US-06 3a`): *«Напоминаем, что ваш заказ №{order_number} готов к выдаче. Код получения: {pickup_code}.»*
