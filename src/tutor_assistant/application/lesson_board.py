from __future__ import annotations

import base64
import hmac
import json
import logging
import re
import secrets
import threading
from collections.abc import Callable
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, quote, urlsplit

from ..content import ActivityLease, LessonEditConflictError
from ..domain import Lesson
from ..pipeline import LessonPipeline

_BOARD_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}")
_MAX_CALLBACK_PATH = 512


class BoardLaunchError(RuntimeError):
    """The browser could not establish a verified lesson-board association."""


class LocalBoardLaunch:
    """One-time loopback handoff; the browser owns Board API authentication.

    The secret exists only in memory and the URL fragment. It is never part of a
    server URL, durable lesson metadata or a Git publication artifact.
    """

    def __init__(
        self,
        *,
        lesson: Lesson,
        base_url: str,
        on_linked: Callable[[str], None],
        lifetime_seconds: float = 1800,
    ) -> None:
        self.lesson_id = lesson.lesson_id
        self.title = f"{lesson.lesson_date.isoformat()} · {lesson.subject} · {lesson.topic}"[:200]
        self.base_url = base_url.rstrip("/")
        self._token = secrets.token_urlsafe(32)
        self._on_linked = on_linked
        self._done = threading.Event()
        self._guard = threading.Lock()
        self._lifetime_seconds = lifetime_seconds
        self._server: ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None

    def start(self) -> str:
        if self._server is not None:
            raise BoardLaunchError("Board launch has already started")
        owner = self

        class CallbackHandler(BaseHTTPRequestHandler):
            def log_message(self, _format: str, *_args: object) -> None:
                # Never log the one-time callback token or browser query string.
                pass

            def do_OPTIONS(self) -> None:
                if (
                    self.headers.get("Origin") != owner.base_url
                    or self.headers.get("Host") != f"127.0.0.1:{self.server.server_port}"
                    or urlsplit(self.path).path != "/prepare"
                ):
                    self.send_error(403)
                    return
                self.send_response(204)
                self.send_header("Access-Control-Allow-Origin", owner.base_url)
                self.send_header("Access-Control-Allow-Methods", "GET, OPTIONS")
                self.send_header("Access-Control-Allow-Private-Network", "true")
                self.send_header("Vary", "Origin")
                self.send_header("Content-Length", "0")
                self.end_headers()

            def do_GET(self) -> None:
                if self.headers.get("Host") != f"127.0.0.1:{self.server.server_port}":
                    self.send_error(400)
                    return
                if len(self.path) > _MAX_CALLBACK_PATH:
                    self.send_error(414)
                    return
                url = urlsplit(self.path)
                try:
                    values = parse_qs(url.query, strict_parsing=True)
                except ValueError:
                    self.send_error(403)
                    return
                if url.path == "/prepare":
                    if (
                        self.headers.get("Origin") != owner.base_url
                        or set(values) != {"nonce"}
                        or len(values["nonce"]) != 1
                        or not hmac.compare_digest(values["nonce"][0], owner._token)
                        or owner._done.is_set()
                    ):
                        self.send_error(403)
                        return
                    body = b'{"valid":true}'
                    self.send_response(200)
                    self.send_header("Content-Type", "application/json")
                    self.send_header("Access-Control-Allow-Origin", owner.base_url)
                    self.send_header("Access-Control-Allow-Private-Network", "true")
                    self.send_header("Vary", "Origin")
                    self.send_header("Cache-Control", "no-store")
                    self.send_header("Content-Length", str(len(body)))
                    self.end_headers()
                    self.wfile.write(body)
                    return
                if (
                    url.path != "/complete"
                    or set(values) != {"nonce", "boardId"}
                    or len(values["nonce"]) != 1
                    or len(values["boardId"]) != 1
                    or not hmac.compare_digest(values["nonce"][0], owner._token)
                    or _BOARD_ID.fullmatch(values["boardId"][0]) is None
                ):
                    self.send_error(403)
                    return
                board_id = values["boardId"][0]
                with owner._guard:
                    if owner._done.is_set():
                        self.send_error(409)
                        return
                    try:
                        owner._on_linked(board_id)
                    except Exception:
                        logging.exception(
                            "Failed to persist linked TutorBoard for lesson=%s",
                            owner.lesson_id,
                        )
                        self.send_error(503)
                        return
                    owner._done.set()
                self.send_response(303)
                self.send_header("Location", f"{owner.base_url}/b/{quote(board_id, safe='')}#/board")
                self.send_header("Cache-Control", "no-store")
                self.send_header("Referrer-Policy", "no-referrer")
                self.send_header("X-Content-Type-Options", "nosniff")
                self.send_header("Content-Length", "0")
                self.end_headers()

        server = ThreadingHTTPServer(("127.0.0.1", 0), CallbackHandler)
        server.daemon_threads = True
        server.timeout = 0.5
        self._server = server
        callback = f"http://127.0.0.1:{server.server_port}/complete?nonce={self._token}"
        payload = {
            "version": 1,
            "lessonId": self.lesson_id,
            "title": self.title,
            "callback": callback,
        }
        encoded = base64.urlsafe_b64encode(
            json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        ).decode("ascii").rstrip("=")
        self._thread = threading.Thread(target=self._serve, name="tutorboard-link", daemon=True)
        self._thread.start()
        return f"{self.base_url}/boards#ta-start={encoded}"

    def _serve(self) -> None:
        import time

        server = self._server
        if server is None:
            return
        deadline = time.monotonic() + self._lifetime_seconds
        try:
            while not self._done.is_set() and time.monotonic() < deadline:
                server.handle_request()
        finally:
            server.server_close()

    def close(self) -> None:
        self._done.set()
        thread = self._thread
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout=2)


