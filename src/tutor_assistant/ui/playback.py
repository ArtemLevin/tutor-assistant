from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from PySide6.QtCore import QObject, Qt, QTimer, QUrl, Signal
from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer
from PySide6.QtWidgets import (
    QComboBox,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSlider,
    QVBoxLayout,
    QWidget,
)

from ..playback import (
    PlaybackController,
    PlaybackSegment,
    audio_track_label,
    format_playback_time,
)
from .theme import set_button_kind


class QtPlaybackBackend(QObject):
    position_changed = Signal(int)
    duration_changed = Signal(int)
    playing_changed = Signal(bool)
    error_occurred = Signal(str)

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self.player = QMediaPlayer(self)
        self.audio_output = QAudioOutput(self)
        self.player.setAudioOutput(self.audio_output)
        self.player.positionChanged.connect(self.position_changed.emit)
        self.player.durationChanged.connect(self.duration_changed.emit)
        self.player.playbackStateChanged.connect(self._state_changed)
        self.player.errorOccurred.connect(self._error_occurred)

    def load(self, path: Path) -> None:
        self.player.setSource(QUrl.fromLocalFile(str(path.resolve())))

    def play(self) -> None:
        self.player.play()

    def pause(self) -> None:
        self.player.pause()

    def stop(self) -> None:
        self.player.stop()

    def set_position(self, position_ms: int) -> None:
        self.player.setPosition(position_ms)

    def position_ms(self) -> int:
        return self.player.position()

    def set_rate(self, rate: float) -> None:
        self.player.setPlaybackRate(rate)

    def is_playing(self) -> bool:
        return self.player.playbackState() == QMediaPlayer.PlaybackState.PlayingState

    def _state_changed(self, state: QMediaPlayer.PlaybackState) -> None:
        self.playing_changed.emit(state == QMediaPlayer.PlaybackState.PlayingState)

    def _error_occurred(self, error: QMediaPlayer.Error, message: str) -> None:
        if error != QMediaPlayer.Error.NoError:
            self.error_occurred.emit(message or self.player.errorString())


class QtStopScheduler(QObject):
    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self.timer = QTimer(self)
        self.timer.setSingleShot(True)
        self.timer.timeout.connect(self._run)
        self.callback: Callable[[], None] | None = None

    def schedule(self, delay_ms: int, callback: Callable[[], None]) -> None:
        self.callback = callback
        self.timer.start(delay_ms)

    def cancel(self) -> None:
        self.timer.stop()
        self.callback = None

    def _run(self) -> None:
        callback = self.callback
        self.callback = None
        if callback:
            callback()


