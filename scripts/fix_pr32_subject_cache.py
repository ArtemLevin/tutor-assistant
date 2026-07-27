from pathlib import Path


def replace_once(path: str, old: str, new: str) -> None:
    target = Path(path)
    content = target.read_text(encoding="utf-8")
    if old not in content:
        raise RuntimeError(f"Expected block not found in {path}: {old[:100]!r}")
    target.write_text(content.replace(old, new, 1), encoding="utf-8")


replace_once(
    "src/tutor_assistant/normalization/service.py",
    '''    def _configuration_payload(
        self,
        model: str,
        *,
        subject_profile: SubjectProfile,
    ) -> dict[str, Any]:
        payload = self.config.model_dump(mode="json")
        payload["model"] = model
        payload["subject_profile"] = subject_profile.name.value
        payload["prompt_version"] = subject_profile.prompt_version
        return payload
''',
    '''    def _configuration_payload(
        self,
        model: str,
        *,
        lesson_subject: str,
        subject_profile: SubjectProfile,
    ) -> dict[str, Any]:
        payload = self.config.model_dump(mode="json")
        payload["model"] = model
        payload["lesson_subject"] = lesson_subject.strip()
        payload["subject_profile"] = subject_profile.name.value
        payload["prompt_version"] = subject_profile.prompt_version
        return payload
''',
)
replace_once(
    "src/tutor_assistant/normalization/service.py",
    '''        config_hash = configuration_hash(
            self._configuration_payload(model, subject_profile=subject_profile)
        )
''',
    '''        config_hash = configuration_hash(
            self._configuration_payload(
                model,
                lesson_subject=lesson.subject,
                subject_profile=subject_profile,
            )
        )
''',
)
replace_once(
    "tests/test_subject_aware_filtering.py",
    '''from tutor_assistant.normalization.errors import UnsafeNormalizationResultError
''',
    '''from tutor_assistant.normalization.artifacts import configuration_hash
from tutor_assistant.normalization.errors import UnsafeNormalizationResultError
''',
)
replace_once(
    "tests/test_subject_aware_filtering.py",
    '''def test_subject_prompts_are_versioned_and_domain_specific() -> None:
''',
    '''def test_configuration_hash_distinguishes_raw_subject_labels(tmp_path: Path) -> None:
    content = StudentContentService(tmp_path / "data")
    service = NormalizationService(NormalizationConfig(), content)
    profile = resolve_subject_profile("physics")

    base = service._configuration_payload(
        "qwen3:8b",
        lesson_subject="physics",
        subject_profile=profile,
    )
    exam = service._configuration_payload(
        "qwen3:8b",
        lesson_subject="ЕГЭ физика",
        subject_profile=profile,
    )

    assert configuration_hash(base) != configuration_hash(exam)


def test_subject_prompts_are_versioned_and_domain_specific() -> None:
''',
)
