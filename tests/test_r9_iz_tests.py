# -*- coding: utf-8 -*-
# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Maxim Si
"""Задание IZ (круг 9): тесты, изоляция, установщики.

1. Установщики: .gitignore содержит .venv/, install.sh проверяет venv/ensurepip,
   install.ps1 содержит логику venv и синтаксически валиден для [scriptblock]::Create.
2. Изоляция conftest: глубокая копия и полный сброс джобов, сброс кэшей insertlib и
   app_meta._UI_LANG_CACHED, переармирование логгера в pytest_runtest_teardown hookwrapper.
3. Фронтенд (node):
   - static/app/95-styles.js: sfxLoad() берет d.pps (SFX.pps = d.pps);
   - static/app/40-queue.js: отмена скачивания дает нейтральный статус без toast();
   - static/app/10-settings.js: aiSetProv() очищает ключ-маску при смене провайдера.

Запуск: py -3.10 -m pytest tests/test_r9_in_tests.py -q -p no:cacheprovider
"""
import logging
from logging.handlers import RotatingFileHandler
import os
from pathlib import Path
import shutil
import subprocess
import sys

import pytest

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core import applog  # noqa: E402

node_required = pytest.mark.skipif(not shutil.which("node"), reason="требуется node в PATH")


def test_gitignore_contains_venv():
    """Файл .gitignore содержит правило игнорирования .venv/."""
    gitignore_path = ROOT / ".gitignore"
    text = gitignore_path.read_text(encoding="utf-8")
    assert ".venv/" in text.splitlines()


def test_install_sh_checks_venv_ensurepip():
    """Скрипт install.sh проверяет наличие модулей venv и ensurepip."""
    sh_path = ROOT / "install.sh"
    content = sh_path.read_text(encoding="utf-8")
    assert 'import venv, ensurepip' in content
    assert 'REELSI_NO_VENV=1' in content
    assert 'python3-venv' in content


def test_install_ps1_syntax_and_venv_logic():
    """Скрипт install.ps1 парсится через [scriptblock]::Create и содержит логику venv."""
    ps1_path = ROOT / "install.ps1"
    content = ps1_path.read_text(encoding="utf-8")
    assert '$env:REELSI_NO_VENV' in content
    assert 'import venv, ensurepip' in content

    # Проверка через PowerShell [scriptblock]::Create((Get-Content -Raw install.ps1))
    # На Linux PowerShell обычно нет: разбор скрипта проверяем там, где он есть.
    exe = shutil.which("powershell") or shutil.which("pwsh")
    if not exe:
        pytest.skip("нет powershell/pwsh — разбор install.ps1 не проверить")
    res = subprocess.run(
        [exe, "-NoProfile", "-Command", "[scriptblock]::Create((Get-Content -Raw install.ps1)) | Out-Null; Write-Host OK"],
        cwd=str(ROOT),
        capture_output=True,
        text=True,
    )
    assert res.returncode == 0, f"powershell parser error: {res.stderr}"
    assert "OK" in res.stdout


def test_logger_monkeypatch_override_step1(monkeypatch, tmp_path):
    """Шаг 1/2: тест выставляет кастомный REELSI_LOG через monkeypatch."""
    custom_log = tmp_path / "test_override.log"
    monkeypatch.setenv("REELSI_LOG", str(custom_log))
    logger = applog.get_logger("reelsi.test_step1")
    logger.info("custom log entry")

    base = logging.getLogger("reelsi")
    handlers = [h for h in base.handlers if isinstance(h, RotatingFileHandler)]
    assert any(os.path.normcase(h.baseFilename) == os.path.normcase(str(custom_log)) for h in handlers)


def test_logger_rearmed_to_session_log_step2():
    """Шаг 2/2: следующий тест видит обработчик на логе сессии, переармированный в teardown."""
    from conftest import _TEST_LOG_FILE
    base = logging.getLogger("reelsi")
    handlers = [h for h in base.handlers if isinstance(h, RotatingFileHandler)]
    assert len(handlers) == 1, "Должен остаться ровно один RotatingFileHandler на базовом логгере"
    assert os.path.normcase(handlers[0].baseFilename) == os.path.normcase(_TEST_LOG_FILE)


def test_job_state_isolation_mutation_step1():
    """Шаг 1/2: мутирует состояние джобов и кэшей."""
    from api.render import RJOB, RLOCK
    from core import app_meta, insertlib
    with RLOCK:
        RJOB["result"] = ["leaked_result"]
        RJOB["out_dir"] = "/leaked/path"
        RJOB["items"] = ["leaked_item"]
    app_meta._UI_LANG_CACHED = "de"
    insertlib._CACHE["data"] = {"leaked": True}


