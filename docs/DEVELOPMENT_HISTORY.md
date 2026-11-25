# История разработки и журнал изменений проекта «YouPhoto» (Development History)

**Основание**: `2_Структура проекта YouPhoto.pdf` (п. 1.2 «Вехи проекта», п. 2.2 «Структура команды», п. 3.1 «Управление конфигурацией — Git Flow», п. 3.2.1 «Журнал изменений»), `3_Функциональная спецификация.pdf`.

Данный документ фиксирует хронологическую историю разработки системы заказов городской сети фотосалонов **«YouPhoto»** по этапам (вехам MSF `M1–M6`), веткам **Git Flow** (`main`, `develop`, `feature/*`), коммитам и Pull Request'ам, а также план завершения оставшихся этапов.

---

## 1. Сводный статус по проектным вехам (MSF Milestones)

| Веха | Наименование фазы (по «Структуре проекта») | Статус | Ключевые артефакты и результаты |
|------|--------------------------------------------|--------|---------------------------------|
| **M1** | Фаза выработки концепции | ✅ Завершена | Изучены и зафиксированы «Концепция проекта», «Структура проекта», сценарии `US-01..US-08`, рамки `FTR1–FTR7`. |
| **M2** | Фаза планирования и архитектуры | ✅ Завершена | Изучена «Функциональная спецификация» и UML-диаграммы; разработаны архитектурные гайдлайны модулей `docs/guidelines/00..09`, регламент для ИИ-агентов [AGENTS.md](../AGENTS.md), схема БД, контракты `IBackendApi`, `IFileStorage`, `INotificationService`, `ICameraService`, `IPrinterService`. |
| **M3** | Готовность серверной части (Этап 1) | 🔄 В процессе (ядро БД, Storage и Notifications готовы) | Реализованы доменные модели и миграции (`User`, `Salon`, `Equipment`, `Service`, `Order`, `OrderItem`, `PhotoFile`, `OrderStatusHistory`), модули `File Storage` (MinIO/S3, валидация `<= 50 МБ`, очистка через 30 дней) и `Notification Service` (`Apprise` + `TelegramBotBinder`). Ожидают завершения: `OrderService`, `RoutingService`, `PricingService`, `REST API v1`. |
| **M4** | Окончание фазы разработки (`FTR1–FTR7`) | ⏳ Запланирована | Реализация клиентского веб-портала (`Модуль 4`: Django Templates + Bootstrap 5 + `StatusPollerJS`) и десктопного АРМ сотрудника (`Модули 5–6`: `PySide6` MVP, `UsbCameraService`, `DocumentCropper`, `WindowsPrinterService`). |
| **M5** | Готовность к внедрению (Тестирование и DevOps) | 🔄 В процессе (готов пакет из 143 E2E-тестов) | Разработан полный набор из 143 сквозных приемочных тестов `tests/e2e/` (`Tier 1–4`) по всем `US-01..US-08`. Ожидают завершения: `deploy/` (`Docker Compose`, `Nginx`, `backup_db.sh`, `YouPhoto.spec`), `.github/workflows/ci.yml`, `tests/unit/`, `tests/integration/`, `tests/desktop/`. |
| **M6** | Внедрение и релиз `v1.0.0` | ⏳ Запланирована | Финальный прогон `migrate`, `ruff`, `pytest`, документация по запуску Docker-контейнеров и `YouPhoto.exe`, слияние `develop -> main` с тегом `v1.0.0`. |

---

## 2. История и структура веток (Git Flow)

В репозитории используется **Git Flow** с двумя защищёнными ветками: `main` (релизы) и `develop` (интеграция текущей разработки). Актуальный граф коммитов в любой момент можно посмотреть командой:

```bash
git log --graph --oneline --decorate --all
```

Каноническая схема ветвления проекта:

```text
main       ●──────────────────────────────────────────────────────▶ (v1.0.0 — Веха M6)
            \                                                   /
develop      ●────●────●────●────●────●────────────────────────●
              \  / \  / \  / \  / \  /
feature/*      ●    ●    ●    ●    ●   (PR в develop через --no-ff)
```

