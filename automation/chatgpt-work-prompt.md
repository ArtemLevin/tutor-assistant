# Tutor Assistant: обработка готовых занятий

Проверь репозиторий `ArtemLevin/students-26-27` и найди новые файлы `lesson.json`, для которых:

- `status` равен `ready_for_generation` или `published`, либо существует
  `reports/latex/latex_fix_request.md`;
- соседний `job.status.json` содержит `ready_for_generation`, `generated_tex` или `compile_failed`;
- готовые итоговые артефакты ещё отсутствуют.

Для каждого найденного занятия:

1. Прочитай `lesson.json` и все файлы каталога `source/`.
2. Используй подтверждённый `source/transcript.txt` как основной источник.
3. Выполни промпты строго по порядку:
   - `pipline/prompts/01_latex_handbook.md`;
   - `pipline/prompts/02_educational_poster.md`;
   - `pipline/prompts/03_web_equivalent.md`.
4. Параметры ученика, класса, экзамена, предмета, темы и даты бери из `lesson.json`.
5. Разрешай конфликтующие плейсхолдеры плаката по данным занятия и содержанию готового пособия.
6. Сохрани результаты внутри каталога занятия:
   - `handbook/<DD.MM.YY>.tex`;
   - `handbook/<DD.MM.YY>.pdf` создаёт локальный Tutor Assistant после появления TEX;
   - `poster/<DD.MM.YY>.png`;
   - `web/<DD-MM-YY>.html`;
   - `reports/coverage.json`;
   - `reports/validation.json`.
7. Сразу после создания `.tex` обнови `job.status.json`:

```json
{
  "status": "generated_tex",
  "stage": "latex",
  "artifacts": {
    "tex": "completed",
    "pdf": "waiting_for_local_compiler"
  }
}
```

Локальный Tutor Assistant обнаружит TEX, выполнит безопасную компиляцию и добавит PDF в эту же ветку.

Если появился `reports/latex/latex_fix_request.md`, прочитай его вместе с
`reports/latex/compilation.log`, исправь только блокирующие ошибки в исходном `.tex` и снова
установи `status=generated_tex`. Количество попыток ограничено полем `max_attempts` в отчёте.

8. Обнови главный `index.html` ученика в его существующем дизайне.
9. На ученических страницах не показывай сведения о промптах, конвейере, покрытии и технической проверке.
10. В web-страницу добавь свёрнутый блок плаката, который раскрывается по клику, и ссылки для скачивания `.tex` и доступного `.pdf`.
11. Проверь относительные ссылки, комплектность упражнений и ответов, MathML, мобильную версию и печатные стили.
12. После успеха и появления PDF замени содержимое `job.status.json` на:

```json
{
  "status": "completed"
}
```

При блокирующей ошибке запиши `status=failed`, краткое описание и требуемое действие. Не публикуй частично готовое занятие в навигаторе ученика.
