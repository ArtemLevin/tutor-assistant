UV ?= uv
UV_CACHE_DIR ?= .uv-cache
CONFIG ?= config/app.yaml
TEX ?=
LESSON ?=
RECORDING ?=

export UV_CACHE_DIR

.DEFAULT_GOAL := help

.PHONY: help init sync lock lock-check upgrade run setup doctor doctor-json doctor-strict \
	devices latex-doctor test coverage lint format format-check check build clean compile compile-remote \
	scan-latex recover support recovery-drill hardware-soak windows-build release-validate \
	privacy-history privacy-history-full

help: ## Показать список команд
	@$(UV) run --no-project python scripts/make_help.py

init: ## Установить все зависимости и создать рабочий конфиг
	$(UV) sync --all-extras
	$(UV) run python scripts/bootstrap.py --config "$(CONFIG)"

sync: ## Синхронизировать .venv, extras и dev-зависимости
	$(UV) sync --all-extras

lock: ## Создать или обновить uv.lock в рамках текущих ограничений
	$(UV) lock

lock-check: ## Проверить актуальность uv.lock
	$(UV) lock --check

upgrade: ## Обновить зафиксированные версии зависимостей
	$(UV) lock --upgrade
	$(UV) sync --all-extras

run: ## Запустить графическое приложение
	$(UV) run --all-extras tutor-assistant-gui "$(CONFIG)"

setup: ## Открыть мастер настройки
	$(UV) run --all-extras tutor-assistant-gui --setup "$(CONFIG)"

doctor: ## Выполнить полную диагностику окружения
	$(UV) run --all-extras tutor-assistant --config "$(CONFIG)" doctor

doctor-json: ## Вывести диагностический отчёт в JSON
	@$(UV) run --all-extras tutor-assistant --config "$(CONFIG)" doctor --json

doctor-strict: ## Завершиться с ошибкой при обязательной проблеме
	$(UV) run --all-extras tutor-assistant --config "$(CONFIG)" doctor --strict

support: ## Собрать безопасный ZIP диагностики
	$(UV) run --all-extras tutor-assistant --config "$(CONFIG)" support-bundle

recovery-drill: ## Проверить backup, rollback и восстановление аудио в отдельной песочнице
	$(UV) run --extra desktop tutor-assistant --config "$(CONFIG)" recovery-drill

hardware-soak: ## Показать текущий sanitized-отчёт аппаратных испытаний
	$(UV) run --extra desktop tutor-assistant --config "$(CONFIG)" hardware-soak

windows-build: ## Собрать portable Windows приложение из чистого checkout
	$(UV) run --extra desktop --extra transcription --extra packaging python scripts/build_windows.py --build

release-validate: ## Проверить версии, installer policy и Windows packaging contract
	$(UV) run --extra desktop python scripts/build_windows.py --validate-only

devices: ## Показать входные аудиоустройства
	$(UV) run --all-extras tutor-assistant --config "$(CONFIG)" devices

latex-doctor: ## Проверить TeX Live, движок и Poppler
	$(UV) run --all-extras tutor-assistant --config "$(CONFIG)" latex-doctor

test: ## Запустить тесты
	$(UV) run --all-extras pytest -q

coverage: ## Запустить тесты с измерением line и branch coverage без дополнительных зависимостей
	$(UV) run --all-extras python scripts/test_coverage.py --fail-under 70 --branch-fail-under 45 --output coverage.json -- -q

lint: ## Проверить код с Ruff
	$(UV) run --all-extras ruff check .

format: ## Отформатировать код с Ruff
	$(UV) run --all-extras ruff format .

format-check: ## Проверить форматирование без изменений
	$(UV) run --all-extras ruff format --check .

privacy-history: ## Проверить commits после privacy baseline
	$(UV) run --no-project python scripts/history_privacy.py audit --mode head

privacy-history-full: ## Проверить все refs и PRIVATE visibility
	$(UV) run --no-project python scripts/history_privacy.py audit --mode full --require-visibility

check: lock-check lint format-check test privacy-history ## Полная проверка перед публикацией
	$(UV) run --all-extras python -m compileall -q src tests scripts

build: check ## Собрать wheel и sdist
	$(UV) build

clean: ## Удалить кеши и результаты сборки
	$(UV) run --no-project python scripts/clean.py

compile: ## Скомпилировать TEX: make compile TEX=path/to/handout.tex
	$(if $(strip $(TEX)),,$(error Укажите путь: make compile TEX=path/to/handout.tex))
	$(UV) run --all-extras tutor-assistant --config "$(CONFIG)" compile "$(TEX)"

compile-remote: ## Скомпилировать занятие: make compile-remote LESSON=path/to/lesson.json
	$(if $(strip $(LESSON)),,$(error Укажите путь: make compile-remote LESSON=path/to/lesson.json))
	$(UV) run --all-extras tutor-assistant --config "$(CONFIG)" compile-remote "$(LESSON)"

scan-latex: ## Найти новый TEX в удалённых ветках занятий
	$(UV) run --all-extras tutor-assistant --config "$(CONFIG)" scan-latex

recover: ## Восстановить запись: make recover RECORDING=path/to/recording
	$(if $(strip $(RECORDING)),,$(error Укажите путь: make recover RECORDING=path/to/recording))
	$(UV) run --all-extras tutor-assistant-recover "$(RECORDING)"
# BEGIN AGENTKIT
-include .agent/Makefile.agent
# END AGENTKIT
