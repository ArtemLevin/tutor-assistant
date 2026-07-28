from __future__ import annotations

from tutor_assistant.cli import parser


def test_cloud_cli_requires_explicit_allow_flag_surface() -> None:
    args = parser().parse_args(
        [
            "filter-transcript",
            "lesson-id",
            "--provider",
            "yandex_ai_studio",
            "--allow-cloud",
        ]
    )
    assert args.allow_cloud is True


def test_credentials_and_privacy_doctor_commands_are_available() -> None:
    credentials = parser().parse_args(["credentials", "yandex", "status"])
    privacy = parser().parse_args(["privacy-doctor", "--json", "--strict"])

    assert credentials.command == "credentials"
    assert credentials.credential_action == "status"
    assert privacy.command == "privacy-doctor"
    assert privacy.json is True
    assert privacy.strict is True
