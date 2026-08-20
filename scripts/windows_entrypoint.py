"""Frozen executable always enters the complete production composition root."""

from __future__ import annotations

import sys

CLI_COMMANDS = frozenset(
    {
        "content-backup",
        "content-doctor",
        "content-filter-doctor",
        "content-index",
        "credentials",
        "devices",
        "doctor",
        "hardware-soak",
        "latex-doctor",
        "normalization-doctor",
        "privacy-doctor",
        "recovery-drill",
        "support-bundle",
    }
)


def main() -> None:
    if any(argument in CLI_COMMANDS for argument in sys.argv[1:]):
        from tutor_assistant.cli import main as cli_main

        cli_main()
        return
    from tutor_assistant.ui.recording_recovery_app import main as gui_main

    gui_main()


if __name__ == "__main__":
    main()