Фактическая хронология ключевых этапов и слияний в `develop`:

| Этап / PR | Ветка | Ключевой коммит / Merge | Содержание |
|-----------|-------|-------------------------|------------|
| Init (`M1–M2`) | `main` -> `develop` | `70c24d8`, `ab8671c` | Инициализация репозитория, конфигов и гайдлайнов `00..09`. |
| PR #1 (локальный merge) | `feature/US-02-backend-core-and-routing` | `9da852f` -> `923a3f4` | Модели БД (`User`, `Salon`, `Equipment`, `Service`, `Order` и др.), миграции, auth backend (`FTR1`, `FTR2`, `FTR4`). |
| PR #2 (локальный merge) | `feature/US-01-storage-and-notifications` | `359fc9a`, `b5fb525` -> `d5b1140` | Модуль `storage` (`FileValidator`, `MinioFileStorage`, очистка 30 дней) и модуль `notifications` (`Apprise`, `TelegramBotBinder`). |
| PR #3 (локальный merge) | `feature/US-01-US-08-e2e-test-suite` | `69dcc39` -> `af5d1ce` | Пакет из 143 сквозных приёмочных тестов `tests/e2e/` (`Tier 1–4`, `US-01..US-08`). |
| Docs & Sync | `develop` | `4a07be6`, `decb999`, `6de73a7`, `72748cc` | Фиксация истории разработки, замена абсолютных локальных путей на относительные, уточнение ссылок. |
| PR #4 (GitHub) | `Serebro1:develop` -> `develop` | `0f3e487` | Интеграция Этапа 1 (`Core`, `Storage`, `Notifications`, `E2E` и обновлённой документации) в `origin/develop`. |
| Docs & Agent Guidelines | `docs/agents-guidelines-and-history` -> `develop` | *(текущий PR)* | Добавление [AGENTS.md](../AGENTS.md), исключение `.codegraph/` в [`.gitignore`](../.gitignore) и актуализация `DEVELOPMENT_HISTORY.md`. |
---

## 3. Подробное описание выполненных этапов разработки

### Этап 0. Инициализация репозитория и проектирование архитектуры (Вехи M1–M2)
- **Базовые коммиты**:
  - `70c24d8` — `Create README.md` (первичная инициализация репозитория).
  - `ab8671c` — `chore(init): инициализация репозитория, конфигурации и архитектурных гайдлайнов 00..09`.
- **Выполненные работы**:
  1. Настроена конфигурация проекта: [pyproject.toml](../pyproject.toml) (`pytest`, `ruff`), [requirements.txt](../requirements.txt), [requirements-desktop.txt](../requirements-desktop.txt), [.env.example](../.env.example) и [.gitignore](../.gitignore) (защита секретов в соответствии с `152-ФЗ`, `S-SEC-01`, `S-SEC-02`).
  2. Сформирована модульная структура пакетов `backend/`, `desktop/`, `tests/`, `docs/guidelines/`.
  3. Реализованы единые перечисления и граф допустимых переходов 10 статусов заказа в [backend/apps/core/enums.py](../backend/apps/core/enums.py) и русскоязычные доменные исключения в [backend/apps/core/exceptions.py](../backend/apps/core/exceptions.py).
  4. Разработаны детальные архитектурные гайдлайны по всем 7 модулям и процессу Git Flow:
     - [00_ARCHITECTURE_OVERVIEW.md](guidelines/00_ARCHITECTURE_OVERVIEW.md) — общие инварианты, роли, таблица переходов 10 статусов и межмодульные интерфейсы.
     - [01_BACKEND_CORE.md](guidelines/01_BACKEND_CORE.md) — модели, сервисы (`OrderService`, `RoutingService`, `PricingService`) и REST API.
     - [02_FILE_STORAGE.md](guidelines/02_FILE_STORAGE.md) — валидация `JPEG/PNG/HEIC/TIFF <= 50 МБ`, `MinioFileStorage`, очистка через 30 дней (`S-SEC-04`).
     - [03_NOTIFICATION_SERVICE.md](guidelines/03_NOTIFICATION_SERVICE.md) — Telegram-уведомления через `Apprise` и привязка `TelegramBotBinder`.
     - [04_CLIENT_WEB_APP.md](guidelines/04_CLIENT_WEB_APP.md) — клиентский портал на Django Templates + Bootstrap 5 и `StatusPollerJS` (5 сек).
     - [05_DESKTOP_CLIENT.md](guidelines/05_DESKTOP_CLIENT.md) — десктопное приложение сотрудника на `PySide6` (паттерн MVP).
     - [06_HARDWARE_CAMERA_PRINTER.md](guidelines/06_HARDWARE_CAMERA_PRINTER.md) — интеграция с USB-камерой (`UsbCameraService`, `DocumentCropper`) и принтерами Windows (`WindowsPrinterService`).
     - [07_QA_TESTING_DEVOPS.md](guidelines/07_QA_TESTING_DEVOPS.md) — матрица автотестов `PyTest` и конфигурация `Docker Compose` / `Nginx` / `PyInstaller` / `CI`.
     - [08_REPOSITORY_AND_WORKFLOW.md](guidelines/08_REPOSITORY_AND_WORKFLOW.md) и [09_GIT_FLOW_AND_PR_HISTORY.md](guidelines/09_GIT_FLOW_AND_PR_HISTORY.md) — регламент управления конфигурацией, изменениями (RFC) и ветвлением Git Flow.

