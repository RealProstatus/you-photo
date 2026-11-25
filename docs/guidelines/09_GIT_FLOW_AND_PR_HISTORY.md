# 09. Гайдлайн по фиксации истории разработки в Git Flow (Commits, Feature Branches, PR / Merge History)

**Основание**: `2_Структура проекта YouPhoto.pdf` (п. 1.2 «Вехи проекта», п. 2.2 «Структура команды», п. 3.1 «Управление конфигурацией — Git Flow»), `docs/guidelines/08_REPOSITORY_AND_WORKFLOW.md`.

Данный гайдлайн устанавливает обязательный порядок оформления истории Git-коммитов, веток `feature/*`, `release/*`, `develop`, `main` и слияний (`merge --no-ff`), чтобы история репозитория полностью отражала реальные этапы разработки проекта **«YouPhoto»** по методологии **Git Flow** и **MSF**.

> **Приоритетное правило при экономии токенов / бюджета**:
> При переходе между этапами (`M1 -> M2 -> M3 -> M4 -> M5`) или при снижении остатка квоты/токенов необходимо **в первую очередь** зафиксировать все наработанные изменения в чистую историю веток и мержей по Git Flow согласно данному гайдлайну, чтобы ни один модуль не остался незакоммиченным в рабочем дереве.

---

## 1. Структура веток Git Flow проекта YouPhoto

```text
main       ●───────────────────────────────────────────────────────────● (v1.0.0 — Веха M6)
            \                                                         /
develop      ●───────●───────────●───────────●───────────●───────────●
              \     / \         / \         / \         / \         /
feature/*      ●───●   ●───────●   ●───────●   ●───────●   ●───────●
             (M1-Core) (M1-API)    (M2-Web)  (M3-Desktop) (M4-QA/Deploy)
```

1. **`main`**:
   - Содержит начальную инициализацию проекта, базовую документацию/гайдлайны и финальный релизный мерж из `release/v1.0.0` (или `develop`).
2. **`develop`**:
   - Основная интеграционная ветка, ответвленная от `main`.
   - Все функциональные ветки `feature/*` вливаются в `develop` **только с флагом `--no-ff`** (`git merge --no-ff`), чтобы в графе `git log --graph` сохранялись узлы слияния каждого Pull Request / Merge Request.
3. **`feature/US-XX-<описание>`**:
   - Короткоживущие ветки под конкретные модули и пользовательские сценарии `US-01..US-08`.

---

## 2. Каноническая последовательность веток, коммитов и PR-мержей по этапам

При переходе от этапа к этапу (или при финальной упаковке истории репозитория) изменения раскладываются по следующим логическим веткам и мерж-коммитам:

### Базовая линия (`main` -> `develop`)
- **Ветка `main`**:
  - `chore(init): инициализация репозитория, конфигурации и архитектурных гайдлайнов 00..09`
  - Создание ветки `develop`: `git checkout -b develop`

---

### Этап 1 (Веха M3 — Серверное ядро, Хранилище, Уведомления и REST API)

1. **Ветка `feature/US-02-backend-core-and-routing`** (от `develop`):
   - *Файлы*: `backend/config/`, `backend/apps/core/` (модели, миграции, `admin.py`, `OrderService`, `RoutingService`, `PricingService`, management-команда `seed_initial_data`).
   - *Коммиты*:
     - `feat(core): реализовать модели User, Salon, Equipment, Service, Order и миграции БД`
     - `feat(US-02): реализовать RoutingService, PricingService, OrderService и seed_initial_data`
   - *Слияние в `develop`*:
     - `git checkout develop && git merge --no-ff feature/US-02-backend-core-and-routing -m "Merge pull request #1 from feature/US-02-backend-core-and-routing: Серверное ядро и автораспределение заказов (FTR1, FTR2, FTR4)"`

2. **Ветка `feature/US-01-storage-and-notifications`** (от `develop`):
   - *Файлы*: `backend/apps/storage/` (`FileValidator`, `MinioFileStorage`, `RetentionCleanupJob`, команда `purge_expired_photos`), `backend/apps/notifications/` (`AppriseNotificationService`, `TelegramBotBinder`).
   - *Коммиты*:
     - `feat(storage): реализовать FileValidator (JPEG/PNG/HEIC/TIFF <= 50MB), MinioFileStorage и очистку 30 дней (S-SEC-04)`
     - `feat(notifications): реализовать AppriseNotificationService и привязку TelegramBotBinder (US-08, FUNC 3.3)`
   - *Слияние в `develop`*:
     - `git checkout develop && git merge --no-ff feature/US-01-storage-and-notifications -m "Merge pull request #2 from feature/US-01-storage-and-notifications: Файловое хранилище MinIO и сервис Telegram-уведомлений"`

3. **Ветка `feature/US-03-rest-api-v1`** (от `develop`):
   - *Файлы*: `backend/api/` (`serializers.py`, `permissions.py`, `views.py`, `urls.py`).
   - *Коммиты*:
     - `feat(api): реализовать REST API v1 (IBackendApi) и ролевые права доступа IsClient, IsManager, IsAdmin`
   - *Слияние в `develop`*:
     - `git checkout develop && git merge --no-ff feature/US-03-rest-api-v1 -m "Merge pull request #3 from feature/US-03-rest-api-v1: REST API v1 для веб-портала и десктопного АРМ"`

---

### Этап 2 (Веха M4 — Клиентское веб-приложение, Модуль 4)

