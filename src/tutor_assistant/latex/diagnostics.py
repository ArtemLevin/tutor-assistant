from __future__ import annotations

import shutil
import subprocess

from ..config import LatexConfig
from .models import EnvironmentReport


REQUIRED_TEX_FILES = [
    "babel.sty", "russianb.ldf", "amsmath.sty", "tikz.sty", "pgfplots.sty", "tkz-euclide.sty"
]


def inspect_latex_environment(config: LatexConfig) -> EnvironmentReport:
    latexmk = shutil.which(config.latexmk_command)
    engine = shutil.which(config.engine)
    pdftoppm = shutil.which("pdftoppm")
    messages: list[str] = []
    packages: dict[str, bool] = {}
    kpsewhich = shutil.which("kpsewhich")
    if kpsewhich:
        for filename in REQUIRED_TEX_FILES:
            result = subprocess.run(
                [kpsewhich, filename], capture_output=True, text=True, timeout=15
            )
            packages[filename] = result.returncode == 0 and bool(result.stdout.strip())
    else:
        packages = {filename: False for filename in REQUIRED_TEX_FILES}
        messages.append("kpsewhich не найден: состав TeX-пакетов не проверен")
    if not latexmk:
        messages.append(f"Не найден latexmk: {config.latexmk_command}")
    if not engine:
        messages.append(f"Не найден движок: {config.engine}")
    missing = [name for name, present in packages.items() if not present]
    if kpsewhich and missing:
        messages.append("Отсутствуют TeX-компоненты: " + ", ".join(missing))
    if config.render_preview and not pdftoppm:
        messages.append("pdftoppm не найден: PNG-предпросмотр будет пропущен")
    return EnvironmentReport(
        ready=bool(latexmk and engine and (not kpsewhich or not missing)),
        latexmk=latexmk, engine=engine, pdftoppm=pdftoppm, packages=packages, messages=messages,
    )
