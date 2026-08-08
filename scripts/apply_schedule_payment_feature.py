from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def replace_once(path: str, old: str, new: str) -> None:
    target = ROOT / path
    text = target.read_text(encoding="utf-8")
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f"{path}: expected one match, found {count}: {old[:80]!r}")
    target.write_text(text.replace(old, new, 1), encoding="utf-8")


def append_once(path: str, marker: str, content: str) -> None:
    target = ROOT / path
    text = target.read_text(encoding="utf-8")
    if marker in text:
        raise RuntimeError(f"{path}: marker already present: {marker}")
    target.write_text(text.rstrip() + "\n\n\n" + content.strip() + "\n", encoding="utf-8")


# Domain model and SQLite schema.
replace_once(
    "src/tutor_assistant/crm.py",
    "    status: str = \"planned\"\n    rate_cents: int = 0\n    lesson_id: str | None = None\n",
    "    status: str = \"planned\"\n    rate_cents: int = 0\n    paid: bool = False\n    lesson_id: str | None = None\n",
)
replace_once(
    "src/tutor_assistant/crm.py",
    "                    status TEXT NOT NULL DEFAULT 'planned',\n                    rate_cents INTEGER NOT NULL DEFAULT 0,\n                    lesson_id TEXT,\n",
    "                    status TEXT NOT NULL DEFAULT 'planned',\n                    rate_cents INTEGER NOT NULL DEFAULT 0,\n                    paid INTEGER NOT NULL DEFAULT 0,\n                    lesson_id TEXT,\n",
)
replace_once(
    "src/tutor_assistant/crm.py",
    "                \"\"\"\n            )\n\n    @staticmethod\n    def _now() -> str:\n",
    "                \"\"\"\n            )\n            self._ensure_schema_upgrades(db)\n\n    @staticmethod\n    def _ensure_schema_upgrades(db: sqlite3.Connection) -> None:\n        columns = {\n            str(row[\"name\"])\n            for row in db.execute(\"PRAGMA table_info(crm_lesson_occurrences)\")\n        }\n        if \"paid\" not in columns:\n            db.execute(\n                \"ALTER TABLE crm_lesson_occurrences \"\n                \"ADD COLUMN paid INTEGER NOT NULL DEFAULT 0\"\n            )\n\n    @staticmethod\n    def _now() -> str:\n",
)

# Persist payment state for one-off and materialized recurring occurrences.
replace_once(
    "src/tutor_assistant/crm.py",
    "                    topic, meeting_secret, status, rate_cents, lesson_id, created_at, updated_at\n                ) VALUES (NULL, NULL, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)\n",
    "                    topic, meeting_secret, status, rate_cents, paid, lesson_id, created_at, updated_at\n                ) VALUES (NULL, NULL, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)\n",
)
replace_once(
    "src/tutor_assistant/crm.py",
    "                    lesson.status,\n                    lesson.rate_cents,\n                    lesson.lesson_id,\n                    now,\n                    now,\n",
    "                    lesson.status,\n                    lesson.rate_cents,\n                    int(lesson.paid),\n                    lesson.lesson_id,\n                    now,\n                    now,\n",
)
replace_once(
    "src/tutor_assistant/crm.py",
    "                    topic, meeting_secret, status, rate_cents, lesson_id, created_at, updated_at\n                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)\n",
    "                    topic, meeting_secret, status, rate_cents, paid, lesson_id, created_at, updated_at\n                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)\n",
)
replace_once(
    "src/tutor_assistant/crm.py",
    "                    lesson.status,\n                    lesson.rate_cents,\n                    lesson.lesson_id,\n                    now,\n                    now,\n                ),\n            )\n            row = db.execute(\n",
    "                    lesson.status,\n                    lesson.rate_cents,\n                    int(lesson.paid),\n                    lesson.lesson_id,\n                    now,\n                    now,\n                ),\n            )\n            row = db.execute(\n",
)
replace_once(
    "src/tutor_assistant/crm.py",
    "            rate_cents=row[\"rate_cents\"],\n            lesson_id=row[\"lesson_id\"],\n",
    "            rate_cents=row[\"rate_cents\"],\n            paid=bool(row[\"paid\"]),\n            lesson_id=row[\"lesson_id\"],\n",
)
replace_once(
    "src/tutor_assistant/crm.py",
    "        return int(row[\"id\"])\n\n    def update_occurrence(\n",
    "        return int(row[\"id\"])\n\n    def set_lesson_paid(self, lesson: ScheduledLesson, paid: bool) -> int:\n        occurrence_id = self.ensure_occurrence(lesson)\n\n        def operation() -> None:\n            with self.connect() as db:\n                db.execute(\n                    \"\"\"\n                    UPDATE crm_lesson_occurrences\n                    SET paid=?, updated_at=?\n                    WHERE id=?\n                    \"\"\",\n                    (int(paid), self._now(), occurrence_id),\n                )\n\n        self._retry(operation)\n        return occurrence_id\n\n    def update_occurrence(\n",
)