4. **Ветка `feature/US-01-US-04-US-08-client-web-portal`** (от `develop`):
   - *Файлы*: `backend/apps/web/`, `backend/templates/`, `backend/static/` (`status_poller.js`, `order_form.js`, `main.css`).
   - *Коммиты*:
     - `feat(web): реализовать регистрацию и вход клиента с согласием ПДн и привязкой Telegram (US-08)`
     - `feat(US-01,US-04): реализовать онлайн-заказ, адаптивные шаблоны Bootstrap 5 и StatusPollerJS (5 сек)`
   - *Слияние в `develop`*:
     - `git checkout develop && git merge --no-ff feature/US-01-US-04-US-08-client-web-portal -m "Merge pull request #4 from feature/US-01-US-04-US-08-client-web-portal: Клиентский веб-портал и отслеживание статусов (FTR1, FTR3)"`

---

### Этап 3 (Веха M4 — Десктопное приложение сотрудника и Оборудование, Модули 5 и 6)

5. **Ветка `feature/US-07-hardware-camera-printer`** (от `develop`):
   - *Файлы*: `desktop/hardware/` (`dto.py`, `camera_service.py`, `printer_service.py`).
   - *Коммиты*:
     - `feat(hardware): реализовать UsbCameraService, DocumentCropper (35x45, 50x50) и WindowsPrinterService (US-07)`
   - *Слияние в `develop`*:
     - `git checkout develop && git merge --no-ff feature/US-07-hardware-camera-printer -m "Merge pull request #5 from feature/US-07-hardware-camera-printer: Модули интеграции с USB-фотоаппаратом и принтерами Windows (FTR6, FTR7)"`

6. **Ветка `feature/US-03-US-05-US-06-desktop-client`** (от `develop`):
   - *Файлы*: `desktop/config.py`, `desktop/main.py`, `desktop/api_client/`, `desktop/presenters/`, `desktop/views/`.
   - *Коммиты*:
     - `feat(desktop): реализовать RestApiClient и MVP-презентеры очереди, фото на документы и справочников`
     - `feat(desktop-ui): реализовать интерфейс PySide6 MainWindow для менеджера и администратора (US-03, US-05, US-06, US-07)`
   - *Слияние в `develop`*:
     - `git checkout develop && git merge --no-ff feature/US-03-US-05-US-06-desktop-client -m "Merge pull request #6 from feature/US-03-US-05-US-06-desktop-client: Десктопное приложение сотрудника салона на PySide6 (FTR5, FTR6, FTR7)"`

---

### Этап 4 (Веха M5 — Инфраструктура, Документация запуска и Автотесты PyTest, Модуль 7)

7. **Ветка `feature/FTR-7-deploy-ci-and-run-guide`** (от `develop`):
   - *Файлы*: `deploy/` (`Dockerfile.backend`, `docker-compose.yml`, `nginx/nginx.conf`, `scripts/backup_db.sh`, `pyinstaller/YouPhoto.spec`), `.github/workflows/ci.yml`, гайд по запуску контейнеров и `.exe`.
   - *Коммиты*:
     - `ops(deploy): добавить Docker Compose, Dockerfile, Nginx, скрипт бэкапа БД, PyInstaller spec и GitHub Actions CI`
     - `docs(deploy): задокументировать подробный гайд по запуску Docker-контейнеров и сборке/запуску YouPhoto.exe`
   - *Слияние в `develop`*:
     - `git checkout develop && git merge --no-ff feature/FTR-7-deploy-ci-and-run-guide -m "Merge pull request #7 from feature/FTR-7-deploy-ci-and-run-guide: Контейнеризация Docker, CI/CD, сборка PyInstaller и руководство по запуску"`

8. **Ветка `feature/US-01-US-08-qa-pytest-suite`** (от `develop`):
   - *Файлы*: `tests/conftest.py`, `tests/unit/`, `tests/integration/`, `tests/desktop/` (и `tests/e2e/`).
   - *Коммиты*:
     - `test(qa): реализовать модульные, интеграционные и десктопные автотесты PyTest по сценариям US-01..US-08`
   - *Слияние в `develop`*:
     - `git checkout develop && git merge --no-ff feature/US-01-US-08-qa-pytest-suite -m "Merge pull request #8 from feature/US-01-US-08-qa-pytest-suite: Полное покрытие автотестами PyTest сценариев US-01..US-08"`

---

### Этап 5 (Веха M6 — Релиз `release/v1.0.0` и слияние в `main`)

9. **Ветка `release/v1.0.0-M6`** (от `develop`):
   - Финальная стабилизация, проверка `ruff check .`, `python backend/manage.py migrate`, `pytest`.
   - Слияние релиза в `main` и обратно в `develop` с тегом `v1.0.0`:
     ```powershell
     git checkout main
     git merge --no-ff develop -m "Merge pull request #9 from release/v1.0.0-M6: Релиз первой очереди системы заказов YouPhoto v1.0.0"
     git tag -a v1.0.0 -m "YouPhoto v1.0.0 (Веха M6: Готовность к внедрению)"
     ```

---

## 3. Если удаленный репозиторий (`origin`) подключен к GitHub

- Если в репозитории настроен `git remote` на GitHub и авторизован `gh` CLI:
  - Ветки пушатся в `origin` (`git push -u origin <branch>`), а для каждого слияния создается и мержится Pull Request (`gh pr create --base develop --head <branch> ...` && `gh pr merge --merge`).
- Если работа ведется в локальном Git-репозитории без подключения к внешнему серверу GitHub:
  - Все ветки `main`, `develop`, `feature/*` сохраняются локально в `.git/refs/heads/`, а все слияния в `develop` и `main` выполняются через `git merge --no-ff` с заголовками `Merge pull request #N from feature/...`, формируя эталонный граф истории разработки (`git log --graph --oneline --all`).
- **Важно**: временные файлы, базы данных `db.sqlite3`, `.venv` и кэши не должны попадать в коммиты (контролируется через `.gitignore` и `git status`).
