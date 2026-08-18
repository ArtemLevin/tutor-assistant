from __future__ import annotations

from pathlib import Path


def replace_exact(path: str, old: str, new: str) -> None:
    file_path = Path(path)
    source = file_path.read_text(encoding="utf-8")
    if old not in source:
        raise RuntimeError(f"Expected source fragment not found in {path}: {old!r}")
    updated = source.replace(old, new, 1)
    if updated == source:
        raise RuntimeError(f"Replacement made no change in {path}")
    file_path.write_text(updated, encoding="utf-8")


replace_exact(
    "src/tutor_assistant/ui/app.py",
    "def main() -> None:\n    force_setup = \"--setup\" in sys.argv\n",
    "def main(window_type: type[MainWindow] = MainWindow) -> None:\n    force_setup = \"--setup\" in sys.argv\n",
)
replace_exact(
    "src/tutor_assistant/ui/app.py",
    "    window = MainWindow(config_path)\n",
    "    window = window_type(config_path)\n",
)

for path in (
    "src/tutor_assistant/ui/transcript_publication_app.py",
    "src/tutor_assistant/ui/audio_resilient_app.py",
    "src/tutor_assistant/ui/recording_finalize_app.py",
    "src/tutor_assistant/ui/recording_recovery_app.py",
):
    replace_exact(
        path,
        "def main() -> None:\n    base_app.MainWindow = MainWindow\n    base_app.main()\n",
        "def main() -> None:\n    base_app.main(MainWindow)\n",
    )

replace_exact(
    "src/tutor_assistant/ui/concurrent_app.py",
    "def main() -> None:\n    # Reuse the established startup/setup workflow while injecting the safe window implementation.\n    base_app.MainWindow = MainWindow\n    base_app.main()\n",
    "def main() -> None:\n    base_app.main(MainWindow)\n",
)

for path in (
    "src/tutor_assistant/ui/concurrent_app.py",
    "src/tutor_assistant/ui/transcript_publication_app.py",
    "src/tutor_assistant/ui/audio_resilient_app.py",
    "src/tutor_assistant/ui/recording_finalize_app.py",
    "src/tutor_assistant/ui/recording_recovery_app.py",
):
    source = Path(path).read_text(encoding="utf-8")
    if "base_app.MainWindow = MainWindow" in source:
        raise RuntimeError(f"Legacy composition monkeypatch still present in {path}")
    if "base_app.main(MainWindow)" not in source:
        raise RuntimeError(f"Explicit bootstrap call missing in {path}")

Path("scripts/_migrate_wave2_composition.py").unlink()
Path(".github/workflows/_wave2_composition_migration.yml").unlink()