# Schedule UI: preserve payment state through editing and expose a checkable slot.
replace_once(
    "src/tutor_assistant/ui/crm.py",
    "from PySide6.QtCore import QDate, Qt, QTime, Signal\n",
    "from PySide6.QtCore import QDate, QSignalBlocker, Qt, QTime, Signal\n",
)
replace_once(
    "src/tutor_assistant/ui/crm.py",
    "            status=self.lesson.status if self.lesson else \"planned\",\n            rate_cents=round(self.rate.value() * 100),\n            lesson_id=self.lesson.lesson_id if self.lesson else None,\n",
    "            status=self.lesson.status if self.lesson else \"planned\",\n            rate_cents=round(self.rate.value() * 100),\n            paid=self.lesson.paid if self.lesson else False,\n            lesson_id=self.lesson.lesson_id if self.lesson else None,\n",
)
replace_once(
    "src/tutor_assistant/ui/crm.py",
    "        self.grid.currentCellChanged.connect(self._sync_schedule_action)\n        self.grid.cellDoubleClicked.connect(self._cell_opened)\n",
    "        self.grid.currentCellChanged.connect(self._sync_schedule_action)\n        self.grid.cellDoubleClicked.connect(self._cell_opened)\n        self.grid.itemChanged.connect(self._payment_state_changed)\n",
)
replace_once(
    "src/tutor_assistant/ui/crm.py",
    "        self.week_label.setText(\n            f\"{self.week_start:%d.%m.%Y} — {end:%d.%m.%Y} · выберите ячейку для действия\"\n        )\n        self.grid.clearSpans()\n",
    "        self.week_label.setText(\n            f\"{self.week_start:%d.%m.%Y} — {end:%d.%m.%Y} · выберите ячейку для действия\"\n        )\n        signal_blocker = QSignalBlocker(self.grid)\n        self.grid.clearSpans()\n",
)
replace_once(
    "src/tutor_assistant/ui/crm.py",
    "            item = QTableWidgetItem(\n                f\"{lesson.starts_at:%H:%M}–{lesson.ends_at:%H:%M}  {lesson.student_name}\\n\"\n                f\"{subject_label(lesson.subject)}\"\n                + (f\" · {lesson.topic}\" if lesson.topic else \"\")\n                + f\"\\n{status_names.get(lesson.status, lesson.status)}\"\n            )\n            item.setTextAlignment(Qt.AlignLeft | Qt.AlignTop)\n            item.setToolTip(\n                f\"{lesson.student_name}\\n{lesson.starts_at:%d.%m %H:%M}\"\n                f\"–{lesson.ends_at:%H:%M}\\n{lesson.topic or lesson.subject}\\n\"\n                \"Выберите занятие и нажмите «Открыть занятие»\"\n            )\n            item.setBackground(colors.get(lesson.status, QColor(\"#FFFFFF\")))\n",
    "            payment_label = \"Оплачено\" if lesson.paid else \"Не оплачено\"\n            item = QTableWidgetItem(\n                f\"{lesson.starts_at:%H:%M}–{lesson.ends_at:%H:%M}  {lesson.student_name}\\n\"\n                f\"{subject_label(lesson.subject)}\"\n                + (f\" · {lesson.topic}\" if lesson.topic else \"\")\n                + f\"\\n{status_names.get(lesson.status, lesson.status)} · {payment_label}\"\n            )\n            item.setTextAlignment(Qt.AlignLeft | Qt.AlignTop)\n            item.setToolTip(\n                f\"{lesson.student_name}\\n{lesson.starts_at:%d.%m %H:%M}\"\n                f\"–{lesson.ends_at:%H:%M}\\n{lesson.topic or lesson.subject}\\n\"\n                f\"Оплата: {payment_label}\\n\"\n                \"Галочка в слоте отмечает оплату занятия. \"\n                \"Выберите занятие и нажмите «Открыть занятие»\"\n            )\n            item.setCheckState(\n                Qt.CheckState.Checked if lesson.paid else Qt.CheckState.Unchecked\n            )\n            if lesson.status == \"cancelled\":\n                item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsUserCheckable)\n                item.setBackground(colors[\"cancelled\"])\n            elif not lesson.paid:\n                item.setBackground(QColor(\"#FFF0F0\"))\n                item.setForeground(QColor(\"#9B2C2C\"))\n            else:\n                item.setBackground(colors.get(lesson.status, QColor(\"#FFFFFF\")))\n",
)
replace_once(
    "src/tutor_assistant/ui/crm.py",
    "        self.revenue_stat.setText(f\"План · {stats.planned_revenue_cents / 100:,.0f} ₽\")\n        self._sync_schedule_action()\n\n    def _sync_schedule_action(self, *_args) -> None:\n",
    "        self.revenue_stat.setText(f\"План · {stats.planned_revenue_cents / 100:,.0f} ₽\")\n        del signal_blocker\n        self._sync_schedule_action()\n\n    def _payment_state_changed(self, item: QTableWidgetItem) -> None:\n        lesson = self.cell_lessons.get((item.row(), item.column()))\n        if lesson is None or lesson.status == \"cancelled\":\n            return\n        paid = item.checkState() == Qt.CheckState.Checked\n        if paid == lesson.paid:\n            return\n        previous = lesson.paid\n        try:\n            occurrence_id = self.store.set_lesson_paid(lesson, paid)\n        except Exception as exc:\n            signal_blocker = QSignalBlocker(self.grid)\n            item.setCheckState(\n                Qt.CheckState.Checked if previous else Qt.CheckState.Unchecked\n            )\n            del signal_blocker\n            QMessageBox.critical(\n                self,\n                \"Оплата занятия\",\n                f\"Не удалось сохранить состояние оплаты: {exc}\",\n            )\n            return\n        lesson.occurrence_id = occurrence_id\n        lesson.paid = paid\n        self.refresh()\n\n    def _sync_schedule_action(self, *_args) -> None:\n",
)

