# 01. Гайдлайн модуля: Backend Core (`backend/apps/core/` & `backend/api/`)

**Основание**: `backend_core_module.puml`, `3_Функциональная спецификация.pdf` (п. 5.2), сценарии `US-01..US-08`, требования `U-C-01..07`, `U-M-01..07`, `U-A-01..06`, `S-SEC-01..03`.

---

## 1. Структура пакета

```text
backend/
├── apps/
│   └── core/
│       ├── models.py          # User, Salon, Equipment, Service, Order, OrderItem, PhotoFile, OrderStatusHistory
│       ├── services/
│       │   ├── order_service.py    # OrderService (реализация бизнес-операций IBackendApi)
│       │   ├── routing_service.py  # RoutingService (автоподбор и ручное переназначение салона)
│       │   └── pricing_service.py  # PricingService (расчет стоимости позиций и заказа)
│       ├── exceptions.py      # Доменные исключения (InvalidStatusTransitionError, EquipmentMismatchError и др.)
│       └── admin.py           # Настройка Django Admin
└── api/
    ├── urls.py                # Маршруты /api/v1/...
    ├── serializers.py         # DTO / Сериализаторы DRF
    ├── views.py               # Контроллеры REST API для Desktop и JS-поллинга
    └── permissions.py         # IsClient, IsManagerOfSalon, IsSystemAdmin
```

---

## 2. Доменные модели (`backend/apps/core/models.py`)

Все первичные ключи (`id`) должны использовать `UUIDField(primary_key=True, default=uuid.uuid4, editable=False)` согласно `backend_core_module.puml`.

### 2.1. `User` (наследник `AbstractBaseUser`, `PermissionsMixin`)
- `id: UUID`
- `name: CharField(max_length=150)` — Имя пользователя
- `email: EmailField(unique=True)`
- `phone: CharField(max_length=20, unique=True)`
- `role: CharField(choices=UserRole.choices, default=UserRole.CLIENT)`
- `is_blocked: BooleanField(default=False)` — Признак блокировки (`U-A-03`). Заблокированный пользователь не может войти и делать запросы к API.
- `telegram_chat_id: CharField(max_length=64, blank=True, null=True)`
- `pdn_consent_at: DateTimeField(null=True, blank=True)` — Дата согласия на обработку ПДн (`US-08`).
- `salon: ForeignKey('Salon', null=True, blank=True, on_delete=SET_NULL, related_name='managers')` — Привязка менеджера к салону (`U-A-03`).
- **Методы**:
  - `has_role(role: UserRole) -> bool`
  - `bind_telegram(chat_id: str) -> None`
  - Аутентификация должна поддерживать вход **как по `email`, так и по `phone`** (`U-C-02`).

### 2.2. `Salon` («Салон»)
- `id: UUID`
- `name: CharField(max_length=120)`
- `address: CharField(max_length=255)`
- `is_active: BooleanField(default=True)` — Если `False` («скрыт», `U-A-02`), салон не отображается клиентам для выдачи и не получает новые заказы.
- `current_load: PositiveIntegerField(default=0)` — Текущая загрузка (или вычисляется динамически как количество активных заказов салона в статусах `ASSIGNED_TO_SALON`, `IN_PROGRESS`, `PRINTING`, `FINISHING`).
- `delivery_cost: DecimalField(max_digits=10, decimal_places=2, default=150.00)` — Базовый тариф логистики из этого салона в другой салон выдачи.
- **Методы**:
  - `can_fulfill(item: OrderItem) -> bool`: проверяет, что салон активен (`is_active=True`), оказывает услугу позиции (`services`), и в салоне есть хотя бы одна единица исправного оборудования (`is_operational=True`), у которого `max_width_cm >= item.width_cm` и `max_height_cm >= item.height_cm` (с учетом возможного поворота листа на 90°: `min(w,h) <= min(max_w, max_h)` и `max(w,h) <= max(max_w, max_h)`).
  - `can_fulfill_order(order: Order) -> bool`: проверяет `can_fulfill` для всех позиций заказа целиком.

