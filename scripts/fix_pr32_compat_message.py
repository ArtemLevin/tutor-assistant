from pathlib import Path

path = Path("src/tutor_assistant/normalization/validation.py")
text = path.read_text(encoding="utf-8")
old = '''                f"Удалён термин профиля {profile.name.value}: " + ", ".join(missing_terms)
'''
new = '''                f"Удалён термин школьного курса профиля {profile.name.value}: "
                + ", ".join(missing_terms)
'''
if old not in text:
    raise RuntimeError("subject term error message not found")
path.write_text(text.replace(old, new, 1), encoding="utf-8")
