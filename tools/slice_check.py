# -*- coding: utf-8 -*-
# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Maxim Si
r"""Прогон публичного среза через весь CI, а не только через тесты.

Зачем. Тесты гоняются в приватном дереве, а публикуется срез без файлов из
`.publicignore`. Пока здесь запускался один `python -m pytest tests -q`, между
«локально зелено» и «в CI зелено» оставалась щель: pytest не видит ни ключ,
подставленный в тест (его ловит gitleaks), ни нечитаемый в cp1252
`requirements-dev.txt` (его ловит разбор требований pip'ом). Обе поломки прошли
ровно в эту щель — и всплыли уже после выпуска среза. Поэтому после тестов на том
же дереве идут остальные проверки `.github/workflows/ci.yml`, которые можно
выполнить локально.

Шаги в порядке прогона (имена — в `STEP_NAMES`, подсказки — в `STEP_HINTS`):
pytest; ruff; mypy (конфигурация берётся из `pyproject.toml` среза); jsx
(`node --check` по ExtendScript и по `static/app/*.js`); smoke (`compileall` и
`--help` точек входа); requirements (разбор требований pip'ом и чтение их в
системной кодировке); gitleaks (только дерево среза); linux (тесты на Linux в docker с
--init по ssh). Каждый шаг печатает имя и итог, в конце — сводная таблица.
Код возврата ненулевой, если хоть один шаг провален или не прогонялся без явного
флага пропуска; провал самих тестов отдаёт их собственный код.

Чего здесь нет по сравнению с CI и почему: pip-audit (тянет сеть и базу
уязвимостей), история публичного репозитория у gitleaks (сканируется только
дерево среза — историю смотрит джоба `scan`), `reelsi --help` из установленного
пакета (нужна `pip install -e .`).

Правила отбора файлов НЕ дублируются: `.publicignore` читается и разбирается
функциями `tools/public_slice.py` (`IGNORE_FILE`, `parse_ignore`, `is_ignored`).
`build_slice` для этого не годится: он собирает КОММИТ и пишет объекты в `.git`
приватного репозитория, а нужен каталог с файлами — «ничего не писать внутрь
репозитория» здесь обязательное условие.

Внутри каталога среза заводится свой git (`git init` + `git add -A`): часть
набора читает `git ls-files` (`test_docs_links.py`, `test_layout.py`,
`test_public_clean.py`, `test_review_fixes.py`), а в публичном репозитории это
клон — без `.git` эти тесты падают с кодом 128 не по делу. Свой `.git` заодно
прекращает поиск репозитория вверх по дереву: каталог среза может оказаться и
внутри другой рабочей копии (`TEMP` иногда указывает в дерево), а тогда тесты
увидели бы чужие приватные файлы.

Интерфейс: `--ref`, `--keep`, `--root` — как было; `--only ШАГ` гоняет один шаг
при разборе; `--skip ШАГ` пропускает шаг явно; бинарник gitleaks берётся из
`--gitleaks PATH` или `$GITLEAKS`, а `--no-gitleaks` — явный пропуск с громкой
строкой в выводе; удалённый хост для шага linux берётся из `--linux-ssh USER@HOST`
или `$REELSI_LINUX_SSH`, образ — `--linux-image` (по умолчанию `reelsi-ci:py310`),
а `--no-linux` — явный пропуск.

Код возврата: 0 — все шаги прошли или пропущены явно; иначе ненулевой (при
провале тестов — их код, как и раньше); не собрался срез — 2.
"""
import argparse
import io
import os
import re
import shutil
import stat
import subprocess
import sys
import tarfile
import tempfile
from functools import partial
from typing import Any, Callable

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import public_slice  # noqa: E402

if sys.stdout and hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
if sys.stderr and hasattr(sys.stderr, "reconfigure"):
    try:
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

PYTEST_ARGS = ["-m", "pytest", "tests", "-q"]
TAIL_LINES = 15                     # сколько последних строк вывода команды печатать
TREE_PREFIX = "reelsi_slice_"       # префикс временного каталога среза
JSX_TMP_PREFIX = "reelsi_jsx_"      # каталог копий `.jsx` -> `.js` для `node --check`
TIMEOUT_S = 1800                    # потолок на одну команду: прогон не должен встать намертво
TIMEOUT_CODE = 124                  # коды, которые run_command отдаёт вместо исключений
NO_BINARY_CODE = 127
ENV_GITLEAKS = "GITLEAKS"
GITLEAKS_CONFIG = ".gitleaks.toml"
ENV_LINUX_SSH = "REELSI_LINUX_SSH"
DEFAULT_LINUX_IMAGE = "reelsi-ci:py310"
REQUIREMENTS_FILES = ("requirements.txt", "requirements-optional.txt", "requirements-dev.txt")
COMPILE_PATHS = ("api", "core", "tools", "tests", "webui.py", "reelsi.py", "doctor.py")
CLI_HELP = (("reelsi.py",), ("-m", "core.omni_cut"), ("-m", "core.gigaam_cut"))

