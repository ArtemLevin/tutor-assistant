from __future__ import annotations

import argparse
import dis
import json
import os
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from types import CodeType

import pytest


@dataclass(frozen=True, slots=True)
class ModuleCoverage:
    module: str
    statements: int
    covered_statements: int
    branches: int
    covered_branches: int

    @property
    def line_percent(self) -> float:
        return 100.0 if not self.statements else 100.0 * self.covered_statements / self.statements

    @property
    def branch_percent(self) -> float:
        return 100.0 if not self.branches else 100.0 * self.covered_branches / self.branches


class CoverageMonitor:
    def __init__(self, source: Path) -> None:
        self.source = source.resolve()
        self._source_prefix = str(self.source) + os.sep
        self._source_paths: dict[str, Path] = {}
        self.lines: dict[Path, set[int]] = {}
        self.branches: dict[tuple[Path, int, str, int], set[int]] = {}

    def _source_path(self, code: CodeType) -> Path | None:
        filename = code.co_filename
        if not filename.startswith(self._source_prefix):
            return None
        path = self._source_paths.get(filename)
        if path is None:
            path = Path(filename)
            self._source_paths[filename] = path
        return path

    def line(self, code: CodeType, line_number: int) -> object | None:
        path = self._source_path(code)
        if path is None:
            return sys.monitoring.DISABLE
        self.lines.setdefault(path, set()).add(line_number)
        return sys.monitoring.DISABLE

    def branch(self, code: CodeType, offset: int, destination: int) -> object | None:
        path = self._source_path(code)
        if path is None:
            return sys.monitoring.DISABLE
        key = (path, code.co_firstlineno, code.co_qualname, offset)
        destinations = self.branches.setdefault(key, set())
        destinations.add(destination)
        return sys.monitoring.DISABLE if len(destinations) == 2 else None

    def start(self) -> None:
        tool = sys.monitoring.COVERAGE_ID
        if sys.monitoring.get_tool(tool) is not None:
            raise RuntimeError("Python coverage monitoring is already in use")
        sys.monitoring.use_tool_id(tool, "tutor-assistant-coverage")
        sys.monitoring.register_callback(tool, sys.monitoring.events.LINE, self.line)
        sys.monitoring.register_callback(tool, sys.monitoring.events.BRANCH, self.branch)
        sys.monitoring.set_events(tool, sys.monitoring.events.LINE | sys.monitoring.events.BRANCH)

    def stop(self) -> None:
        tool = sys.monitoring.COVERAGE_ID
        sys.monitoring.set_events(tool, 0)
        sys.monitoring.register_callback(tool, sys.monitoring.events.LINE, None)
        sys.monitoring.register_callback(tool, sys.monitoring.events.BRANCH, None)
        sys.monitoring.free_tool_id(tool)

    @staticmethod
    def _code_objects(code: CodeType):
        yield code
        for constant in code.co_consts:
            if isinstance(constant, CodeType):
                yield from CoverageMonitor._code_objects(constant)

    def report(self) -> list[ModuleCoverage]:
        modules: list[ModuleCoverage] = []
        for path in sorted(self.source.rglob("*.py")):
            executable_lines: set[int] = set()
            possible_branches: set[tuple[Path, int, str, int]] = set()
            root = compile(path.read_text(encoding="utf-8"), str(path), "exec")
            for code in self._code_objects(root):
                executable_lines.update(
                    line_number for _offset, line_number in dis.findlinestarts(code) if line_number > 0
                )
                for instruction in dis.get_instructions(code):
                    if instruction.opname == "FOR_ITER" or (
                        "JUMP" in instruction.opname and "IF" in instruction.opname
                    ):
                        possible_branches.add(
                            (path, code.co_firstlineno, code.co_qualname, instruction.offset)
                        )
            module = ".".join(path.relative_to(self.source.parent).with_suffix("").parts)
            covered_lines = executable_lines & self.lines.get(path, set())
            covered_branches = sum(
                min(2, len(self.branches.get(branch, set()))) for branch in possible_branches
            )
            modules.append(
                ModuleCoverage(
                    module=module,
                    statements=len(executable_lines),
                    covered_statements=len(covered_lines),
                    branches=len(possible_branches) * 2,
                    covered_branches=covered_branches,
                )
            )
        return modules


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run pytest with Python 3.12 line and branch coverage")
    parser.add_argument("--source", type=Path, default=Path("src/tutor_assistant"))
    parser.add_argument("--output", type=Path)
    parser.add_argument("--fail-under", type=float, default=0)
    parser.add_argument("--branch-fail-under", type=float, default=0)
    arguments, pytest_arguments = parser.parse_known_args(argv)
    if pytest_arguments[:1] == ["--"]:
        pytest_arguments = pytest_arguments[1:]
    monitor = CoverageMonitor(arguments.source)
    monitor.start()
    try:
        result = pytest.main(pytest_arguments or ["-q"])
    finally:
        monitor.stop()
    if result:
        return int(result)

    modules = monitor.report()
    total = ModuleCoverage(
        module="TOTAL",
        statements=sum(module.statements for module in modules),
        covered_statements=sum(module.covered_statements for module in modules),
        branches=sum(module.branches for module in modules),
        covered_branches=sum(module.covered_branches for module in modules),
    )
    print("\nModule                                                Lines   Cover  Branch  BrCov")
    for module in [*modules, total]:
        print(
            f"{module.module:<53} {module.covered_statements:>5}/{module.statements:<5} "
            f"{module.line_percent:>5.1f}% {module.covered_branches:>5}/{module.branches:<5} "
            f"{module.branch_percent:>5.1f}%"
        )
    if arguments.output:
        arguments.output.parent.mkdir(parents=True, exist_ok=True)
        arguments.output.write_text(
            json.dumps(
                {
                    "line_percent": round(total.line_percent, 2),
                    "branch_percent": round(total.branch_percent, 2),
                    "totals": asdict(total),
                    "modules": [asdict(module) for module in modules],
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
    if total.line_percent < arguments.fail_under:
        print(f"Line coverage {total.line_percent:.1f}% is below {arguments.fail_under:.1f}%")
        return 1
    if total.branch_percent < arguments.branch_fail_under:
        print(f"Branch coverage {total.branch_percent:.1f}% is below {arguments.branch_fail_under:.1f}%")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