def test_job_state_isolation_restored_step2():
    """Шаг 2/2: следующий тест видит восстановленное чистое состояние из conftest."""
    from api.render import RJOB
    from core import app_meta, insertlib
    assert RJOB["result"] == []
    assert RJOB["out_dir"] == ""
    assert "items" not in RJOB
    assert app_meta._UI_LANG_CACHED is None
    assert insertlib._CACHE["data"] is None


@node_required
def test_frontend_styles_sfx_pps_takes_d_pps(tmp_path):
    """Поведение static/app/95-styles.js: sfxLoad берет d.pps ответа сервера."""
    styles_js = (ROOT / "static" / "app" / "95-styles.js").as_posix()
    runner = f"""
const fs = require('fs');
const vm = require('vm');

const src = fs.readFileSync('{styles_js}', 'utf8');

const elements = {{}};
function $(id) {{
  if (!elements[id]) elements[id] = {{ style: {{}}, textContent: '', value: '' }};
  return elements[id];
}}

const sandbox = {{
  console,
  $,
  val: (id) => ($(id).value || ''),
  document: {{
    addEventListener: () => {{}},
    removeEventListener: () => {{}},
    createElement(tag) {{
      return {{
        style: {{}},
        duration: 5.0,
        addEventListener(ev, cb) {{
          if (ev === 'loadedmetadata') setTimeout(cb, 0);
        }},
        removeEventListener() {{}}
      }};
    }},
    querySelectorAll: () => []
  }},
  fetch: async (url) => ({{
    json: async () => ({{ pps: 142, peaks: [0.1, 0.3], dur: 5.0 }})
  }}),
  t: (s) => s,
  devicePixelRatio: 1,
  setTimeout,
  clearTimeout,
  Promise
}};

vm.createContext(sandbox);
vm.runInContext(src, sandbox);

vm.runInContext(`
  SFX = {{
    kind: 'audio',
    path: 'sample.wav',
    dur: 0,
    pps: 0,
    peaks: []
  }};
`, sandbox);

sandbox.sfxLoad().then(() => {{
  const pps = vm.runInContext('SFX.pps', sandbox);
  if (pps !== 142) {{
    console.error('FAIL: SFX.pps expected 142, got ' + pps);
    process.exit(1);
  }}
  console.log('OK: SFX.pps = ' + pps);
}}).catch((err) => {{
  console.error(err);
  process.exit(1);
}});
"""
    runner_file = tmp_path / "test_sfx_pps.js"
    runner_file.write_text(runner, encoding="utf-8")
    res = subprocess.run(["node", str(runner_file)], capture_output=True, text=True, timeout=10)
    assert res.returncode == 0, f"node execution failed: {res.stderr}\n{res.stdout}"
    assert "OK: SFX.pps = 142" in res.stdout


@node_required
def test_frontend_queue_download_cancel_neutral_status_no_toast(tmp_path):
    """Поведение static/app/40-queue.js: при отмене скачивания статус нейтральный и тост не зовется."""
    queue_js = (ROOT / "static" / "app" / "40-queue.js").as_posix()
    runner = f"""
const fs = require('fs');
const vm = require('vm');

const src = fs.readFileSync('{queue_js}', 'utf8');

const elements = {{}};
function $(id) {{
  if (!elements[id]) elements[id] = {{ style: {{}}, textContent: '', value: '' }};
  return elements[id];
}}

let toastCalls = [];
let uiLogs = [];

const sandbox = {{
  console,
  $,
  t: (s) => s,
  fmtLog: (l) => l,
  uiLog: (m) => uiLogs.push(m),
  toast: (m) => toastCalls.push(m),
  document: {{
    addEventListener: () => {{}},
    removeEventListener: () => {{}},
    createElement: () => ({{ style: {{}} }}),
    querySelectorAll: () => []
  }},
  clearInterval: () => {{}},
  setInterval: () => 0,
  fetch: async (url) => ({{
    json: async () => ({{
      running: false,
      done: true,
      cancelled: true,
      failed: null,
      log: ['cancel log']
    }})
  }}),
  setTimeout,
  clearTimeout,
  Promise
}};

vm.createContext(sandbox);
vm.runInContext(src, sandbox);

sandbox.gdTick().then(() => {{
  const stText = elements['gdrive_status'] ? elements['gdrive_status'].textContent : '';
  if (stText !== 'скачивание остановлено') {{
    console.error('FAIL: status text expected "скачивание остановлено", got: ' + stText);
    process.exit(1);
  }}
  if (toastCalls.length > 0) {{
    console.error('FAIL: toast was called on cancellation: ' + JSON.stringify(toastCalls));
    process.exit(1);
  }}
  console.log('OK: cancelled status without toast');
}}).catch((err) => {{
  console.error(err);
  process.exit(1);
}});
"""
    runner_file = tmp_path / "test_queue_cancel.js"
    runner_file.write_text(runner, encoding="utf-8")
    res = subprocess.run(["node", str(runner_file)], capture_output=True, text=True, timeout=10)
    assert res.returncode == 0, f"node execution failed: {res.stderr}\n{res.stdout}"
    assert "OK: cancelled status without toast" in res.stdout


