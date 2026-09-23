# Платформы: чем заменить CUDA на Mac и AMD

Не проверено на живом железе: это заметки о переносе на macOS, AMD и Linux. Проверенная платформа одна — Windows + NVIDIA.

Reelsi писался под Windows + NVIDIA + Adobe. Вопрос «а на маке пойдёт?» не имеет
общего ответа: пайплайн состоит из десятка кусков, и у каждого своя история. Один
переносится бесплатно, другой упирается в то, что нужной библиотеки для этого железа
не существует в природе.

Ниже — разбор по кускам. Статус на август 2026.

## Короткий вывод

| | Windows + NVIDIA | macOS (Apple Silicon) | Windows + AMD | Linux |
|---|---|---|---|---|
| Нарезка, субтитры, XML | полностью | да, местами медленнее | да, с оговорками | да |
| Ротоскоп (RVM) | полностью | заметно медленнее | ~15-25% медленнее CUDA | как AMD/NVIDIA |
| Черновой рендер | NVENC | VideoToolbox, равноценно | AMF, чуть хуже качеством | VAAPI/NVENC |
| Premiere / After Effects | да | **да** | да | **Adobe нет вообще** |

Главный вывод не про GPU: **на Linux ломается не вычисление, а выход пайплайна** —
Adobe там не существует. Зато FCP7 XML, который Reelsi и генерирует, импортирует
DaVinci Resolve, а он есть под Linux. То есть на Linux осмысленна нарезка с
субтитрами в XML, но не сборка графики в After Effects.

На Mac ровно наоборот: Adobe на месте, а спотыкается вычислительная часть.

---

## 1. Транскрипция — самое больное место

Сейчас: **faster-whisper** поверх CTranslate2, CUDA + float16, и **whisper.cpp** как
дополнительный движок (Metal на Mac, Vulkan на AMD) — см. ниже «Сделано».

**CTranslate2 не поддерживает ни Metal, ни ROCm.** Это не «медленнее» — этого просто
нет, и на Mac faster-whisper считает на CPU. Ускорить его на не-NVIDIA нельзя никак,
только менять движок.

Замены:

- **whisper.cpp** — Metal и Core ML на Apple Silicon, порядка 10× реального времени на
  large-v3. На AMD собирается с Vulkan-бэкендом. Питон-зависимостей не тянет вообще,
  зовётся как отдельный бинарник — по нашей архитектуре это плюс: `core/asr_backends.py` уже
  реестр движков, добавляется ещё одним.
- **MLX Whisper** (`lightning-whisper-mlx`) — только Apple Silicon, зато нативно под их
  unified memory.

Работы: новый бэкенд в `core/asr_backends.py` + `core/transcribe.py`. Архитектура под это уже
готова — движки регистрируются через `data/asr_engines.json`.

**Сделано (2026-08-09, ветка `feat/whisper-cpp`):** движки `whisper.cpp:large-v3 /
medium / small` в `asr_backends.py`, модуль `whisper_cpp.py` (бинарник и ggml-модели
с HF, отдельный процесс, `doctor.py`). Установка: `brew install whisper-cpp` /
`scoop install whisper-cpp` / apt, либо бинарник в `~/.reelsi/whisper_cpp/bin`
(путь переопределяется `REELSI_WHISPER_CLI`, папка — `REELSI_WHISPER_CPP`).
Проверено вживую на Windows (v1.9.2, CPU, jfk.wav → 22 слова, тайминги верные).
Единицы таймкодов между версиями плавают (v1.9+ — миллисекунды, старые — тики) —
калибруются автоматически. Пословных таймингов в JSON v1.9.2 нет вовсе (только
через DTW) — слова натягиваются на сегменты интерполяцией. Осталось проверить
сам Metal на живом Mac.

## 2. GigaAM, Qwen2.5-Omni, forced-align, детектор вздохов

Сейчас: обычный PyTorch с `.cuda()`.

Это переносится дешевле всего — PyTorch есть на всех трёх платформах:

- **Apple Silicon — MPS.** Нужен `PYTORCH_ENABLE_MPS_FALLBACK=1`: MPS покрывает не все
  операции, и без этой переменной инференс падает на первой неподдержанной. Отдельно
  важно: **MPS требует, чтобы модель целиком влезла в unified memory** — `device_map="auto"`
  не умеет выгружать слои на CPU, как на CUDA. Для Omni это ограничение жёсткое.
- **AMD — ROCm.** С ноября 2025 PyTorch на Windows с ROCm официально существует (сейчас
  ROCm 7.2.1) для Radeon RX 7000/9000 и части Ryzen AI, но это public preview, не вся
  ROCm-стека. На Linux — зрелее. Отставание от CUDA порядка 15-25%.
- **DirectML** — рабочий, но **в maintenance mode**, новых функций не будет; развитие
  ушло в WinML. Как запасной вариант для старых Radeon годится, как ставку — нет.

Сделано: единый `device.py:pick_device` (`cuda → mps → cpu`) подключён в
`core/roto.py`/`core/breath.py`/`core/ctc_asr.py`/`core/falign.py`. Осталось: `core/asr_backends.py` —
дефолт `device="cuda"` у CTC-движков (на Mac/AMD править `data/asr_engines.json` вручную).

## 3. Ротоскоп (RVM)

Сейчас: Robust Video Matting через `torch.hub`, CUDA fp16, NVDEC-декод.