# Unit tests.
replace_once(
    "tests/test_crm.py",
    "    ScheduleConflict,\n    ScheduleRule,\n    StudentProfile,\n",
    "    ScheduleConflict,\n    ScheduledLesson,\n    ScheduleRule,\n    StudentProfile,\n",
)
append_once(
    "tests/test_crm.py",
    "test_existing_crm_database_adds_paid_column",
    r'''
def test_existing_crm_database_adds_paid_column(tmp_path: Path) -> None:
    path = tmp_path / "legacy.sqlite3"
    with sqlite3.connect(path) as db:
        db.execute(
            """
            CREATE TABLE crm_lesson_occurrences (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                rule_id INTEGER,
                original_date TEXT,
                student_id TEXT NOT NULL,
                starts_at TEXT NOT NULL,
                duration_minutes INTEGER NOT NULL,
                subject TEXT NOT NULL,
                topic TEXT NOT NULL DEFAULT '',
                meeting_secret TEXT,
                status TEXT NOT NULL DEFAULT 'planned',
                rate_cents INTEGER NOT NULL DEFAULT 0,
                lesson_id TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                UNIQUE(rule_id, original_date)
            )
            """
        )
        db.execute(
            """
            INSERT INTO crm_lesson_occurrences (
                student_id, starts_at, duration_minutes, subject,
                created_at, updated_at
            ) VALUES ('legacy', '2026-08-03T16:00:00', 60, 'mathematics', 'now', 'now')
            """
        )

    store = CrmStore(path, TestCodec())

    with sqlite3.connect(store.path) as db:
        columns = {row[1] for row in db.execute("PRAGMA table_info(crm_lesson_occurrences)")}
        paid = db.execute("SELECT paid FROM crm_lesson_occurrences").fetchone()[0]
    assert "paid" in columns
    assert paid == 0


def test_recurring_payment_materializes_only_selected_date(store: CrmStore) -> None:
    store.sync_students([Student(id="paid-student", full_name="Оплата")])
    store.save_schedule_rule(
        ScheduleRule(
            student_id="paid-student",
            weekday=1,
            start_minute=16 * 60,
            valid_from=date(2026, 8, 1),
            rate_cents=300_000,
        )
    )
    first_week = date(2026, 8, 3)
    lesson = store.lessons_for_week(first_week)[0]
    assert lesson.occurrence_id is None
    assert lesson.paid is False

    occurrence_id = store.set_lesson_paid(lesson, True)

    paid_lesson = store.lessons_for_week(first_week)[0]
    next_lesson = store.lessons_for_week(date(2026, 8, 10))[0]
    assert paid_lesson.occurrence_id == occurrence_id
    assert paid_lesson.paid is True
    assert next_lesson.occurrence_id is None
    assert next_lesson.paid is False
    with sqlite3.connect(store.path) as db:
        assert db.execute("SELECT COUNT(*) FROM crm_lesson_occurrences").fetchone()[0] == 1

    store.set_lesson_paid(paid_lesson, True)
    store.set_lesson_paid(paid_lesson, False)
    assert store.lessons_for_week(first_week)[0].paid is False
    with sqlite3.connect(store.path) as db:
        assert db.execute("SELECT COUNT(*) FROM crm_lesson_occurrences").fetchone()[0] == 1


def test_paid_state_survives_occurrence_detail_edit(store: CrmStore) -> None:
    store.sync_students([Student(id="one-off-paid", full_name="Разовое занятие")])
    week_start = date(2026, 8, 3)
    occurrence_id = store.save_one_off(
        ScheduledLesson(
            student_id="one-off-paid",
            student_name="Разовое занятие",
            starts_at=datetime(2026, 8, 5, 17, 0),
            duration_minutes=60,
            subject="mathematics",
            topic="Исходная тема",
        )
    )
    lesson = store.lessons_for_week(week_start)[0]
    store.set_lesson_paid(lesson, True)

    edited = lesson.model_copy(update={"topic": "Новая тема", "paid": True})
    store.update_occurrence_details(occurrence_id, edited)

    restored = store.lessons_for_week(week_start)[0]
    assert restored.topic == "Новая тема"
    assert restored.paid is True
''',
)