@node_required
def test_frontend_settings_provider_change_clears_masked_key(tmp_path):
    """Поведение static/app/10-settings.js: смена провайдера очищает маску ключа и показывает предупреждение."""
    settings_js = (ROOT / "static" / "app" / "10-settings.js").as_posix()
    runner = f"""
const fs = require('fs');
const vm = require('vm');

const src = fs.readFileSync('{settings_js}', 'utf8');

const elements = {{}};
function $(id) {{
  // addEventListener: 10-settings.js при загрузке вешает обработчик на поле URL (IW)
  if (!elements[id]) elements[id] = {{ style: {{}}, textContent: '', value: '', options: [],
    listeners: {{}}, addEventListener(ev, fn) {{ (this.listeners[ev] = this.listeners[ev] || []).push(fn); }} }};
  return elements[id];
}}

const sandbox = {{
  console,
  $,
  val: (id) => ($(id).value || ''),
  esc: (s) => s,
  t: (s, params) => {{
    if (params && params.n) return s.replace('{{n}}', params.n);
    return s;
  }},
  document: {{
    addEventListener: () => {{}},
    removeEventListener: () => {{}},
    createElement: () => ({{ style: {{}} }}),
    querySelectorAll: () => []
  }},
  fetch: async (url) => ({{
    json: async () => ({{
      active: 'default',
      presets: {{
        lmstudio: {{ base_url: 'http://127.0.0.1:1234/v1', models: [] }},
        openrouter: {{ base_url: 'https://openrouter.ai/api/v1', models: [] }}
      }},
      profiles: {{
        default: {{ provider: 'lmstudio', base_url: 'http://127.0.0.1:1234/v1', api_key: '\\u2022\\u2022\\u2022\\u2022\\u2022\\u2022\\u2022\\u2022' }}
      }}
    }})
  }}),
  devicePixelRatio: 1,
  setTimeout,
  clearTimeout,
  Promise
}};

vm.createContext(sandbox);
vm.runInContext(src, sandbox);

vm.runInContext(`
  AICFG = {{
    active: 'default',
    presets: {{
      lmstudio: {{ base_url: 'http://127.0.0.1:1234/v1', models: [] }},
      openrouter: {{ base_url: 'https://openrouter.ai/api/v1', models: [] }}
    }},
    profiles: {{
      default: {{ provider: 'lmstudio', base_url: 'http://127.0.0.1:1234/v1', api_key: '\\u2022\\u2022\\u2022\\u2022\\u2022\\u2022\\u2022\\u2022' }}
    }}
  }};
`, sandbox);

elements['ais_provider'] = {{ value: 'openrouter', style: {{}} }};
elements['ais_key'] = {{ value: '\\u2022\\u2022\\u2022\\u2022\\u2022\\u2022\\u2022\\u2022', style: {{}} }};
elements['ais_url'] = {{ value: 'http://127.0.0.1:1234/v1', style: {{}} }};
elements['ais_key_warn'] = {{ textContent: '', style: {{ display: 'none' }} }};
elements['ais_model'] = {{ value: '', style: {{}} }};
elements['ais_models'] = {{ innerHTML: '', options: [], style: {{}} }};
elements['ais_reas'] = {{ textContent: '', style: {{}} }};

sandbox.aiSetProv();

const keyVal = elements['ais_key'].value;
const warnEl = elements['ais_key_warn'];

if (keyVal !== '') {{
  console.error('FAIL: ais_key should be cleared, got: ' + keyVal);
  process.exit(1);
}}
if (!warnEl.textContent.includes('Сменился адрес или провайдер')) {{
  console.error('FAIL: ais_key_warn missing expected warning message: ' + warnEl.textContent);
  process.exit(1);
}}
if (warnEl.style.display === 'none') {{
  console.error('FAIL: ais_key_warn should be visible');
  process.exit(1);
}}
console.log('OK: mask cleared and warning displayed on provider change');
"""
    runner_file = tmp_path / "test_settings_mask.js"
    runner_file.write_text(runner, encoding="utf-8")
    res = subprocess.run(["node", str(runner_file)], capture_output=True, text=True, timeout=10)
    assert res.returncode == 0, f"node execution failed: {res.stderr}\n{res.stdout}"
    assert "OK: mask cleared and warning displayed on provider change" in res.stdout
