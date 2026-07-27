# Transcript normalization compatibility

Публичный этап переименован в **LLM-фильтрацию учебного содержания**, поскольку
его контракт допускает только удаление неучебных фрагментов и запрещает
перефразирование или исправление распознавания.

Актуальная документация: [educational-content-filter.md](educational-content-filter.md).
Внутренний пакет `tutor_assistant.normalization` и старые CLI-команды сохранены как
совместимые поверхности для существующих баз и `lesson.json`.
