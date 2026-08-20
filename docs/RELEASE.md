# Release governance и сборка Windows

## Runtime и обязательный gate

Production runtime — Python 3.12.x. Python 3.13/3.14 выполняются отдельным compatibility
workflow и не входят в contractual release gate. Stable required check называется:

```text
Release 1.0 Gate
```

Gate объединяет production full suite, Ruff/compileall/lock, privacy/secret contracts,
architecture boundaries, accessibility/scaling и packaging/recovery smoke. Failed,
cancelled и skipped dependency jobs приводят к failure aggregate gate.

## Защита main

После первого успешного `Release 1.0 Gate` владелец repository вручную включает:

- pull request перед merge;
- обязательный status check `Release 1.0 Gate`;
- up-to-date branch и resolved conversations;
- запрет force push и удаления `main`.

Для single-maintainer repository не требуется approval второго человека. Emergency bypass
разрешён только для production blocker, с audit note, последующим PR и повторным gate.
Изменение repository settings требует отдельного разрешения владельца; изменение файлов
репозитория само по себе не включает branch protection.

## Release candidate

```powershell
uv lock --check
uv run ruff check .
uv run pytest -q
uv run python scripts\build_windows.py --validate-only --tag v1.0.0-rc.1
tutor-assistant --config config\app.yaml recovery-drill
```

Python package version `1.0.0rc1` и Git tag `v1.0.0-rc.1` — эквивалентные PEP 440
представления; release validation сравнивает их canonical form. Stable `v1.0.0` требует
отдельного изменения project/package version после успешного физического RC soak.

## Windows packaging

```powershell
uv sync --extra desktop --extra transcription --extra packaging --group dev
uv run python scripts\build_windows.py --build
uv run python scripts\build_windows.py --smoke dist\TutorAssistant\TutorAssistant.exe
uv run python scripts\build_windows.py --installer
uv run python scripts\build_windows.py --installer-smoke dist\TutorAssistant-1.0.0rc1-win64-setup.exe
uv run python scripts\build_windows.py --scan dist
```

PyInstaller использует `onedir`, production composition root и пример безопасной конфигурации.
Inno Setup проверяет clean install, reinstall и uninstall без удаления config/workspace/backups.
Build выполняется только на Windows/Python 3.12 и требует clean checkout.

## GitHub release workflow

`.github/workflows/release.yml` запускается при tag `v1.*` или вручную. Pipeline выполняет:

```text
production verification → frozen build → executable signing → installer build
→ installer signing → portable repack → installation smoke → privacy scan
→ wheel → build-manifest.json → SHA256SUMS.txt → immutable GitHub Release
```

Assets включают installer, portable ZIP, Python wheel, SHA-256 list и build manifest.
Повторная публикация существующего tag запрещена: исправление делается новым patch release.

## Signing

Repository secrets:

- `WINDOWS_SIGNING_CERTIFICATE` — base64-encoded PFX certificate;
- `WINDOWS_SIGNING_PASSWORD` — пароль сертификата.

Certificate создаётся только во временном файле job и удаляется после signature verification.
RC может быть unsigned; manifest честно фиксирует `signed: false`. Stable unsigned release
блокируется, если владелец не дал отдельное явное `allow_unsigned_stable` approval.

## Stable release decision

Stable tag допускается только после green gate, PASS recovery drill, Windows install smoke,
sanitized hardware evidence (20 часов / 2 часа непрерывно / 20 циклов / 5 recovery /
5 disruptions), актуальной документации и отсутствия открытых P0/P1 data-loss defects.