### 2.3. `Equipment` («Оборудование»)
- `id: UUID`
- `salon: ForeignKey(Salon, on_delete=CASCADE, related_name='equipment')`
- `name: CharField(max_length=150)`
- `equipment_type: CharField(max_length=80)` — Тип оборудования (связь с требованиями услуги)
- `max_width_cm: FloatField()`
- `max_height_cm: FloatField()`
- `is_operational: BooleanField(default=True)`
- **Методы**:
  - `supports_format(width_cm: float, height_cm: float) -> bool`

### 2.4. `Service` («Услуга»)
- `id: UUID`
- `name: CharField(max_length=150)`
- `service_type: CharField(max_length=80)` — Например: `standard_print`, `large_format`, `banner`, `document_photo`, `framing`
- `base_price: DecimalField(max_digits=10, decimal_places=2)` — Базовая цена (за единицу или за кв. см / стандарт)
- `price_per_sq_cm: DecimalField(max_digits=10, decimal_places=4, default=0)`
- `allowed_materials: JSONField(default=list)` — Список допустимых материалов (например, `["Глянцевая бумага", "Матовая бумага", "Плотная бумага", "Ламинированная бумага", "Баннерная ткань"]`)
- `allowed_options: JSONField(default=list)` — Список допустимых опций и их наценок (например, `{"Установка люверсов": 200, "Проклейка краев": 150, "Рамка": 300, "Накатка на пенокартон": 400}`)
- `required_equipment_type: CharField(max_length=80, blank=True)`
- `salons: ManyToManyField(Salon, related_name='services', blank=True)`

### 2.5. `Order` («Заказ»)
- `id: UUID`
- `order_number: CharField(max_length=32, unique=True)` — Человекочитаемый номер (например, `YP-2026-0001`)
- `pickup_code: CharField(max_length=12)` — 6-значный цифровой код выдачи (`US-01`, `US-06`)
- `order_type: CharField(choices=OrderType.choices, default=OrderType.ONLINE)`
- `status: CharField(choices=OrderStatus.choices, default=OrderStatus.NEW)`
- `total_price: DecimalField(max_digits=12, decimal_places=2, default=0)`
- `client: ForeignKey(User, null=True, blank=True, on_delete=SET_NULL, related_name='orders')` — `None` для заказов «Фото на документы» в салоне (`US-07`)
- `pickup_salon: ForeignKey(Salon, on_delete=PROTECT, related_name='pickup_orders')`
- `assigned_salon: ForeignKey(Salon, null=True, blank=True, on_delete=PROTECT, related_name='assigned_orders')`
- `clarification_comment: TextField(blank=True)`
- `created_at: DateTimeField(auto_now_add=True)`
- `completed_at: DateTimeField(null=True, blank=True)` — Заполняется при переходе в `COMPLETED` (точка отсчета 30 дней для `S-SEC-04`)

### 2.6. `PhotoFile` («Файл фотографии») и `OrderItem` («Позиция заказа»)
- `PhotoFile`:
  - `id: UUID`, `order: ForeignKey(Order, null=True, blank=True, on_delete=CASCADE, related_name='photos')`, `uploaded_by: ForeignKey(User, null=True, blank=True, on_delete=SET_NULL)`
  - `file_key: CharField(max_length=512)` — Ключ в хранилище `IFileStorage`
  - `original_name: CharField(max_length=255)`
  - `file_format: CharField(max_length=16)` (`JPEG`, `PNG`, `HEIC`, `TIFF`)
  - `size_bytes: BigIntegerField()`
  - `uploaded_at: DateTimeField(auto_now_add=True)`
  - `delete_after: DateTimeField(null=True, blank=True)` — Выставляется в `completed_at + 30 days`
  - `is_deleted_from_storage: BooleanField(default=False)`
