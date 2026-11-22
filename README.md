# Система заказов сети фотосалонов «YouPhoto»

Репозиторий первой очереди проекта **«Система заказов салона YouPhoto»** (ННГУ им. Н.И. Лобачевского, ИИТММ, курс «Проектирование и архитектура программных систем», 2026).

---

## 🏗️ Архитектура и состав модулей

Система разделена на **7 модулей** в соответствии с `system_overview.puml` и функциональной спецификацией (`FTR1–FTR7`, сценарии `US-01..US-08`):

| № | Модуль | Технологический стек | Путь в репозитории | Гайдлайн разработки |
|---|--------|----------------------|--------------------|---------------------|
| 1 | **Backend Core** | Python 3.11+, Django 5 + DRF, PostgreSQL | `backend/apps/core/`, `backend/api/` | [docs/guidelines/01_BACKEND_CORE.md](file:///c:/Users/notebook/UNN/you-photo/docs/guidelines/01_BACKEND_CORE.md) |
| 2 | **File Storage** | MinIO (S3) + `django-storages`, Pillow/HEIF | `backend/apps/storage/` | [docs/guidelines/02_FILE_STORAGE.md](file:///c:/Users/notebook/UNN/you-photo/docs/guidelines/02_FILE_STORAGE.md) |
| 3 | **Notification Service** | Apprise + Telegram Bot API | `backend/apps/notifications/` | [docs/guidelines/03_NOTIFICATION_SERVICE.md](file:///c:/Users/notebook/UNN/you-photo/docs/guidelines/03_NOTIFICATION_SERVICE.md) |
| 4 | **Client Web App** | Django Templates, Bootstrap 5, JS Polling | `backend/apps/web/`, `backend/templates/`, `backend/static/` | [docs/guidelines/04_CLIENT_WEB_APP.md](file:///c:/Users/notebook/UNN/you-photo/docs/guidelines/04_CLIENT_WEB_APP.md) |
| 5 | **Employee Desktop App** | Python 3.11+, PySide6 (MVP), REST Client | `desktop/` | [docs/guidelines/05_DESKTOP_CLIENT.md](file:///c:/Users/notebook/UNN/you-photo/docs/guidelines/05_DESKTOP_CLIENT.md) |
| 6 | **Camera & Printer Hardware** | USB PTP/MTP/WIA, Win32Print / QtPrintSupport | `desktop/hardware/` | [docs/guidelines/06_HARDWARE_CAMERA_PRINTER.md](file:///c:/Users/notebook/UNN/you-photo/docs/guidelines/06_HARDWARE_CAMERA_PRINTER.md) |
| 7 | **QA, Testing & DevOps** | PyTest (`pytest-django`, `pytest-qt`), Docker, Nginx | `tests/`, `deploy/`, `.github/workflows/` | [docs/guidelines/07_QA_TESTING_DEVOPS.md](file:///c:/Users/notebook/UNN/you-photo/docs/guidelines/07_QA_TESTING_DEVOPS.md) |

Главный архитектурный индекс и контракты взаимодействия модулей: [docs/guidelines/00_ARCHITECTURE_OVERVIEW.md](file:///c:/Users/notebook/UNN/you-photo/docs/guidelines/00_ARCHITECTURE_OVERVIEW.md).
Регламент работы с проектом и репозиторием (MSF, Git Flow, управление конфигурацией, изменениями и релизами): [docs/guidelines/08_REPOSITORY_AND_WORKFLOW.md](file:///c:/Users/notebook/UNN/you-photo/docs/guidelines/08_REPOSITORY_AND_WORKFLOW.md).
Регламент фиксации истории разработки в Git Flow (коммиты, ветки `feature/*`, Pull Request / Merge Request граф по вехам): [docs/guidelines/09_GIT_FLOW_AND_PR_HISTORY.md](file:///c:/Users/notebook/UNN/you-photo/docs/guidelines/09_GIT_FLOW_AND_PR_HISTORY.md).

---

## 📁 Структура файловой базы

```text
you-photo/
├── .env.example                 # Шаблон переменных окружения
├── .gitignore                   # Исключения Git (вкл. секреты по 152-ФЗ)
├── pyproject.toml               # Конфигурация PyTest и Ruff
├── requirements.txt             # Зависимости серверной части и тестов
├── requirements-desktop.txt     # Зависимости Windows-клиента сотрудника (PySide6)
├── docs/
│   └── guidelines/              # Детальные гайдлайны по каждому из 7 модулей
├── backend/                     # Серверная часть и веб-приложение клиента (Django)
│   ├── manage.py
│   ├── config/                  # Настройки Django, URL-маршрутизация, WSGI/ASGI
│   ├── api/                     # REST API (IBackendApi) для Веб-поллинга и Desktop АРМ
│   ├── apps/
│   │   ├── core/                # Модуль 1: Модели (User, Salon, Equipment, Service, Order...) и сервисы (OrderService, RoutingService, PricingService)
│   │   ├── storage/             # Модуль 2: IFileStorage, MinioFileStorage, FileValidator, RetentionCleanupJob (30 дней)
│   │   ├── notifications/       # Модуль 3: INotificationService, AppriseNotificationService, TelegramBotBinder
│   │   └── web/                 # Модуль 4: AuthController, OrderWebController, OrderDraftForm
│   ├── templates/               # Шаблоны Bootstrap 5 (Русский интерфейс S-USE-01)
│   └── static/                  # CSS и JS (StatusPollerJS — опрос каждые 5 сек)
├── desktop/                     # Десктопное приложение сотрудника (Windows / PySide6)
│   ├── main.py                  # Точка входа YouPhoto.exe
│   ├── api_client/              # RestApiClient (реализация IBackendApi по HTTPS/JSON)
│   ├── presenters/              # OrderQueuePresenter, DocumentPhotoPresenter, SalonAdminPresenter
│   ├── views/                   # MainWindow и вкладки очереди, фото на документы, справочников
│   └── hardware/                # Модули 6 и 7: UsbCameraService, DocumentCropper, WindowsPrinterService
├── deploy/                      # Docker, Docker Compose, Nginx, скрипты бэкапа БД
└── tests/                       # Автотесты PyTest (unit, integration US-01..US-08, desktop, security)
```
