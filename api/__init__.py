# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Maxim Si
"""Reelsi — общий Flask-бэкенд (все /api/*-роуты, JOB/LOCK, джобы).

Вынесен из webui.py 2026-07-17 (старый UI на порту 5000 был переходным и удалён
2026-07-22; файл интерфейса с 2026-08-09 снова называется webui.py —
историческое имя webui2.py). Фронт подключает бэкенд так:

    from api import bp
    app.register_blueprint(bp)

- webui.py (порт 5001) — единственный UI (templates/ + static/).

С 2026-08-06 это пакет, а не один файл на 2617 строк. Снаружи не изменилось ничего:
`import api` и `from api import bp, DEFAULT_BASE` работают как работали, роуты те же и
по тем же адресам. Внутри:

| модуль     | что там                                                        |
|------------|----------------------------------------------------------------|
| `_core`    | Blueprint, JOB/LOCK/emit, job_start/finish, локи, общие пути    |
| `jobs`     | джобы нарезки: /api/run, /api/omnicut_run, /api/cancel, /status |
| `files`    | камеры, нативные диалоги выбора, /api/media, /api/ui_state      |
| `presets`  | пресеты стиля, профили спикеров, термины                       |
| `ai`       | профили провайдеров, ключи, модели, одиночные ИИ-вызовы         |
| `editor`   | слова, субтитры, редактор нарезки                               |
| `build`    | сборка .jsx для AE и раскладка камер                            |
| `inserts`  | база вставок                                                    |
| `previewproxy` | превью-прокси камер: /api/preview_proxy, /api/preview_proxy_status |
| `render` | безголовый рендер в AE (задание BD): /api/render_run, /api/render_status |
| `gdrive` | скачивание с гугл-диска по ссылке через rclone (задание G)        |
| `videogen` | вкладка «Видео»                                                 |

Новый эндпоинт пишется в тот модуль, к чьей теме относится. Новая тема — новый модуль
плюс строка импорта здесь: роут регистрируется самим фактом импорта модуля, забыть её
значит получить 404 на живом коде.

Порядок импортов ниже — это порядок зависимостей, а не алфавит: `_core` первым (он
кладёт корень репозитория в sys.path, без этого не найдётся ни reelsi, ни aicut), за
ним модули, которые ни от кого не зависят, и только потом те, кто тянет соседей.
"""
from . import _core                                   # noqa: F401  (первым — sys.path)
from . import editor, files, gdrive, inserts, presets, previewproxy, render, videogen   # noqa: F401
from . import ai, build, jobs                         # noqa: F401

# Имена, которые снаружи берут прямо из `api` — webui.py и тесты. Всё остальное
# доступно через свой модуль (`api.editor.api_words`), и специально сюда не тащится:
# пакет затем и заводили, чтобы не держать одну плоскую свалку имён.
from ._core import (                                  # noqa: F401
    bp, DEFAULT_BASE, HERE, UI_STATE_PATH, JOB, LOCK, emit,
    job_start, job_finish, set_progress,
    _host_is_local, _origin_is_local, _block_dns_rebinding,
)
from .editor import _ensure_project                   # noqa: F401
from .inserts import _adopt_inserts                   # noqa: F401
from .videogen import VJOB, vemit, vhist_put, vhist_boot   # noqa: F401