# Состояния шага. «Не прогонялся» красит код возврата наравне с FAIL: молча
# пропущенная проверка — это ровно та щель, из-за которой скрипт и переписан.
OK = "OK"
FAIL = "FAIL"
NOTRUN = "НЕ ПРОГОНЯЛСЯ"
SKIP = "ПРОПУЩЕН"

STEP_NAMES = ("pytest", "ruff", "mypy", "jsx", "smoke", "requirements", "gitleaks", "linux")

STEP_HINTS = {
    "pytest": "python -m pytest tests -q",
    "ruff": "ruff check .",
    "mypy": "mypy (список модулей — из pyproject.toml среза)",
    "jsx": "node --check по ExtendScript и static/app/*.js",
    "smoke": "compileall и --help точек входа",
    "requirements": "pip install --dry-run по requirements*.txt и чтение их в системной кодировке",
    "gitleaks": "gitleaks dir --redact -c .gitleaks.toml",
    "linux": "pytest в docker с --init по ssh на Linux-хосте",
}

LOCALE_CHECK = (
    "import locale; enc = locale.getpreferredencoding(False); "
    "files = ('requirements.txt','requirements-optional.txt','requirements-dev.txt'); "
    "[open(f, encoding=enc).read() for f in files]; "
    "[open(f, encoding='utf-8-sig').read() for f in files]"
)

# Сеть в шаге требований: pip без индекса ничего не проверит, и делать вид, что
# шаг прошёл, нельзя — сообщаем и оставляем его непройденным.
NETWORK_MARKERS = (
    "Could not fetch URL",
    "Network is unreachable",
    "Temporary failure in name resolution",
    "getaddrinfo failed",
    "Failed to establish a new connection",
    "ProxyError",
    "ConnectionError",
    "Read timed out",
)


class StepResult:
    """Итог шага: имя, состояние, пояснение и вывод команды (для разбора падений)."""

    def __init__(self, name: str, status: str, detail: str = "", code: int = 0, output: str = ""):
        self.name = name
        self.status = status
        self.detail = detail
        self.code = code
        self.output = output

    @property
    def failed(self) -> bool:
        """Красит ли шаг код возврата: FAIL и «не прогонялся» — да, пропуск по флагу — нет."""
        return self.status in (FAIL, NOTRUN)


def _text(value: Any) -> str:
    """Вывод команды строкой: TimeoutExpired отдаёт то str, то bytes."""
    if not value:
        return ""
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return value