class PlaybackPanel(QGroupBox):
    status_changed = Signal(str, str)

    def __init__(
        self,
        controller: PlaybackController,
        backend: QtPlaybackBackend,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__("Аудиозапись", parent)
        self.controller = controller
        self.backend = backend
        self.segments: tuple[PlaybackSegment, ...] = ()
        self._seeking = False
        self._resume_after_seek = False
        self._build()
        backend.position_changed.connect(self._position_changed)
        backend.duration_changed.connect(self._duration_changed)
        backend.playing_changed.connect(self._playing_changed)
        backend.error_occurred.connect(self._backend_error)
        self.reset()

    def _build(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 10, 12, 10)
        layout.setSpacing(8)

        selectors = QHBoxLayout()
        self.track = QComboBox()
        self.track.setMinimumWidth(180)
        self.track.setToolTip("Выберите смешанную дорожку, преподавателя или ученика")
        self.track.currentIndexChanged.connect(self._track_changed)
        selectors.addWidget(self.track, 1)
        speed_label = QLabel("Скорость")
        speed_label.setObjectName("muted")
        selectors.addWidget(speed_label)
        self.speed = QComboBox()
        for label, value in (
            ("0,75×", 0.75),
            ("1×", 1.0),
            ("1,25×", 1.25),
            ("1,5×", 1.5),
            ("2×", 2.0),
        ):
            self.speed.addItem(label, value)
        self.speed.setCurrentIndex(1)
        self.speed.currentIndexChanged.connect(self._speed_changed)
        selectors.addWidget(self.speed)
        layout.addLayout(selectors)

        transport = QHBoxLayout()
        self.previous = set_button_kind(QPushButton("← Сегмент"), "ghost")
        self.previous.clicked.connect(lambda: self._step_segment(-1))
        transport.addWidget(self.previous)
        self.play_pause = set_button_kind(QPushButton("▶"), "primary")
        self.play_pause.setFixedWidth(48)
        self.play_pause.setToolTip("Воспроизвести или приостановить")
        self.play_pause.clicked.connect(self.toggle)
        transport.addWidget(self.play_pause)
        self.stop_button = set_button_kind(QPushButton("■"), "ghost")
        self.stop_button.setFixedWidth(42)
        self.stop_button.setToolTip("Остановить")
        self.stop_button.clicked.connect(self.stop)
        transport.addWidget(self.stop_button)
        self.next = set_button_kind(QPushButton("Сегмент →"), "ghost")
        self.next.clicked.connect(lambda: self._step_segment(1))
        transport.addWidget(self.next)
        self.position = QSlider(Qt.Horizontal)
        self.position.setRange(0, 0)
        self.position.sliderPressed.connect(self._seek_started)
        self.position.sliderReleased.connect(self._seek_finished)
        transport.addWidget(self.position, 1)
        self.time = QLabel("00:00 / 00:00")
        self.time.setObjectName("muted")
        transport.addWidget(self.time)
        layout.addLayout(transport)

        segment_row = QHBoxLayout()
        segment_label = QLabel("Переход")
        segment_label.setObjectName("muted")
        segment_row.addWidget(segment_label)
        self.segment = QComboBox()
        self.segment.setMinimumContentsLength(24)
        self.segment.activated.connect(self.play_selected_segment)
        segment_row.addWidget(self.segment, 1)
        layout.addLayout(segment_row)

        self.state = QLabel("Для занятия нет доступной аудиозаписи")
        self.state.setObjectName("muted")
        self.state.setWordWrap(True)
        layout.addWidget(self.state)

    def set_tracks(self, paths: list[Path]) -> None:
        self.stop(clear_source=True)
        unique = {path.resolve() for path in paths if path.is_file()}
        priority = {"Смешанная дорожка": 0, "Преподаватель": 1, "Ученик": 2}
        ordered = sorted(
            unique,
            key=lambda path: (priority.get(audio_track_label(path), 3), path.name.casefold()),
        )
        self.track.blockSignals(True)
        self.track.clear()
        for path in ordered:
            self.track.addItem(audio_track_label(path), str(path))
        self.track.blockSignals(False)
        enabled = bool(ordered)
        self.track.setEnabled(enabled)
        self.play_pause.setEnabled(enabled)
        self.stop_button.setEnabled(enabled)
        self.state.setText("Готово к воспроизведению" if enabled else "Для занятия нет доступной аудиозаписи")

    def set_segments(self, segments: tuple[PlaybackSegment, ...], error: str | None = None) -> None:
        self.segments = segments
        self.segment.blockSignals(True)
        self.segment.clear()
        for item in segments:
            speaker = f" · {item.speaker}" if item.speaker else ""
            text = f" · {item.text[:60]}" if item.text else ""
            self.segment.addItem(f"{format_playback_time(item.start_ms)}{speaker}{text}")
        self.segment.blockSignals(False)
        enabled = bool(segments) and self.track.count() > 0
        self.segment.setEnabled(enabled)
        self.previous.setEnabled(enabled)
        self.next.setEnabled(enabled)
        if error and self.track.count() > 0:
            self.state.setText(error)

    def reset(self) -> None:
        self.set_tracks([])
        self.set_segments(())
        self.position.setRange(0, 0)
        self.time.setText("00:00 / 00:00")

    def current_path(self) -> Path | None:
        value = self.track.currentData()
        return Path(str(value)) if value else None

    def play_path(self, path: Path) -> None:
        resolved = path.resolve()
        index = self.track.findData(str(resolved))
        if index < 0 and resolved.is_file():
            self.track.addItem(audio_track_label(resolved), str(resolved))
            index = self.track.count() - 1
        if index >= 0:
            self.track.setCurrentIndex(index)
            self.toggle()

    def toggle(self) -> None:
        path = self.current_path()
        if path is None:
            return
        if self.controller.toggle(path, rate=float(self.speed.currentData())):
            self.state.setText(f"Дорожка: {self.track.currentText()}")
            self.status_changed.emit("Аудиоплеер готов", "working")
        else:
            self._show_error(self.controller.last_error)

    def play_selected_segment(self, index: int | None = None) -> None:
        path = self.current_path()
        row = self.segment.currentIndex() if index is None else index
        if path is None or not 0 <= row < len(self.segments):
            return
        self.segment.setCurrentIndex(row)
        if self.controller.play_segment(
            path,
            self.segments[row],
            rate=float(self.speed.currentData()),
        ):
            self.state.setText(f"Воспроизводится сегмент {row + 1} из {len(self.segments)}")
        else:
            self._show_error(self.controller.last_error)

    def stop(self, *, clear_source: bool = False) -> None:
        self.controller.stop(clear_source=clear_source)
        self.play_pause.setText("▶")

    def _track_changed(self, _index: int) -> None:
        self.stop(clear_source=True)
        self.position.setValue(0)
        if self.current_path():
            self.state.setText(f"Выбрана дорожка: {self.track.currentText()}")

    def _speed_changed(self, _index: int) -> None:
        if not self.controller.set_rate(float(self.speed.currentData())):
            self._show_error(self.controller.last_error)

    def _step_segment(self, step: int) -> None:
        if not self.segments:
            return
        target = min(max(0, self.segment.currentIndex() + step), len(self.segments) - 1)
        self.play_selected_segment(target)

    def _seek_started(self) -> None:
        self._seeking = True
        self._resume_after_seek = self.backend.is_playing()
        if self._resume_after_seek:
            self.controller.pause()

    def _seek_finished(self) -> None:
        self._seeking = False
        path = self.current_path()
        if path is None or not self.controller.seek(path, self.position.value()):
            self._resume_after_seek = False
            if self.controller.last_error:
                self._show_error(self.controller.last_error)
            return
        if self._resume_after_seek:
            resumed = self.controller.play_file(
                path,
                rate=float(self.speed.currentData()),
                start_ms=self.position.value(),
            )
            if not resumed:
                self._show_error(self.controller.last_error)
        self._resume_after_seek = False

    def _position_changed(self, position_ms: int) -> None:
        if not self._seeking:
            self.position.setValue(position_ms)
        self.time.setText(
            f"{format_playback_time(position_ms)} / {format_playback_time(self.position.maximum())}"
        )

    def _duration_changed(self, duration_ms: int) -> None:
        self.position.setRange(0, max(0, duration_ms))
        self._position_changed(self.backend.position_ms())

    def _playing_changed(self, playing: bool) -> None:
        self.play_pause.setText("❚❚" if playing else "▶")

    def _backend_error(self, message: str) -> None:
        self._show_error(f"Повреждённый или неподдерживаемый аудиофайл: {message}")

    def _show_error(self, message: str) -> None:
        self.state.setText(message)
        self.status_changed.emit("Ошибка воспроизведения", "error")
