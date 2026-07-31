from __future__ import annotations

from pathlib import Path
from typing import Any

from . import remote as _remote
from .compiler import LatexCompiler
from .diagnostics import inspect_latex_environment
from .models import CompilationResult, EnvironmentReport
from .remote import (
    LatexCompilationReservation,
    RemoteCompilationResult,
    RemoteLatexService,
    RemoteRepositoryUnavailable,
    RemoteTexProbe,
)
from .validator import validate_tex

_original_remote_run_git = _remote.run_git


def _read_only_remote_git(repo: Path, *args: str, **kwargs: Any) -> str:
    if args and args[0] == "push":
        raise RuntimeError(
            "Remote LaTeX работает в read-only режиме; публикация PDF, JSON и отчётов запрещена"
        )
    return _original_remote_run_git(repo, *args, **kwargs)


_remote.run_git = _read_only_remote_git

__all__ = [
    "CompilationResult",
    "EnvironmentReport",
    "LatexCompilationReservation",
    "LatexCompiler",
    "RemoteCompilationResult",
    "RemoteLatexService",
    "RemoteRepositoryUnavailable",
    "RemoteTexProbe",
    "inspect_latex_environment",
    "validate_tex",
]
