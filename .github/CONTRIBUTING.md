# Contributing to Reelsi

All pull requests require signing the [CLA](../docs/CLA.md). [CLA Assistant](https://cla-assistant.io/mxmlab/reelsi) checks every pull request and asks you to sign once.

[![CLA assistant](https://cla-assistant.io/readme/badge/mxmlab/reelsi)](https://cla-assistant.io/mxmlab/reelsi)

## Language policy

| Where | Language |
|---|---|
| Issues, PRs, commits | English or Russian |
| `README.md`, UI strings | English (`README.ru.md` in Russian) |
| Code comments | Russian or English (do not translate existing) |
| Internal specs in `docs/` | Russian |

## Before writing code

Read [docs/ARCHITECTURE.md](../docs/ARCHITECTURE.md) first. For features beyond small bug fixes, open an issue first.

## Ground rules

- Add a test for every bug fix.
- Fixtures are anonymized byte-for-byte to keep binary subtitle offsets valid.
- Never commit personal data (`ai_config.json`, `insertlib.json`, `ui_state.json`, `styles/*.json`, `speakers/*.json`).
- Comments explain why, not what.
- Do not touch `data/refblobs.json`, `data/refblobs_color.json`, or `data/sub_template.xml`.

## Development and tests

Run all commands from the `reelsi/` folder:

```bash
pip install torch==2.5.1 torchaudio==2.5.1 torchvision==0.20.1 --index-url https://download.pytorch.org/whl/cu121
pip install -r requirements.txt -r requirements-optional.txt
python -m pytest tests -q
python webui.py
```

Front-end edits apply on page refresh; back-end edits require a server restart.

## Verifying output

Verify generated After Effects scripts without opening AE:

```bash
python -m core.verify_jsx Reelsi_out
python -m core.verify_jsx out.jsx --xml source.xml
python tools/verify_ae.py project.inspect.json --jsx out.jsx
```

Rebuild `.jsx` files after editing `core/xml2ae/`.

## Branches and PRs

- Branch from `main`: `feat/name` or `fix/name`.
- One concern per pull request. CI must be green. Merges are squashed.
- Report bugs with OS, GPU, logs, and sample files. Security issues go to [SECURITY.md](SECURITY.md).