Модель обычная свёрточная, на MPS и ROCm пойдёт. Но это самая тяжёлая часть пайплайна,
и она же сильнее всех просядет: на Mac fp16 на MPS работает, однако скорости
десктопной NVIDIA там нет. Реалистично — рабочий, но не «фоном, пока пьёшь кофе».

Отдельно: `_INTERNAL_PX` и `SEQ_CHUNK` подбирались под VRAM NVIDIA. На unified memory
Mac делить память с системой придётся иначе — константы надо перекалибровать, иначе
вместо честной ошибки получишь своп.

## 4. Черновой рендер и прокси камер

Сейчас: `h264_nvenc`, `-hwaccel cuda`, `scale_cuda`, фолбэк на `libx264`.

Здесь всё хорошо, потому что ffmpeg абстрагирует железо:

- **macOS — VideoToolbox** (`h264_videotoolbox`, `hevc_videotoolbox`), отдельный
  медиадвижок Apple Silicon. По качеству конкурентоспособен NVENC.
- **AMD Windows — AMF** (`h264_amf`). Качество немного уступает NVENC, но приемлемо.
- **AMD/Intel Linux — VAAPI**.

Сделано: `_CANDIDATES` по платформе в `core/draftrender.py` (Darwin=videotoolbox,
Windows/Linux=nvenc/amf/qsv) поверх механики «попробовать кодек, при неудаче
следующий» (`tries`, `_nvenc_probe`); `scale_cuda` на не-NVIDIA заменяется
обычным `scale` — фолбэк `vf_cpu` написан. Осталось: VAAPI (Intel/AMD Linux) —
требует `-vaapi_device`, в `_CANDIDATES` пока нет.

## 5. rembg (удаление фона у сгенерированных картинок)

Сейчас: onnxruntime.

Переносится провайдером исполнения, кода не касается:

- **macOS — CoreML EP** (CPU + GPU + Neural Engine).
- **Windows AMD — DirectML EP**.

Подвох установки: `onnxruntime`, `onnxruntime-gpu`, `onnxruntime-directml` — это один и
тот же импортируемый модуль. **В одном venv их мешать нельзя.** Это надо явно написать
в инструкции по установке, иначе человек ставит второй пакет поверх первого и получает
необъяснимые падения. (Пока не написано в install.ps1/install.sh — TODO.)

## 6. Синхронизация камер, VAD, сборка XML

scipy, numpy, silero-vad, генерация FCP7 XML и `.jsx` — чистый CPU. Работает везде
одинаково, трогать нечего.

## 7. Выход пайплайна

- **macOS** — Premiere и After Effects есть, ExtendScript работает. Единственная
  оговорка — шрифт SF Pro на маке уже стоит системно, то есть субтитры там как раз
  соберутся правильнее, чем на чистой Windows. Безголовый рендер доступен только на
  Windows: поиск After Effects в doctor идёт по `%ProgramFiles%\Adobe` (см.
  `core/aerender.py:find_ae`), на маке рендер собирается вручную из интерфейса.
- **Linux** — Adobe нет и не будет. `.jsx` генерировать бессмысленно. Но FCP7 XML
  открывается в **DaVinci Resolve**, который под Linux есть, — значит нарезка с
  субтитрами-SRT остаётся полезной. Стоит проверить на реальном XML: наша разметка
  использует специфику Premiere, и часть её Resolve может не понять.

---

## Что делать по порядку

1. **Единый `pick_device()`** (`cuda → mps → cpu`) вместо хардкода в пяти файлах.
   Дешёво, ничего не ломает на Windows, сразу оживляет Mac для GigaAM/RVM/align.
2. **Список кодеков по платформе** в `core/draftrender.py` поверх существующего механизма
   фолбэка.
3. **whisper.cpp как ASR-бэкенд** — самая крупная задача и единственный способ дать
   маку и AMD быструю транскрипцию. Сделано 2026-08-09 (см. раздел 1); осталась
   проверка Metal на живом Mac.
4. **`doctor.py`**, который честно печатает: какое железо найдено, какой бэкенд будет
   использован и что именно недоступно.
5. Провайдеры onnxruntime и предупреждение про смешивание пакетов — в инструкцию.

## Чего здесь нет

Ни одна строчка выше не проверена на живом Mac или Radeon — у нас нет этого железа.
Это разбор того, что технически возможно и какой ценой, а не отчёт о работающем
портировании. Прежде чем обещать поддержку в README, нужен реальный прогон: как
минимум нарезка одного ролика и один ротоскоп на каждой платформе.

Источники (август 2026): [faster-whisper issue про Metal/MPS](https://github.com/SYSTRAN/faster-whisper/issues/515),
[сравнение faster-whisper и whisper.cpp](https://codersera.com/blog/faster-whisper-vs-whisper-cpp-speech-to-text-2026/),
[AMD: PyTorch на Radeon под Windows](https://www.amd.com/en/blogs/2025/the-road-to-rocm-on-radeon-for-windows-and-linux.html),
[ROCm on Radeon — матрица совместимости](https://rocm.docs.amd.com/projects/radeon-ryzen/en/latest/docs/compatibility/compatibilityrad/windows/windows_compatibility.html),
[HF: PyTorch на Apple Silicon (MPS)](https://huggingface.co/docs/transformers/en/perf_train_special),
[DirectML в maintenance mode](https://github.com/microsoft/DirectML),
[ffmpeg: аппаратное ускорение](https://deepwiki.com/FFmpeg/FFmpeg/7-hardware-acceleration),
[ONNX Runtime CoreML EP](https://onnxruntime.ai/docs/execution-providers/CoreML-ExecutionProvider.html).
