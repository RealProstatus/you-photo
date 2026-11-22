# 06. Гайдлайн модулей: Camera & Printer Integration (`desktop/hardware/`)

**Основание**: `camera_module.puml`, `printer_module.puml`, сценарий `US-07`, требования `U-M-05`, `U-M-06`, `S-ENV-07`, `S-INT-02`, `FUNC 6.1..7.1`.

---

## 1. Структура пакета

```text
desktop/hardware/
├── __init__.py
├── dto.py             # DocumentStandard, ImportedPhoto, PrinterInfo, PrintJob, PrintResult
├── camera_service.py  # ICameraService, UsbCameraService, DocumentCropper
└── printer_service.py # IPrinterService, WindowsPrinterService
```

---

## 2. Модуль интеграции с фотоаппаратом (`camera_module.puml`)

### 2.1. Перечисление `DocumentStandard` и DTO `ImportedPhoto` (`dto.py`)
- `DocumentStandard`:
  - `RF_PASSPORT_35x45` — Паспорт РФ (35×45 мм, 413×531 px при 300 DPI)
  - `FOREIGN_PASSPORT_35x45` — Загранпаспорт (35×45 мм, 413×531 px при 300 DPI)
  - `SCHENGEN_VISA_35x45` — Виза Шенген (35×45 мм, 413×531 px при 300 DPI)
  - `US_VISA_50x50` — Виза США (50×50 мм, 591×591 px при 300 DPI)
- `ImportedPhoto` (`dataclass(frozen=True)`):
  - `local_file_path: str`
  - `width_px: int`
  - `height_px: int`
  - `captured_at: datetime`

### 2.2. `DocumentCropper` (`camera_service.py`)
- `dpi_target: int = 300`
- `detect_face_bounds(image_path: str) -> tuple[int, int, int, int]`:
  - Определяет область кадрирования по центру/шаблону пропорций выбранного документа.
- `crop_and_resize(image_path: str, standard: DocumentStandard, crop_box: tuple[int, int, int, int] = None) -> str`:
  - Выполняет кадрирование изображения через `Pillow` (по автоматическому шаблону или по переданным координатам ручной рамки менеджера `US-07 шаг 5`), масштабирует под целевой размер стандарта при `300 DPI` и сохраняет готовый файл во временную директорию.

### 2.3. `ICameraService` и `UsbCameraService` (`camera_service.py`)
- Методы `ICameraService`:
  - `is_camera_connected() -> bool`
  - `import_latest_photo() -> ImportedPhoto`
  - `crop_for_document(photo_path: str, standard: DocumentStandard, crop_box: tuple = None) -> str`
- Реализация `UsbCameraService`:
  - Поддерживает работу через стандартные средства Windows (WIA / съемные USB PTP/MTP-устройства и DCIM-каталоги подключенных камер) + **режим тестового стенда/эмулятора** (`mock_mode` или указание папки импорта USB-камеры), чтобы сценарий `US-07` и исключение `3a` (камера отключена / файл поврежден) детерминированно проверялись автотестами `PyTest`.
  - Если камера не подключена (`is_camera_connected() == False`), метод `import_latest_photo()` выбрасывает `CameraConnectionError("Фотоаппарат не обнаружен на USB-порту. Проверьте подключение кабеля и повторите попытку.")` (`U-M-05`, `US-07 3a`).

---

## 3. Модуль интеграции с принтерами (`printer_module.puml`)

### 3.1. DTO принтера (`dto.py`)
- `PrinterInfo` (`dataclass(frozen=True)`):
  - `name: str`
  - `is_default: bool`
  - `is_online: bool`
  - `supported_formats: list[str]`
- `PrintJob` (`dataclass(frozen=True)`):
  - `file_path: str`
  - `printer_name: str`
  - `width_cm: float`
  - `height_cm: float`
  - `copies: int`
- `PrintResult` (`dataclass(frozen=True)`):
  - `is_success: bool`
  - `spooler_job_id: int`
  - `error_message: str`

### 3.2. `IPrinterService` и `WindowsPrinterService` (`printer_service.py`)
- Методы `IPrinterService`:
  - `list_available_printers() -> list[PrinterInfo]`
  - `send_to_print(job: PrintJob) -> PrintResult`
  - `get_printer_status(printer_name: str) -> bool`
- Реализация `WindowsPrinterService`:
  - Получает список установленных принтеров Windows через `QtPrintSupport.QPrinterInfo.availablePrinters()` и/или `win32print.EnumPrinters`.
  - При вызове `send_to_print(job: PrintJob)`:
    1. Проверяет существование файла `job.file_path`, валидность `copies >= 1` и статус принтера `get_printer_status(job.printer_name)`.
    2. Если принтер недоступен/остановлен (`Исключение 8a` в `US-07`, `U-M-06`), возвращает `PrintResult(is_success=False, spooler_job_id=0, error_message="Принтер недоступен или остановлен. Проверьте питание и драйвер принтера.")`.
    3. Настраивает размер страницы (`width_cm × height_cm`), количество копий (`copies`) через `QPrinter` / `QPainter` (или спулер `win32print` / тестовый виртуальный принтер на стенде) и ставит задание в очередь печати, возвращая `PrintResult(is_success=True, spooler_job_id=..., error_message="")`.