class LessonBoardCoordinator:
    """Record board associations independently of audio/ASR state transitions."""

    def __init__(self, pipeline: LessonPipeline, base_url: str) -> None:
        self.pipeline = pipeline
        self.base_url = base_url

    def _update_board(self, lesson_id: str, update: Callable[[Lesson], bool]) -> None:
        # Compare-and-swap the entire nested board object. Link and stop can
        # complete concurrently; stale views must never overwrite each other.
        for attempt in range(4):
            content = self.pipeline.content_service.get_lesson(lesson_id)
            stored = content.lesson
            if not update(stored):
                return
            try:
                self.pipeline.save_state(
                    stored, "board", expected_row_version=content.row_version
                )
                return
            except LessonEditConflictError:
                if attempt == 3:
                    raise

    def launch(
        self, lesson: Lesson, *, recording_lease: ActivityLease | None = None
    ) -> tuple[LocalBoardLaunch, str]:
        if not lesson.board.enabled or lesson.board.status != "pending":
            raise BoardLaunchError("Lesson board launch is disabled or already completed")
        if not self.base_url:
            raise BoardLaunchError("TutorBoard origin has not been configured")
        launch = LocalBoardLaunch(
            lesson=lesson,
            base_url=self.base_url,
            on_linked=lambda board_id: self.link(
                lesson.lesson_id, board_id, recording_lease=recording_lease
            ),
        )
        return launch, launch.start()

    def link(
        self,
        lesson_id: str,
        board_id: str,
        *,
        recording_lease: ActivityLease | None = None,
    ) -> None:
        if _BOARD_ID.fullmatch(board_id) is None:
            raise BoardLaunchError("Invalid board identifier")

        def update(stored: Lesson) -> bool:
            if not stored.board.enabled:
                raise BoardLaunchError("The lesson no longer permits a board association")
            if stored.board.board_id is not None:
                if stored.board.board_id == board_id:
                    return False
                raise BoardLaunchError("The lesson is already linked to a different board")
            if stored.board.status not in {"pending", "failed"}:
                raise BoardLaunchError("The lesson board launch is not pending")
            stored.board.board_id = board_id
            stored.board.status = "linked"
            stored.board.error = None
            if stored.board.recording_finished:
                stored.board.pdf_status = "pending"
            return True

        def persist_link() -> None:
            self._update_board(lesson_id, update)

        if recording_lease is not None and recording_lease.valid:
            # Live recording owns this lesson's write lease. Explicit delegation
            # keeps the original exclusion boundary intact in the callback thread.
            with self.pipeline.recording_lease_scope(recording_lease, lesson_id=lesson_id):
                persist_link()
        else:
            persist_link()

    def recording_finished(self, lesson_id: str) -> None:
        def update(stored: Lesson) -> bool:
            if not stored.board.enabled or stored.board.recording_finished:
                return False
            stored.board.recording_finished = True
            if stored.board.status == "linked":
                stored.board.pdf_status = "pending"
            return True

        self._update_board(lesson_id, update)

    def launch_failed(self, lesson_id: str, description: str) -> None:
        def update(stored: Lesson) -> bool:
            if not stored.board.enabled or stored.board.board_id:
                return False
            stored.board.status = "failed"
            stored.board.error = description[:250]
            return True

        self._update_board(lesson_id, update)