# GUI tests.
replace_once(
    "tests/test_ux3_crm_materials_gui.py",
    "from __future__ import annotations\n\nfrom datetime import date, datetime\n",
    "from __future__ import annotations\n\nimport sqlite3\nfrom datetime import date, datetime\n",
)
replace_once(
    "tests/test_ux3_crm_materials_gui.py",
    "from PySide6.QtCore import QObject, Signal\n",
    "from PySide6.QtCore import QObject, Qt, Signal\n",
)
replace_once(
    "tests/test_ux3_crm_materials_gui.py",
    "from PySide6.QtWidgets import QApplication, QMessageBox\n",
    "from PySide6.QtTest import QTest\nfrom PySide6.QtWidgets import QApplication, QMessageBox\n",
)
replace_once(
    "tests/test_ux3_crm_materials_gui.py",
    "from tutor_assistant.crm import CrmStore, ScheduledLesson, StudentProfile\n",
    "from tutor_assistant.crm import CrmStore, ScheduledLesson, ScheduleRule, StudentProfile\n",
)
append_once(
    "tests/test_ux3_crm_materials_gui.py",
    "test_schedule_payment_checkbox_persists_and_isolates_recurring_dates",
    r'''
def test_schedule_payment_checkbox_persists_and_isolates_recurring_dates(
    tmp_path: Path,
    application: QApplication,
) -> None:
    store = CrmStore(tmp_path / "schedule-payment.sqlite3", TestCodec())
    store.save_student(StudentProfile(id="student", full_name="Ученик"), [])
    store.save_schedule_rule(
        ScheduleRule(
            student_id="student",
            weekday=0,
            start_minute=16 * 60,
            duration_minutes=90,
            subject="mathematics",
            topic="Оплата занятия",
            valid_from=date(2026, 8, 1),
            rate_cents=300_000,
        )
    )
    page = SchedulePage(store)
    page.week_start = date(2026, 8, 3)

    with sqlite3.connect(store.path) as db:
        before_refresh = db.execute("SELECT COUNT(*) FROM crm_lesson_occurrences").fetchone()[0]
    page.refresh()
    with sqlite3.connect(store.path) as db:
        after_refresh = db.execute("SELECT COUNT(*) FROM crm_lesson_occurrences").fetchone()[0]
    assert before_refresh == after_refresh == 0

    page.show()
    application.processEvents()
    row = page._row_for_time(16, 0)
    item = page.grid.item(row, 0)
    assert item is not None
    assert item.checkState() == Qt.CheckState.Unchecked
    assert "Не оплачено" in item.text()
    assert item.background().color().name().upper() == "#FFF0F0"
    assert page.grid.rowSpan(row, 0) == 3

    page.grid.setCurrentCell(row, 0)
    QTest.keyClick(page.grid, Qt.Key.Key_Space)
    application.processEvents()

    paid_item = page.grid.item(row, 0)
    assert paid_item is not None
    assert paid_item.checkState() == Qt.CheckState.Checked
    assert "Оплачено" in paid_item.text()
    with sqlite3.connect(store.path) as db:
        assert db.execute("SELECT COUNT(*) FROM crm_lesson_occurrences").fetchone()[0] == 1
        assert db.execute("SELECT paid FROM crm_lesson_occurrences").fetchone()[0] == 1

    page.week_start = date(2026, 8, 10)
    page.refresh()
    next_row = page._row_for_time(16, 0)
    next_item = page.grid.item(next_row, 0)
    assert next_item is not None
    assert next_item.checkState() == Qt.CheckState.Unchecked

    page.week_start = date(2026, 8, 3)
    page.refresh()
    restored_item = page.grid.item(row, 0)
    assert restored_item is not None
    assert restored_item.checkState() == Qt.CheckState.Checked

    paid_lesson = page.cell_lessons[(row, 0)]
    assert paid_lesson.occurrence_id is not None
    store.update_occurrence(paid_lesson.occurrence_id, status="cancelled")
    page.refresh()
    cancelled = page.grid.item(row, 0)
    assert cancelled is not None
    assert not bool(cancelled.flags() & Qt.ItemFlag.ItemIsUserCheckable)
    assert cancelled.background().color().name().upper() == "#F2F4F7"
    page.close()
''',
)

# User-facing documentation.
replace_once(
    "README.md",
    "- Расписание использует 30-минутную сетку и отображает длительность занятия высотой блока.\n",
    "- Расписание использует 30-минутную сетку и отображает длительность занятия высотой блока.\n- В слоте занятия можно отметить оплату; неоплаченные занятия подсвечиваются, а отметка сохраняется отдельно для каждой даты серии.\n",
)
