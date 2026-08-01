from pathlib import Path

path = Path("src/tutor_assistant/ui/information_architecture.py")
content = path.read_text(encoding="utf-8")
old = """        if button is None or not button.isVisibleTo(self) or not button.isEnabled():\n            return\n"""
new = """        if button is None or not button.isEnabled():\n            return\n"""
if content.count(old) != 1:
    raise RuntimeError("Expected generated focus guard exactly once")
path.write_text(content.replace(old, new, 1), encoding="utf-8", newline="\n")
