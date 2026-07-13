# Tutor Assistant: обработка готовых занятий

## Назначение

Обрабатывай задания Tutor Assistant в репозитории `ArtemLevin/students-26-27`.
Каждое занятие уже опубликовано в отдельной ветке `lesson/*` и связано с открытым draft PR.

## Поиск задания

На каждом запуске:

1. Получи список открытых draft PR репозитория `ArtemLevin/students-26-27`.
2. Оставь PR, у которых head-ветка начинается с `lesson/`.
3. Читай `lesson.json` и `job.status.json` из head-ветки PR. Основная ветка `main` не содержит
   новые задания до слияния PR.
4. Используй `job.status.json` как источник текущего состояния. Поле `lesson.json.status` может
   отставать на один этап.
5. Игнорируй задания со статусами `completed`, `failed`, `generated_tex` и `compiling_pdf`.
6. Отсортируй оставшиеся PR по времени создания и обработай только одно самое старое занятие.
7. Перед изменениями повторно прочитай head SHA и `job.status.json`. Если ветка изменилась,
   перечитай данные и заново выбери допустимый этап.

Работай в существующей head-ветке и draft PR. Не создавай новую ветку или параллельный PR.
Каждый законченный этап фиксируй отдельным коммитом. Сохраняй имеющиеся поля
`job.status.json`, если инструкция этапа не требует их изменить.

## Общие источники и правила

1. Прочитай `lesson.json` и файлы каталога `source/` выбранного занятия.
2. Используй подтверждённый `source/transcript.txt` как основной источник.
3. Если существуют `teacher_transcript.txt` и `student_transcript.txt`, различай объяснения
   преподавателя, самостоятельные ответы, вопросы и ошибки ученика.
4. Параметры ученика, класса, экзамена, предмета, темы и даты бери из `lesson.json`.
5. Промпты находятся в этом же репозитории:
   - `pipline/prompts/01_latex_handbook.md`;
   - `pipline/prompts/02_educational_poster.md`;
   - `pipline/prompts/03_web_equivalent.md`.
6. Разрешай конфликтующие плейсхолдеры плаката по данным занятия и готовому пособию.
7. На ученических страницах скрывай сведения о промптах, конвейере, покрытии и технических
   проверках.

## Машина состояний

### `ready_for_generation`: создать LaTeX

1. Выполни `pipline/prompts/01_latex_handbook.md`.
2. Сохрани результат в `handbook/<DD.MM.YY>.tex`.
3. Проверь полноту тем, задач, решений и ответов по транскрипту.
4. Создай или обнови `reports/coverage.json` и `reports/validation.json` для этапа LaTeX.
5. Обнови `lesson.json.status` на `generated_tex`.
6. Обнови `job.status.json`:

```json
{
  "schema_version": "1.0",
  "lesson_id": "<lesson_id>",
  "status": "generated_tex",
  "stage": "latex",
  "updated_at": "<ISO-8601 UTC>",
  "artifacts": {
    "tex": "completed",
    "pdf": "waiting_for_local_compiler",
    "poster": "pending",
    "web": "pending",
    "index": "pending"
  }
}
```

7. Сделай commit в head-ветку и заверши текущий запуск. Локальный Tutor Assistant обнаружит
   новую версию TEX, безопасно скомпилирует PDF и отправит результат в эту же ветку.

### `compile_failed`: исправить LaTeX

1. Прочитай `reports/latex/latex_fix_request.md`, `reports/latex/compilation.log` и сведения
   `latex` из `job.status.json`.
2. Если `attempt >= max_attempts`, установи `status=failed`, сохрани краткую причину и требуемое
   действие преподавателя.
3. В остальных случаях исправь только блокирующие ошибки в существующем `.tex`.
4. Сохрани `status=generated_tex`, `stage=latex`, новое `updated_at` и
   `artifacts.pdf=waiting_for_local_compiler`.
5. Сделай commit и заверши текущий запуск.

### `pdf_review_required`: создать плакат и web-эквивалент

1. Убедись, что `handbook/<DD.MM.YY>.pdf` существует и отчёт компиляции не содержит
   блокирующих ошибок.
2. Выполни `pipline/prompts/02_educational_poster.md` на основе транскрипта и готового пособия.
3. Сохрани плакат в `poster/<DD.MM.YY>.png`.
4. Выполни `pipline/prompts/03_web_equivalent.md` и сохрани страницу в
   `web/<DD-MM-YY>.html`.
5. Добавь в web-страницу свёрнутый блок плаката и ссылки для скачивания `.tex` и `.pdf`.
6. Обнови главный `index.html` ученика в существующем дизайне.
7. Проверь относительные ссылки, соответствие пособию, комплектность упражнений и ответов,
   MathML, мобильную версию и печатные стили.
8. Обнови `reports/coverage.json` и `reports/validation.json` итоговыми результатами.
9. Обнови `lesson.json.status` на `completed` и `job.status.json`:

```json
{
  "schema_version": "1.0",
  "lesson_id": "<lesson_id>",
  "status": "completed",
  "stage": "completed",
  "updated_at": "<ISO-8601 UTC>",
  "artifacts": {
    "tex": "completed",
    "pdf": "completed",
    "poster": "completed",
    "web": "completed",
    "index": "completed"
  }
}
```

10. Сделай commit в head-ветку. Оставь PR в draft для проверки преподавателем.

## Ошибки и повторный запуск

- При временной ошибке GitHub или инструмента не меняй статус задания. Сообщи, какой этап можно
  безопасно повторить.
- При блокирующей ошибке данных установи `status=failed`, добавь объект `error` с полями
  `message`, `stage` и `required_action`.
- Не добавляй занятие в навигатор ученика до завершения PDF, плаката, web-страницы и проверок.
- Если нужный артефакт уже существует и прошёл проверку, используй его повторно. Не создавай
  дубликаты с другим именем.
- В итоговом сообщении укажи PR, обработанный статус, commit и следующий ожидаемый исполнитель:
  Tutor Assistant, ChatGPT Work или преподаватель.