- `OrderItem`:
  - `id: UUID`, `order: ForeignKey(Order, on_delete=CASCADE, related_name='items')`
  - `service: ForeignKey(Service, on_delete=PROTECT)`
  - `photo: ForeignKey(PhotoFile, on_delete=PROTECT)`
  - `width_cm: FloatField()`, `height_cm: FloatField()`
  - `quantity: PositiveIntegerField(default=1)`
  - `material: CharField(max_length=100)`
  - `options: JSONField(default=list)`
  - `item_price: DecimalField(max_digits=10, decimal_places=2)`

### 2.7. `OrderStatusHistory` («Запись истории»)
- `id: UUID`, `order: ForeignKey(Order, on_delete=CASCADE, related_name='history')`
- `old_status: CharField(max_length=32, blank=True)`
- `new_status: CharField(max_length=32)`
- `actor: ForeignKey(User, null=True, blank=True, on_delete=SET_NULL)`
- `comment: TextField(blank=True)`
- `changed_at: DateTimeField(auto_now_add=True)`

---

## 3. Сервисный слой (`backend/apps/core/services/`)

### 3.1. `PricingService`
- `calculate_item_price(service, width_cm, height_cm, quantity, material, options) -> Decimal`:
  - Проверяет совместимость `material` и `options` со справочником `service`.
  - Проверяет, что в сети есть хотя бы один активный салон с оборудованием, поддерживающим размер `width_cm × height_cm` (`U-C-04`). Если размер превышает максимальную область печати всех салонов — выбрасывает `ValidationError` с русским пояснением.
  - Вычисляет стоимость: `(service.base_price + width_cm * height_cm * service.price_per_sq_cm + sum(options_cost)) * quantity`.
- `calculate_total(items) -> Decimal`: сумма всех позиций заказа (`Правило 4`).

### 3.2. `RoutingService` (Алгоритм автораспределения `US-02`)
- `auto_assign_salon(order: Order) -> Optional[Salon]`:
  1. Ищет все активные салоны (`is_active=True`), для которых `salon.can_fulfill_order(order) == True`.
  2. Если список пуст (`Исключение 1a` в `US-02`):
     - Переводит заказ в статус `PENDING_ROUTING` («Ожидает распределения»).
     - Пишет запись в `OrderStatusHistory` («Не найден салон, способный выполнить заказ целиком. Передано администратору»).
     - Возвращает `None`.
  3. Если подходящие салоны найдены:
     - Для каждого салона считает функцию затрат (скоринг):
       `delivery_cost = 0 if salon.id == order.pickup_salon_id else salon.delivery_cost`
       `score = (salon.get_current_load() * 100) + float(delivery_cost)` (приоритет наименьшей загрузки и отсутствия межсалонной перевозки).
     - Выбирает салон с минимальным `score`.
     - Назначает `order.assigned_salon = best_salon`, переводит статус в `ASSIGNED_TO_SALON` («Передан в салон»), пишет `OrderStatusHistory` и вызывает `INotificationService.notify_status_changed`.
- `manual_reassign(order: Order, target_salon: Salon, admin_user: User, comment: str = "") -> Order`:
  - Проверяет роль `admin_user.role == UserRole.ADMIN` (`U-A-05`).
  - Проверяет `target_salon.is_active` и `target_salon.can_fulfill_order(order)`. Если салон не располагает нужным оборудованием или услугой — отклоняет с `EquipmentMismatchError` и понятной причиной (`US-02 3a`, `U-A-05`).
  - Назначает новый салон, переводит в `ASSIGNED_TO_SALON` (если был в `PENDING_ROUTING` или `ASSIGNED_TO_SALON`), фиксирует в `OrderStatusHistory` прежний и новый салон, а также `admin_user` (`FUNC 2.4`).

### 3.3. `OrderService`
- `create_order(client, items_dto, pickup_salon_id) -> Order` (`US-01`):
  - Создает заказ в статусе `NEW`, генерирует `order_number` и 6-значный `pickup_code`, сохраняет позиции, записывает историю и сразу вызывает `RoutingService.auto_assign_salon(order)`.