def run_command(args: Any, cwd: str, env: dict[str, str] | None = None,
                timeout: int = TIMEOUT_S,
                input: bytes | str | None = None) -> subprocess.CompletedProcess[Any]:
    """Единственная точка запуска внешних команд: её и подменяют тесты.

    Таймаут и отсутствие программы возвращаются таким же CompletedProcess с
    отдельными кодами: шаг не должен ни вешать прогон, ни падать трейсбеком.
    """
    cmd = [str(a) for a in args]
    try:
        if input is not None:
            input_bytes = input.encode("utf-8") if isinstance(input, str) else input
            res = subprocess.run(
                cmd,
                cwd=cwd,
                input=input_bytes,
                capture_output=True,
                env=env,
                timeout=timeout,
            )
            return subprocess.CompletedProcess(
                cmd,
                res.returncode,
                _text(res.stdout),
                _text(res.stderr),
            )
        return subprocess.run(
            cmd,
            cwd=cwd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            env=env,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired as e:
        return subprocess.CompletedProcess(
            cmd, TIMEOUT_CODE, _text(e.stdout), f"таймаут {timeout} с: {' '.join(cmd)}")
    except OSError as e:
        return subprocess.CompletedProcess(cmd, NO_BINARY_CODE, "", f"команда не запустилась: {e}")


def find_tool(name: str) -> str | None:
    """Путь к внешней программе. Отдельной функцией — чтобы тесты не зависели от PATH машины."""
    return shutil.which(name)


def _git_text(args: list[str], cwd: str) -> subprocess.CompletedProcess:
    """git-команда с текстовым выводом."""
    return subprocess.run(
        ["git", *args],
        cwd=cwd,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )


def _detect_root() -> str:
    """Корень рабочей копии — как в `public_slice.main`, чтобы скрипт шёл из любого каталога."""
    try:
        res = _git_text(["rev-parse", "--show-toplevel"], os.getcwd())
        if res.returncode == 0 and res.stdout.strip():
            return res.stdout.strip()
    except OSError:
        pass
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def slice_entries(root: str, ref: str = "HEAD") -> list[tuple[str, str, str]]:
    """Файлы среза из `ref`: [(mode, blob_sha, path)].

    Отбор ровно как в `public_slice.build_slice`: `.publicignore` из того же коммита и
    `is_ignored` по каждому пути; сабмодуль — ошибка (в срез он не едет). Размер файла
    здесь не нужен: лимит GitHub проверяет `public_slice.py --check`.
    """
    res_ref = _git_text(["rev-parse", "--verify", f"{ref}^{{commit}}"], root)
    if res_ref.returncode != 0:
        raise public_slice.SliceGitError(f"Коммит не найден: {ref}")

    res_ign = _git_text(["show", f"{ref}:{public_slice.IGNORE_FILE}"], root)
    patterns = public_slice.parse_ignore(res_ign.stdout) if res_ign.returncode == 0 else []

    res_tree = subprocess.run(
        ["git", "ls-tree", "-r", "-l", "-z", ref],
        cwd=root,
        capture_output=True,
    )
    if res_tree.returncode != 0:
        raise public_slice.SliceGitError(f"Ошибка git ls-tree: {res_tree.stderr.decode(errors='replace')}")

    entries = []
    for item in res_tree.stdout.split(b"\0"):
        if not item:
            continue
        parts = item.split(b"\t", 1)
        if len(parts) != 2:
            continue
        meta, path_b = parts
        path = path_b.decode("utf-8", errors="replace")
        meta_parts = meta.split()
        if len(meta_parts) < 4:
            continue
        mode = meta_parts[0].decode("ascii", errors="replace")
        obj_type = meta_parts[1].decode("ascii", errors="replace")
        sha = meta_parts[2].decode("ascii", errors="replace")

        if obj_type == "commit":
            raise public_slice.SliceGitError(f"Обнаружен сабмодуль в срезе (запрещено): {path}")

        if public_slice.is_ignored(path, patterns):
            continue

        entries.append((mode, sha, path))
    return entries


def _write_symlink(target: str, link_to: str) -> None:
    """Симлинк среза — симлинком; где прав нет (Windows без режима разработчика) — файлом с целью.

    Симлинков в дереве сейчас нет, но молча превратить ссылку в текстовый файл хуже,
    чем попробовать: на файле с текстом цели тест упадёт непонятно почему.
    """
    try:
        os.symlink(link_to, target)
    except OSError:
        with open(target, "w", encoding="utf-8") as f:
            f.write(link_to)


def write_tree(root: str, entries: list[tuple[str, str, str]], dest: str) -> int:
    """Кладёт файлы среза в `dest`. Возвращает их число."""
    for mode, sha, path in entries:
        res = subprocess.run(["git", "cat-file", "blob", sha], cwd=root, capture_output=True)
        if res.returncode != 0:
            raise public_slice.SliceGitError(f"Не удалось прочитать блоб {sha} для {path}")
        target = os.path.join(dest, *path.split("/"))
        os.makedirs(os.path.dirname(target), exist_ok=True)
        if mode == "120000":
            _write_symlink(target, res.stdout.decode("utf-8", errors="replace"))
        else:
            with open(target, "wb") as f:
                f.write(res.stdout)
    return len(entries)


def init_git(tree: str) -> None:
    """Свой git внутри каталога среза (зачем — в докстринге модуля)."""
    for args in (["init", "-q"], ["add", "-A"]):
        res = _git_text(args, tree)
        if res.returncode != 0:
            raise public_slice.SliceGitError(f"Ошибка git {' '.join(args)}: {res.stderr.strip()}")


def run_pytest(tree: str) -> subprocess.CompletedProcess:
    """pytest в каталоге среза — тем же интерпретатором, что запущен сам скрипт."""
    return run_command([sys.executable, *PYTEST_ARGS], tree)


def remove_tree(tree: str) -> None:
    """Убрать каталог среза.

    Просто `shutil.rmtree` не хватает: `git add` кладёт блобы в `.git/objects`
    «только для чтения», а на Windows такой файл не удаляется — каталог остаётся
    на диске (поймано тестом на код возврата). Поэтому добавляем бит записи
    (`stat.S_IWRITE`). Важно именно ДОБАВЛЯТЬ бит к текущему режиму: на POSIX
    замена режима через `os.chmod(p, stat.S_IWRITE)` выставляет 0o200, лишая
    каталоги прав на чтение и вход, из-за чего `shutil.rmtree` не может их
    обойти и молча оставляет на диске.
    """
    for dirpath, dirnames, filenames in os.walk(tree):
        for name in dirnames + filenames:
            p = os.path.join(dirpath, name)
            try:
                os.chmod(p, os.stat(p).st_mode | stat.S_IWRITE)
            except OSError:
                pass
    try:
        os.chmod(tree, os.stat(tree).st_mode | stat.S_IWRITE)
    except OSError:
        pass
    shutil.rmtree(tree, ignore_errors=True)


def tail_lines(text: str, limit: int = TAIL_LINES) -> str:
    """Последние `limit` строк вывода."""
    return "\n".join((text or "").splitlines()[-limit:])


def _output_of(res: subprocess.CompletedProcess) -> str:
    """stdout и stderr команды одним текстом."""
    return (res.stdout or "") + (res.stderr or "")


def _not_installed(output: str) -> bool:
    """Похоже ли на «модуль не установлен»: тогда шаг не провален, а не прогонялся."""
    return "No module named" in output or "ModuleNotFoundError" in output


def _tool_result(name: str, res: subprocess.CompletedProcess, what: str = "") -> StepResult:
    """Итог шага по коду возврата команды.

    Отдельно разведены «сломалось» и «не прогонялось»: таймаут, отсутствие
    программы и незапущенный модуль — это второе, и в выводе должна быть причина,
    а не голый код возврата.
    """
    output = _output_of(res)
    subject = what or name
    if res.returncode == 0:
        return StepResult(name, OK)
    if res.returncode == TIMEOUT_CODE:
        return StepResult(name, NOTRUN, f"таймаут: {subject}", code=res.returncode, output=output)
    if res.returncode == NO_BINARY_CODE:
        return StepResult(name, NOTRUN, f"команда не запустилась: {subject}",
                          code=res.returncode, output=output)
    if _not_installed(output):
        return StepResult(name, NOTRUN, f"не установлено: {subject}", code=res.returncode, output=output)
    return StepResult(name, FAIL, f"код возврата {res.returncode}", code=res.returncode, output=output)


def _no_network(output: str) -> bool:
    """Индекс PyPI недоступен: pip без сети ничего не проверит."""
    return any(marker in output for marker in NETWORK_MARKERS)


def step_pytest(tree: str) -> StepResult:
    """Тесты контрактов в каталоге среза — как в джобе `test` CI."""
    res = run_pytest(tree)
    print(f"Код возврата pytest: {res.returncode}")
    status = OK if res.returncode == 0 else FAIL
    return StepResult("pytest", status, code=res.returncode, output=_output_of(res))


def step_ruff(tree: str) -> StepResult:
    """`ruff check .` — набор правил из `ruff.toml` среза."""
    res = run_command([sys.executable, "-m", "ruff", "check", "."], tree)
    return _tool_result("ruff", res, "ruff check .")


def step_mypy(tree: str) -> StepResult:
    """`mypy` под win32 и linux: список модулей и строгость — в `pyproject.toml` среза."""
    for platform in ("win32", "linux"):
        res = run_command([sys.executable, "-m", "mypy", "--platform", platform], tree)
        if res.returncode != 0:
            return _tool_result("mypy", res, f"mypy --platform {platform}")
    return StepResult("mypy", OK)


def _walk_ext(tree: str, ext: str) -> list[str]:
    """Файлы с расширением `ext` по всему дереву среза; служебный `.git` пропускаем."""
    found = []
    for dirpath, dirnames, filenames in os.walk(tree):
        dirnames[:] = [d for d in dirnames if d != ".git"]
        for name in filenames:
            if name.lower().endswith(ext):
                found.append(os.path.join(dirpath, name))
    return sorted(found)


def jsx_files(tree: str) -> list[str]:
    """ExtendScript, который уезжает в AE — то же, что `git ls-files '*.jsx'` в CI."""
    return _walk_ext(tree, ".jsx")


def app_js_files(tree: str) -> list[str]:
    """JS интерфейса: ровно `static/app/*.js`, без рекурсии — как в CI."""
    folder = os.path.join(tree, "static", "app")
    if not os.path.isdir(folder):
        return []
    names = [n for n in os.listdir(folder) if n.lower().endswith(".js")]
    return sorted(os.path.join(folder, n) for n in names
                  if os.path.isfile(os.path.join(folder, n)))


def step_jsx(tree: str) -> StepResult:
    """`node --check` по ExtendScript и `static/app/*.js` — джоба `jsx` в CI.

    node не принимает расширение `.jsx`, поэтому файл копируется в `.js` в
    отдельный временный каталог (в CI для этого `/tmp`). Синтаксическая ошибка в
    одном из этих файлов роняет страницу интерфейса или импорт проекта в AE, а
    python-тесты её не видят — они читают код регулярками.
    """
    node = find_tool("node")
    if not node:
        return StepResult("jsx", NOTRUN, "node не найден в PATH")
    files = jsx_files(tree) + app_js_files(tree)
    if not files:
        return StepResult("jsx", NOTRUN, "в срезе нет ни одного .jsx и ни одного static/app/*.js")

    tmp = tempfile.mkdtemp(prefix=JSX_TMP_PREFIX)
    broken = []
    checked = 0
    try:
        for path in files:
            copy = os.path.join(tmp, f"{checked}.js")
            shutil.copyfile(path, copy)
            res = run_command([node, "--check", copy], tree)
            checked += 1
            if res.returncode != 0:
                broken.append((os.path.relpath(path, tree).replace("\\", "/"), _output_of(res)))
    finally:
        remove_tree(tmp)

    if broken:
        report = "\n".join(f"FAIL {rel}\n{tail_lines(out, 5)}" for rel, out in broken)
        return StepResult("jsx", FAIL, f"не прошли node --check: {len(broken)} из {checked}",
                          output=report)
    return StepResult("jsx", OK, f"файлов проверено: {checked}")


def step_smoke(tree: str) -> StepResult:
    """`compileall` и `--help` точек входа — джоба `smoke` в CI.

    `--help` с PYTHONIOENCODING=utf-8: на Windows консоль иначе роняет печать
    русских строк в выводе справки.
    """
    res = run_command([sys.executable, "-m", "compileall", "-q", *COMPILE_PATHS], tree)
    if res.returncode != 0:
        return _tool_result("smoke", res, "compileall")

    env = {**os.environ, "PYTHONIOENCODING": "utf-8"}
    for args in CLI_HELP:
        res = run_command([sys.executable, *args, "--help"], tree, env=env)
        if res.returncode != 0:
            return _tool_result("smoke", res, f"{' '.join(args)} --help")
    return StepResult("smoke", OK)


def step_requirements(tree: str) -> StepResult:
    """Разбор требований pip'ом и чтение их в системной кодировке — джоба `requirements`.

    Второй половины мало кто ждёт: на Windows системная кодировка cp1252, и старый
    pip падал на не-ASCII комментарии в `requirements*.txt` ещё до разбора. Именно
    поэтому файлы читаются ещё и в ней, а не только в utf-8-sig.
    """
    args = [sys.executable, "-m", "pip", "install", "--dry-run", "--no-deps"]
    for name in REQUIREMENTS_FILES:
        args += ["-r", name]
    res = run_command(args, tree)
    if res.returncode != 0 and _no_network(_output_of(res)):
        return StepResult("requirements", NOTRUN,
                          "нет сети: индекс PyPI недоступен, требования не разобраны",
                          code=res.returncode, output=_output_of(res))
    parsed = _tool_result("requirements", res, "pip install --dry-run по requirements*.txt")
    if parsed.status != OK:
        return parsed

    res = run_command([sys.executable, "-c", LOCALE_CHECK], tree)
    if res.returncode != 0:
        return StepResult("requirements", FAIL,
                          "requirements*.txt не читаются в системной кодировке",
                          code=res.returncode, output=_output_of(res))
    return StepResult("requirements", OK)


def resolve_gitleaks(explicit: str | None = None) -> tuple[str | None, str]:
    """Путь к бинарнику gitleaks и причина, если его нет.

    Порядок: `--gitleaks PATH`, затем `$GITLEAKS`, затем PATH. Отдельный путь не
    ищем «наугад»: подсунуть чужой бинарник хуже, чем честно сказать, что сканера нет.
    """
    for source, value in (("--gitleaks", explicit), (f"${ENV_GITLEAKS}", os.environ.get(ENV_GITLEAKS))):
        if not value:
            continue
        if os.path.isfile(value) or find_tool(value):
            return value, ""
        return None, f"{source}: файл не найден: {value}"

    found = find_tool("gitleaks")
    if found:
        return found, ""
    return None, ("бинарник gitleaks не найден: укажи --gitleaks PATH или переменную "
                  f"{ENV_GITLEAKS}, либо пропусти шаг явным флагом --no-gitleaks")


def step_gitleaks(tree: str, gitleaks_path: str | None = None) -> StepResult:
    """Скан ДЕРЕВА среза: `gitleaks dir --redact -v -c .gitleaks.toml <каталог>`.

    История публичного репозитория так не сканируется — только новое дерево; за
    историю отвечает джоба `scan` в CI (`gitleaks git` по клону с полной глубиной).
    """
    binary, reason = resolve_gitleaks(gitleaks_path)
    if binary is None:
        return StepResult("gitleaks", FAIL, reason)
    print("Скан дерева среза (gitleaks dir): история публичного репозитория не проверяется")
    res = run_command([binary, "dir", "--redact", "-v", "-c", GITLEAKS_CONFIG, tree], tree)
    return _tool_result("gitleaks", res, "gitleaks dir")


def pack_slice_tar(tree: str) -> bytes:
    """Упаковывает файлы каталога среза (без служебного .git) в tar-архив."""
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w") as tf:
        for dirpath, dirnames, filenames in os.walk(tree):
            dirnames[:] = [d for d in dirnames if d != ".git"]
            for name in sorted(filenames):
                full = os.path.join(dirpath, name)
                rel = os.path.relpath(full, tree).replace("\\", "/")
                tf.add(full, arcname=rel)
    return buf.getvalue()


def resolve_linux_ssh(explicit: str | None = None) -> tuple[str | None, str]:
    """Хост для удалённого прогона тестов на Linux и причина, если он не задан.

    Порядок: `--linux-ssh USER@HOST`, затем `$REELSI_LINUX_SSH`.
    """
    for source, value in (("--linux-ssh", explicit), (f"${ENV_LINUX_SSH}", os.environ.get(ENV_LINUX_SSH))):
        if value and value.strip():
            return value.strip(), ""

    return None, ("хост не задан: укажи --linux-ssh USER@HOST или переменную "
                  f"{ENV_LINUX_SSH}, либо пропусти шаг явным флагом --no-linux")


def _parse_ci_pytest_args_text(text: str) -> str | None:
    """Извлекает аргументы после 'python -m pytest tests' в шаге джобы test."""
    in_jobs = False
    in_test_job = False

    for raw_line in text.splitlines():
        if raw_line and not raw_line.startswith(" ") and not raw_line.startswith("#"):
            in_jobs = (raw_line.rstrip() == "jobs:")
            in_test_job = False
            continue

        if not in_jobs:
            continue

        # Заголовок джобы: ровно 2 пробела ("  job-name:")
        m_job = re.match(r"^ {2}([a-zA-Z0-9_-]+):\s*$", raw_line)
        if m_job:
            in_test_job = (m_job.group(1) == "test")
            continue

        if not in_test_job:
            continue

        # Шаг внутри test (отступ 4+ пробелов): ищем run: ... python -m pytest tests ...
        m_run = re.search(r"^\s*run:\s*(?:.*?\b)?python\s+-m\s+pytest\s+tests(?:\s+(.*))?$", raw_line)
        if m_run:
            return (m_run.group(1) or "").strip()

    return None


def extract_ci_test_pytest_args(tree: str) -> tuple[str | None, str]:
    """Извлекает аргументы pytest из шага Tests джобы test в .github/workflows/ci.yml."""
    ci_file = os.path.join(tree, ".github", "workflows", "ci.yml")
    if not os.path.isfile(ci_file):
        return None, f"в срезе не найден файл {os.path.join('.github', 'workflows', 'ci.yml')}"

    try:
        with open(ci_file, encoding="utf-8") as f:
            text = f.read()
    except OSError as e:
        return None, f"ошибка чтения {ci_file}: {e}"

    try:
        import yaml  # type: ignore[import-untyped]
        data = yaml.safe_load(text)
        if isinstance(data, dict):
            test_job = data.get("jobs", {}).get("test", {})
            for step in test_job.get("steps", []):
                if isinstance(step, dict):
                    run_cmd = step.get("run", "")
                    if isinstance(run_cmd, str) and "python -m pytest tests" in run_cmd:
                        parts = run_cmd.split("python -m pytest tests", 1)
                        return parts[1].strip(), ""
    except Exception:
        pass

    args = _parse_ci_pytest_args_text(text)
    if args is not None:
        return args, ""

    return None, "в .github/workflows/ci.yml не найдена строка 'run: python -m pytest tests ...' в джобе test"


def step_linux(tree: str, host: str | None = None,
               image: str = DEFAULT_LINUX_IMAGE) -> StepResult:
    """Прогон тестов в docker с --init по ssh на Linux-хосте."""
    target_host, reason = resolve_linux_ssh(host)
    if target_host is None:
        return StepResult("linux", FAIL, reason)

    pytest_args, err = extract_ci_test_pytest_args(tree)
    if pytest_args is None:
        return StepResult("linux", FAIL, err)

    print(f"Прогон тестов в docker на Linux-хосте {target_host} (образ {image})")
    try:
        tar_bytes = pack_slice_tar(tree)
    except Exception as e:
        return StepResult("linux", FAIL, f"ошибка упаковки среза в tar: {e}")

    if pytest_args:
        pytest_cmd = f"python -m pytest tests {pytest_args}"
    else:
        pytest_cmd = "python -m pytest tests"
    if "-p no:cacheprovider" not in pytest_cmd:
        pytest_cmd += " -p no:cacheprovider"

    # --init обязателен: без него pytest = PID 1 внутри контейнера, killpg(1)
    # совпадает со своей группой процессов и пропускается ядром Linux (дефект
    # «тест убил раннер CI» становится невидим), и зомби-процессы не пожинаются.
    remote_script = (
        'd=$(mktemp -d) && '
        'tar -xf - -C "$d" && '
        '(cd "$d" && git init -q && git add -A && git -c user.name=slice -c user.email=slice@local commit -qm slice) && '
        f'docker run --rm --init --user "$(id -u):$(id -g)" -e HOME=/tmp -e COVERAGE_FILE=/tmp/.coverage -v "$d:/src" -w /src {image} {pytest_cmd}; '
        'rc=$?; rm -rf "$d"; exit $rc'
    )
    cmd = ["ssh", "-o", "BatchMode=yes", target_host, remote_script]
    res = run_command(cmd, tree, input=tar_bytes)
    output = _output_of(res)
    if res.returncode == 0:
        return StepResult("linux", OK, code=0, output=output)
    if res.returncode == TIMEOUT_CODE:
        return StepResult("linux", NOTRUN, f"таймаут: ssh {target_host}",
                          code=res.returncode, output=output)
    if res.returncode == NO_BINARY_CODE:
        return StepResult("linux", NOTRUN, "команда не запустилась: ssh",
                          code=res.returncode, output=output)
    return StepResult("linux", FAIL, f"код возврата {res.returncode}",
                      code=res.returncode, output=output)


def _step_calls(gitleaks_path: str | None = None,
                linux_ssh: str | None = None,
                linux_image: str = DEFAULT_LINUX_IMAGE) -> dict[str, Callable[[str], StepResult]]:
    """Запускалки шагов: имя -> вызов. Порядок и имена — в `STEP_NAMES`."""
    return {
        "pytest": step_pytest,
        "ruff": step_ruff,
        "mypy": step_mypy,
        "jsx": step_jsx,
        "smoke": step_smoke,
        "requirements": step_requirements,
        "gitleaks": partial(step_gitleaks, gitleaks_path=gitleaks_path),
        "linux": partial(step_linux, host=linux_ssh, image=linux_image),
    }


def _split_names(values: list[str] | None) -> list[str]:
    """Имена шагов из повторяемых `--only`/`--skip` (можно через запятую)."""
    names = []
    for value in values or []:
        names += [part.strip() for part in value.split(",") if part.strip()]
    return names


def _skip_reason(name: str, only: set[str], skip: set[str],
                 no_gitleaks: bool, no_linux: bool) -> str:
    """Почему шаг не пойдёт; пустая строка — идёт."""
    if only and name not in only:
        return f"не выбран (--only {','.join(sorted(only))})"
    if name in skip:
        return "пропущен явным флагом --skip"
    if name == "gitleaks" and no_gitleaks:
        return "явный флаг --no-gitleaks: дерево среза не сканировалось"
    if name == "linux" and no_linux:
        return "явный флаг --no-linux: тесты на Linux не прогонялись"
    return ""


def run_steps(tree: str, only: set[str], skip: set[str],
              gitleaks_path: str | None, no_gitleaks: bool,
              linux_ssh: str | None = None,
              linux_image: str = DEFAULT_LINUX_IMAGE,
              no_linux: bool = False) -> list[StepResult]:
    """Прогон шагов в порядке `STEP_NAMES`; пропущенные попадают в таблицу отдельным итогом."""
    calls = _step_calls(gitleaks_path=gitleaks_path, linux_ssh=linux_ssh, linux_image=linux_image)
    results = []
    for name in STEP_NAMES:
        reason = _skip_reason(name, only, skip, no_gitleaks, no_linux)
        if reason:
            if name == "gitleaks" and no_gitleaks:
                print("gitleaks НЕ ПРОГОНЯЛСЯ: явный флаг --no-gitleaks — дерево среза не "
                      "сканировано, история публичного репозитория так не проверяется")
            elif name == "linux" and no_linux:
                print("linux НЕ ПРОГОНЯЛСЯ: явный флаг --no-linux — тесты на Linux в docker с "
                      "--init не прогонялись")
            results.append(StepResult(name, SKIP, reason))
            print_result(results[-1])
            continue
        print(f"--- {name}: {STEP_HINTS[name]}")
        results.append(calls[name](tree))
        print_result(results[-1])
    return results


def print_result(res: StepResult) -> None:
    """Строка итога шага и, если есть что смотреть, хвост вывода команды."""
    detail = f" — {res.detail}" if res.detail else ""
    print(f"[{res.status}] {res.name}{detail}")
    if res.output:
        print(tail_lines(res.output))


def print_summary(results: list[StepResult]) -> None:
    """Сводная таблица шагов и общий итог."""
    print("")
    print("Итог по шагам:")
    width = max(len(r.name) for r in results) if results else 0
    for res in results:
        detail = f" — {res.detail}" if res.detail else ""
        print(f"  {res.name.ljust(width)}  {res.status}{detail}")
    bad = [res.name for res in results if res.failed]
    if bad:
        print(f"НЕ ПРОЙДЕНО: {', '.join(bad)}")
    else:
        print("Пройдено всё, что запускалось.")


def exit_code(results: list[StepResult]) -> int:
    """Код возврата скрипта: провал тестов отдаёт их код, любой другой провал — 1."""
    bad = [res for res in results if res.failed]
    if not bad:
        return 0
    for res in bad:
        if res.name == "pytest" and res.code:
            return res.code
    return 1


def main(argv: list[str] | None = None, root: str | None = None) -> int:
    """Точка входа CLI."""
    parser = argparse.ArgumentParser(
        description="Прогон публичного среза (tools/public_slice.py) через проверки CI "
                    "во временном каталоге: pytest, ruff, mypy, node --check, compileall "
                    "с --help, разбор requirements и gitleaks."
    )
    parser.add_argument("--ref", default="HEAD", help="Коммит-источник среза (по умолчанию HEAD)")
    parser.add_argument("--keep", action="store_true", help="Оставить каталог среза на диске (для разбора падений)")
    parser.add_argument("--root", default=None, help="Корень репозитория (по умолчанию автоопределение)")
    parser.add_argument("--only", action="append", default=None, metavar="ШАГ",
                        help=f"Гнать только эти шаги ({', '.join(STEP_NAMES)}); можно через запятую")
    parser.add_argument("--skip", action="append", default=None, metavar="ШАГ",
                        help="Пропустить шаг явно: он не красит код возврата")
    parser.add_argument("--gitleaks", default=None, metavar="PATH",
                        help=f"Путь к бинарнику gitleaks (иначе ${ENV_GITLEAKS} или PATH)")
    parser.add_argument("--no-gitleaks", action="store_true",
                        help="Не гонять gitleaks: шаг не прогонялся, о чём скрипт скажет громко")
    parser.add_argument("--linux-ssh", default=None, metavar="USER@HOST",
                        help=f"Хост для запуска тестов на Linux по ssh (иначе ${ENV_LINUX_SSH})")
    parser.add_argument("--linux-image", default=DEFAULT_LINUX_IMAGE, metavar="IMAGE",
                        help=f"Docker-образ на удалённом хосте (по умолчанию {DEFAULT_LINUX_IMAGE})")
    parser.add_argument("--no-linux", action="store_true",
                        help="Не гонять тесты на Linux: шаг не прогонялся, о чём скрипт скажет громко")

    try:
        args = parser.parse_args(argv)
    except SystemExit as e:
        return e.code if isinstance(e.code, int) else 2

    only = set(_split_names(args.only))
    skip = set(_split_names(args.skip))
    unknown = (only | skip) - set(STEP_NAMES)
    if unknown:
        print(f"Неизвестный шаг: {', '.join(sorted(unknown))}; известные: {', '.join(STEP_NAMES)}",
              file=sys.stderr)
        return 2

    repo_root = args.root or root or _detect_root()
    tree = tempfile.mkdtemp(prefix=TREE_PREFIX)
    try:
        entries = slice_entries(repo_root, args.ref)
        write_tree(repo_root, entries, tree)
        init_git(tree)
        print(f"Срез {args.ref}: файлов {len(entries)}")
        print(f"Каталог среза: {tree}")

        results = run_steps(tree, only, skip, args.gitleaks, args.no_gitleaks,
                            linux_ssh=args.linux_ssh,
                            linux_image=args.linux_image,
                            no_linux=args.no_linux)
        print_summary(results)
        return exit_code(results)
    except public_slice.SliceGitError as e:
        print(f"Ошибка git: {e}", file=sys.stderr)
        return 2
    except Exception as e:
        print(f"Непредвиденная ошибка: {e}", file=sys.stderr)
        return 2
    finally:
        if args.keep:
            print(f"Каталог среза оставлен: {tree}")
        else:
            remove_tree(tree)


if __name__ == "__main__":
    sys.exit(main())