---

### Этап 1.1. Серверное ядро и доменная модель БД (Модуль 1 — Ветка `feature/US-02-backend-core-and-routing`)
- **Ветка**: `feature/US-02-backend-core-and-routing`
- **Коммит реализации**: `9da852f` — `feat(core): реализовать модели User, Salon, Equipment, Service, Order и миграции БД`
- **Коммит слияния (PR #1)**: `923a3f4` — `Merge pull request #1 from feature/US-02-backend-core-and-routing: Серверное ядро и модели данных (FTR1, FTR2, FTR4)`
- **Реализованные компоненты**:
  - Настройки серверного приложения Django ([backend/config/settings.py](../backend/config/settings.py), [urls.py](../backend/config/urls.py), [wsgi.py](../backend/config/wsgi.py), [asgi.py](../backend/config/asgi.py)).
  - Полная схема реляционной БД ([backend/apps/core/models.py](../backend/apps/core/models.py)) и миграция [0001_initial.py](../backend/apps/core/migrations/0001_initial.py):
    - `User` — кастомная модель пользователя с UUID, ролями `CLIENT` / `MANAGER` / `ADMIN`, признаком блокировки `is_blocked`, привязкой менеджера к салону `salon`, `telegram_chat_id` и датой согласия на ПДн `pdn_consent_at`.
    - `Salon` — филиал сети с признаком активности (`is_active`), загрузкой, тарифом доставки и проверками `can_fulfill(item)` / `can_fulfill_order(order)`.
    - `Equipment` — печатное оборудование салона с проверкой максимальных габаритов печати `supports_format(width_cm, height_cm)` (включая поворот листа на 90°) и признаком исправности `is_operational`.
    - `Service` — справочник услуг с базовой ценой, тарифом за кв. см, допустимыми материалами и опциями оформления.
    - `Order`, `OrderItem`, `PhotoFile`, `OrderStatusHistory` — модели заказа, позиций, метаданных файлов и неизменяемого журнала истории переходов статусов.
  - Бэкенд аутентификации как по `email`, так и по `phone` с проверкой блокировки аккаунта ([backend/apps/core/backends.py](../backend/apps/core/backends.py), требования `U-C-02`, `U-A-03`).
  - Административная панель Django ([backend/apps/core/admin.py](../backend/apps/core/admin.py)).

---

### Этап 1.2. Файловое хранилище и Сервис Telegram-уведомлений (Модули 2 и 3 — Ветка `feature/US-01-storage-and-notifications`)
- **Ветка**: `feature/US-01-storage-and-notifications`
- **Коммиты реализации**:
  - `359fc9a` — `feat(storage): реализовать FileValidator (JPEG/PNG/HEIC/TIFF <= 50MB), MinioFileStorage и очистку 30 дней (S-SEC-04)`
  - `b5fb525` — `feat(notifications): реализовать AppriseNotificationService и привязку TelegramBotBinder (US-08, FUNC 3.3)`
- **Коммит слияния (PR #2)**: `d5b1140` — `Merge pull request #2 from feature/US-01-storage-and-notifications: Файловое хранилище MinIO и сервис Telegram-уведомлений`
- **Реализованные компоненты**:
  - **Модуль 2 (`backend/apps/storage/`)**:
    - [dto.py](../backend/apps/storage/dto.py) — неизменяемый `FileMetadata`.
    - [validators.py](../backend/apps/storage/validators.py) — `FileValidator`: проверка размера до `50 МБ` включительно и проверка сигнатур форматов `JPEG`, `PNG`, `HEIC`, `TIFF` с русскоязычными сообщениями об ошибках (`U-C-03`, `S-INT-01`).
    - [services.py](../backend/apps/storage/services.py) — интерфейс `IFileStorage` и сервис `MinioFileStorage` (прозрачное переключение между объектным хранилищем S3/MinIO и локальным изолированным хранилищем по флагу `USE_MINIO`, `S-ENV-04`).
    - [tasks.py](../backend/apps/storage/tasks.py) и management-команда [purge_expired_photos.py](../backend/apps/storage/management/commands/purge_expired_photos.py) — `RetentionCleanupJob` для удаления исходных файлов через 30 календарных дней после перехода заказа в статус `COMPLETED` с сохранением карточки заказа и истории (`S-SEC-04`).
  - **Модуль 3 (`backend/apps/notifications/`)**:
    - [dto.py](../backend/apps/notifications/dto.py) — `NotificationMessage`.
    - [models.py](../backend/apps/notifications/models.py) и миграция [0001_initial.py](../backend/apps/notifications/migrations/0001_initial.py) — модель одноразовых токенов привязки `TelegramBindingToken` с проверкой срока действия (`TTL`).
    - [services.py](../backend/apps/notifications/services.py) — `TelegramBotBinder` (генерация ссылок `https://t.me/<bot>?start=<token>` и обработка `/start`) и `AppriseNotificationService` (отправка русскоязычных уведомлений о смене статуса и напоминаний о выдаче без отката транзакции заказа при недоступности внешнего сервиса).
    - [views.py](../backend/apps/notifications/views.py) и [urls.py](../backend/apps/notifications/urls.py) — HTTP-эндпоинты вебхука Telegram-бота.

---

### Этап 1.3. Сквозной пакет приемочного тестирования E2E (Трек QA — Ветка `feature/US-01-US-08-e2e-test-suite`)
- **Ветка**: `feature/US-01-US-08-e2e-test-suite`
- **Коммит реализации**: `69dcc39` — `test(e2e): реализовать набор сквозных приемочных тестов Tier 1-4 по сценариям US-01..US-08`
- **Коммит слияния (PR #3)**: `af5d1ce` — `Merge pull request #3 from feature/US-01-US-08-e2e-test-suite: Сквозные автотесты E2E Tier 1-4 по сценариям US-01..US-08`
- **Реализованные компоненты (`tests/e2e/`)**:
  - [conftest.py](../tests/e2e/conftest.py) — фикстуры тестовых салонов, оборудования, услуг, ролей пользователей и изолированного хранилища.
  - [test_tier1_backend_and_storage.py](../tests/e2e/test_tier1_backend_and_storage.py) и [test_tier2_backend_and_storage.py](../tests/e2e/test_tier2_backend_and_storage.py) — проверки моделей, графа переходов статусов, алгоритма распределения `US-02`, расчетов `PricingService`, валидации файлов до 50 МБ, очистки через 30 дней и уведомлений.
  - [test_tier1_web_desktop_ops.py](../tests/e2e/test_tier1_web_desktop_ops.py) и [test_tier2_web_desktop_ops.py](../tests/e2e/test_tier2_web_desktop_ops.py) — проверки веб-портала клиента (`US-01`, `US-04`, `US-08`), десктопных презентеров (`PySide6`), импорта с камеры и печати (`US-07`).
  - [test_tier3_cross_feature.py](../tests/e2e/test_tier3_cross_feature.py) и [test_tier4_workflows.py](../tests/e2e/test_tier4_workflows.py) — сквозные бизнес-процессы от онлайн-заказа или съемки в салоне до выдачи по коду и обработки всех исключительных ветвей (`1a..10a`).

---

### Этап 1.4. Интеграция Этапа 1 в `origin/develop`, регламент ИИ-агентов и актуализация документации
- **Коммиты и PR**:
  - `4a07be6` — фиксация журнала разработки `docs/DEVELOPMENT_HISTORY.md`.
  - `e49261a`..`2152f2c`, `decb999`, `6de73a7`, `72748cc` — перевод ссылок в документации на относительные пути и уточнение структуры каталога документации.
  - `0f3e487` (**PR #4** `Serebro1:develop` -> `develop`) — интеграция наработок Этапа 1 в общую ветку `develop`.
  - Ветка `docs/agents-guidelines-and-history` — добавление [AGENTS.md](../AGENTS.md) с правилами Git Flow, чек-листом проверок, стандартами кода и обязательными скилами (`codegraph`, `ponytail`, `ponytail-review`, `git`, `github-pr-status`, `browser-automation`, `workflows`), а также обновление [`.gitignore`](../.gitignore) (исключение `.codegraph/`) и `docs/DEVELOPMENT_HISTORY.md`.

---

## 4. Дорожная карта оставшихся этапов (от актуальной ветки `develop`)

При продолжении разработки от актуального состояния ветки `develop` (после **PR #4** `0f3e487` и текущего PR по `AGENTS.md`) выполняются следующие этапы по [09_GIT_FLOW_AND_PR_HISTORY.md](guidelines/09_GIT_FLOW_AND_PR_HISTORY.md):

1. **Завершение Этапа 1 (Сервисы ядра и REST API v1):**
   - Реализация сервисного слоя `backend/apps/core/services/` (`PricingService`, `RoutingService`, `OrderService`) и management-команды `seed_initial_data`.
   - Ветка `feature/US-03-rest-api-v1` -> реализация `backend/api/` (`serializers.py`, `permissions.py`, `views.py`, `urls.py`) -> PR в `develop`.
2. **Этап 2 (Клиентское веб-приложение — Модуль 4):**
   - Ветка `feature/US-01-US-04-US-08-client-web-portal` -> реализация `backend/apps/web/`, адаптивных шаблонов `backend/templates/` на Bootstrap 5 и скриптов `backend/static/js/status_poller.js` (опрос каждые 5 сек) -> PR в `develop`.
3. **Этап 3 (Десктопное приложение сотрудника и Оборудование — Модули 5 и 6):**
   - Ветка `feature/US-07-hardware-camera-printer` -> `desktop/hardware/` (`UsbCameraService`, `DocumentCropper`, `WindowsPrinterService`) -> PR в `develop`.
   - Ветка `feature/US-03-US-05-US-06-desktop-client` -> `desktop/api_client/`, `desktop/presenters/`, `desktop/views/`, `desktop/main.py` -> PR в `develop`.
4. **Этап 4 (Инфраструктура, Гайд по запуску контейнеров и `.exe`, Тесты — Модуль 7):**
   - Ветка `feature/FTR-7-deploy-ci-and-run-guide` -> `deploy/` (`Dockerfile.backend`, `docker-compose.yml`, `nginx.conf`, `backup_db.sh`, `YouPhoto.spec`), `.github/workflows/ci.yml` и руководство по запуску Docker-контейнеров и сборке `YouPhoto.exe` -> PR в `develop`.
   - Ветка `feature/US-01-US-08-qa-pytest-suite` -> `tests/unit/`, `tests/integration/`, `tests/desktop/` -> PR в `develop`.
5. **Этап 5 (Релиз `v1.0.0` — Веха M6):**
   - Финальная проверка `python backend/manage.py migrate`, `ruff check .`, `pytest` (100% зелёные тесты) и релизный PR в `main` с тегом `v1.0.0`.