- `create_document_order(manager, salon, file_key, standard, copies) -> Order` (`US-07`):
  - Создает заказ с `order_type=OrderType.IN_SALON`, `client=None`, `pickup_salon=salon`, `assigned_salon=salon`, сразу в статусе `PRINTING` («В печати»), минуя `RoutingService`.
- `update_status(order, next_status, actor, comment="") -> Order` (`US-03`, `US-06`):
  - Проверяет права доступа `actor` к заказу.
  - Проверяет допустимость перехода в таблице статусов.
  - Специальное правило `US-03 шаг 5`: если менеджер отмечает заказ как готовый из `PRINTING` или `FINISHING`:
    - если `order.assigned_salon_id == order.pickup_salon_id` -> переводит в `READY_FOR_PICKUP`;
    - если `order.assigned_salon_id != order.pickup_salon_id` -> переводит в `IN_TRANSIT`.
  - Специальное правило `US-03 1a`: если менеджер возвращает заказ из `IN_PROGRESS` в `PENDING_ROUTING` из-за поломки оборудования — сбрасывает `assigned_salon=None` и пытается перераспределить через `RoutingService.auto_assign_salon(order)` среди оставшихся салонов.
  - Если у заказа есть `client` с `telegram_chat_id` — отправляет уведомление через `INotificationService`.
- `complete_pickup(order, pickup_code, actor) -> Order` (`US-06`, `US-07`):
  - Проверяет, что заказ в статусе `READY_FOR_PICKUP`.
  - Сверяет `order.pickup_code == pickup_code`. Если код неверный (`US-06 5a`) — выбрасывает `InvalidPickupCodeError`, статус не меняется.
  - Переводит заказ в `COMPLETED`, проставляет `completed_at = timezone.now()`, выставляет всем `PhotoFile` заказа `delete_after = completed_at + timedelta(days=30)`.

---

## 4. Спецификация REST API (`backend/api/`)

Все эндпоинты возвращают JSON с русскоязычными сообщениями об ошибках в поле `detail` или `errors`:

- `POST /api/v1/auth/login/` — Вход по `login` (телефон или email) и `password`. Возвращает токен/сессию и профиль пользователя (`id`, `role`, `salon_id`).
- `POST /api/v1/photos/upload/` — Загрузка фотографии (`multipart/form-data`), валидация через `FileValidator`, сохранение в `IFileStorage`.
- `POST /api/v1/orders/calculate-price/` — Расчет стоимости списка позиций до подтверждения (`U-C-06`, `≤ 3 с`).
- `GET /api/v1/orders/` — Список заказов (фильтрация по роли: клиент видит свои; менеджер — где `assigned_salon` или `pickup_salon` равен его салону; админ — все салоны с фильтром).
- `POST /api/v1/orders/` — Создание онлайн-заказа (`US-01`).
- `POST /api/v1/orders/document-photo/` — Создание заказа «Фото на документы» в салоне (`US-07`).
- `GET /api/v1/orders/<uuid:id>/` — Детали заказа + `items` + `photos` (со ссылками на скачивание) + `history` (используется и для JS-поллинга статуса `StatusPollerJS`).
- `POST /api/v1/orders/<uuid:id>/status/` — Смена статуса заказа (`updateOrderStatus`).
- `POST /api/v1/orders/<uuid:id>/reassign/` — Ручное назначение/смена салона-исполнителя администратором (`U-A-05`).
- `POST /api/v1/orders/<uuid:id>/complete-pickup/` — Выдача заказа с проверкой `pickup_code` (`US-06`).
- `POST /api/v1/orders/<uuid:id>/clarification-response/` — Ответ клиента на статус «Требует уточнения» (`US-03 2a`: загрузка нового файла или согласие печатать как есть).
- `GET/POST/PUT/DELETE /api/v1/salons/` и `/api/v1/salons/<uuid:id>/catalog/` — Управление салонами, оборудованием и услугами (`US-05`, включая проверку активных заказов при отключении оборудования `4a`).
- `GET/POST/PATCH /api/v1/users/` — Управление учетными записями сотрудников администратором (`U-A-03`).
