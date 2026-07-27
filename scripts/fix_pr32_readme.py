from pathlib import Path

path = Path("README.md")
text = path.read_text(encoding="utf-8")
old = """API-ключ читается только из переменной окружения и в YAML не сохраняется.

Старые команды `normalize` и `normalization-doctor`, а также внутреннее имя
"""
new = """API-ключ читается только из переменной окружения и в YAML не сохраняется.

Фильтр автоматически выбирает профиль по предмету занятия: математика, физика,
химия или общий консервативный режим. Предметные термины, формулы и единицы
защищаются отдельными словарями, а профиль и версия промпта сохраняются в manifest.

Старые команды `normalize` и `normalization-doctor`, а также внутреннее имя
"""
if old not in text:
    raise RuntimeError("README subject-profile insertion point not found")
path.write_text(text.replace(old, new, 1), encoding="utf-8")
