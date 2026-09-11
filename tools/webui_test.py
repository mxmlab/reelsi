# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Maxim Si
"""Изолированный профиль webui для отладки — порт 5098.

То же приложение, что и `webui.py`, но со своими файлами состояния: своё
`ui_state`, свой лок джоба, свой `ai_config`, свой индекс базы вставок и своя
папка генерации видео (в ней же история видео-задач). Нужен,
чтобы проверка интерфейса не лезла в боевые данные: страница зеркалит состояние
на сервер сама, и вторая копия на обычных файлах затирала бы юзеру очередь и
папки камер.

Конфиг и индекс при первом запуске КОПИРУЮТСЯ с рабочих — без ключей и с пустой
базой вставок профиль бесполезен. Дальше они живут отдельно (`*.test.json`).

Заводя новый файл состояния, добавь ему такую же переменную окружения и строку
здесь: без этого «изолированный» профиль правит боевые данные (на insertlib.json
уже попадались).

    python tools/webui_test.py       ->  http://127.0.0.1:5098/
"""
import os, shutil, sys

# Скрипт живёт в tools/, а репозиторий — на уровень выше: без корня в sys.path
# не найдётся ни core.paths, ни webui.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from core import paths   # noqa: E402


def _own(env, name, seed_from=None):
    """Свой файл состояния для профиля; seed_from — с чего скопировать при первом
    запуске (ключи провайдеров, индекс базы вставок)."""
    path = paths.root(name)
    if seed_from and not os.path.exists(path) and os.path.isfile(seed_from):
        shutil.copy2(seed_from, path)
    os.environ[env] = path


_own("REELSI_UI_STATE", "ui_state.test.json")
_own("REELSI_JOB_LOCK", "job.test.lock")
_own("REELSI_AI_CONFIG", "ai_config.test.json", paths.root("ai_config.json"))
_own("REELSI_AI_LOG", "ai_calls.test.json")
_own("REELSI_MODELS_DEV", "models_dev.test.json")
_own("REELSI_INSERTLIB", "insertlib.test.json", paths.root("insertlib.json"))
_own("REELSI_TERMS", "terms.test.json", paths.root("terms.json"))
_own("REELSI_RENDER_STATS", "render_stats.test.json", paths.root("render_stats.json"))
# списки цензуры: свои только если юзер их правил (иначе профиль работает по общим
# badwords.txt/okwords.txt из поставки — им ничего не грозит, их правка сюда не пишет)
_own("REELSI_BADWORDS", "badwords.test.txt", paths.root("badwords.user.txt"))
_own("REELSI_OKWORDS", "okwords.test.txt", paths.root("okwords.user.txt"))
# своя папка генерации видео (в ней же лежит история задач): иначе профиль показывал
# бы боевые ролики, а кнопка «Убрать» удаляла бы их файлы
os.environ["REELSI_VIDEO_DIR"] = paths.root("_videogen.test")
os.environ.setdefault("PORT", "5098")
os.environ.setdefault("REELSI_NO_BROWSER", "1")

import webui  # noqa: E402  (импорт строго после env — модули читают их на старте)

if __name__ == "__main__":
    port = int(os.environ["PORT"])
    print(f"Reelsi Web UI (ИЗОЛИРОВАННЫЙ профиль) -> http://127.0.0.1:{port}")
    webui.app.run(port=port, threaded=True, use_reloader=False)
