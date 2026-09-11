# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Maxim Si
<#
.SYNOPSIS
Рабочая копия под ОТДЕЛЬНУЮ сессию агента (git worktree) со всей личной обвязкой.

.DESCRIPTION
Две сессии в одной папке дерутся: одна делает `git switch`/`git stash`, у второй
работа исчезает из-под рук (так и случилось 2026-08-14 — задание BF2 встало,
чужие правки уехали в stash). Лечится не договорённостями, а отдельной рабочей
копией на сессию: общий `.git`, свои файлы.

Личное в гит не попадает (ключи, база вставок, стили, спикеры), поэтому свежая
копия без обвязки бесполезна: сессия оказывается без ключей и с пустой базой.
Обвязку и выдаёт этот скрипт — часть файлов ОБЩИЕ с боевой копией (через
переменные `REELSI_*`), часть своя.

Что общее и почему:
  ai_config.json  ключи; заводить вторые незачем
  insertlib.json  база вставок; индекс один на человека
  terms.json, badwords.user.txt, okwords.user.txt   словари
  job.lock        ЛОК ДЖОБА — общий НАРОЧНО: GPU один, и переполнение VRAM на
                  Windows не даёт OOM, а вешает машину целиком
Что своё: ui_state.json (две сессии затирали бы состояние друг друга),
папка генерации видео, порт UI.

.EXAMPLE
powershell -File tools/wt.ps1 -Name intro       # завести копию под задание
powershell -File tools/wt.ps1 -Name intro -Remove
#>
param(
    [Parameter(Mandatory = $true)][string]$Name,
    [string]$From = "main",
    [int]$Port = 0,
    [switch]$Remove
)

$ErrorActionPreference = "Stop"
$repo = Split-Path $PSScriptRoot -Parent
Set-Location $repo

# Боевая копия = главный worktree репозитория: его .git — это common-dir, у
# остальных копий там файл-указатель.
$common = (git rev-parse --git-common-dir)
if (-not [System.IO.Path]::IsPathRooted($common)) { $common = Join-Path $repo $common }
$home_wt = Split-Path (Resolve-Path $common) -Parent
$dir = Join-Path (Split-Path $home_wt -Parent) "wt-$Name"

if ($Remove) {
    if (-not (Test-Path $dir)) { throw "нет такой копии: $dir" }
    # Чистоту проверяем ДО того, как трогать junction'ы: иначе на несделанном
    # удалении копия останется без styles/speakers, а работа в ней — на месте.
    $dirty = (git -C $dir status --porcelain)
    if ($dirty) { throw "в копии есть незакоммиченная работа, сначала разберись с ней:`n$dirty" }
    # Junction снимаем ОБЯЗАТЕЛЬНО до удаления копии: удаление вместе со связкой
    # выносит файлы по ту сторону — то есть боевые styles/ и speakers/.
    foreach ($n in @("styles", "speakers")) {
        $link = Join-Path $dir $n
        if (Test-Path $link) { cmd /c rmdir "`"$link`"" | Out-Null }
    }
    git worktree remove $dir
    if ($LASTEXITCODE -ne 0) { throw "копия НЕ убрана: $dir" }
    Write-Host "убрано: $dir"
    return
}

if (Test-Path $dir) { throw "уже есть: $dir" }
git worktree add $dir -b "feat/$Name" $From

# styles/ и speakers/ — папки, и переменной окружения под них нет: связываем
# junction'ом (симлинк на Windows требует прав, junction — нет).
foreach ($n in @("styles", "speakers")) {
    $src = Join-Path $home_wt $n
    $link = Join-Path $dir $n
    if ((Test-Path $src) -and -not (Test-Path $link)) {
        cmd /c mklink /J "`"$link`"" "`"$src`"" | Out-Null
    }
}

if ($Port -le 0) { $Port = 5001 + (Get-ChildItem (Split-Path $home_wt -Parent) -Directory -Filter "wt-*").Count }

# env.ps1 — то, что сессия делает первым делом: `. .\env.ps1`
$env_ps = @"
# Обвязка рабочей копии wt-$Name. Подключить: . .\env.ps1
`$env:REELSI_AI_CONFIG = "$home_wt\ai_config.json"     # ключи — общие
`$env:REELSI_AI_LOG    = "$home_wt\ai_calls.jsonl"
`$env:REELSI_INSERTLIB = "$home_wt\insertlib.json"     # база вставок — общая
`$env:REELSI_TERMS     = "$home_wt\terms.json"
`$env:REELSI_BADWORDS  = "$home_wt\badwords.user.txt"
`$env:REELSI_OKWORDS   = "$home_wt\okwords.user.txt"
`$env:REELSI_JOB_LOCK  = "$home_wt\job.lock"           # НАРОЧНО общий: GPU один
`$env:REELSI_UI_STATE  = "`$PSScriptRoot\ui_state.json"   # своё
`$env:REELSI_VIDEO_DIR = "`$PSScriptRoot\_videogen"       # своё
`$env:PORT             = "$Port"
Write-Host "wt-${Name}: ключи и база — общие, состояние UI своё, порт $Port"
"@
Set-Content -LiteralPath (Join-Path $dir "env.ps1") -Value $env_ps -Encoding utf8

Write-Host ""
Write-Host "готово: $dir  (ветка feat/$Name от $From, порт $Port)"
Write-Host "в новой сессии:  cd $dir ; . .\env.ps1"
Write-Host "убрать потом:    powershell -File tools/wt.ps1 -Name $Name -Remove"
