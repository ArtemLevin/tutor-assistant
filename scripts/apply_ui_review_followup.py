from pathlib import Path

path = Path("src/tutor_assistant/ui/information_architecture.py")
content = path.read_text(encoding="utf-8")
old = """        if button is None or not button.isVisibleTo(self) or not button.isEnabled():\n            return\n"""
new = """        if button is None or not button.isEnabled():\n            return\n"""
if content.count(old) != 1:
    raise RuntimeError("Expected generated focus guard exactly once")
path.write_text(content.replace(old, new, 1), encoding="utf-8", newline="\n")

test_path = Path("tests/test_information_architecture_gui.py")
test_content = test_path.read_text(encoding="utf-8")
test_old = "QTest.keyClick(window.detailed_mode_button, Qt.Key.Key_Return)"
test_new = "QTest.keyClick(window.detailed_mode_button, Qt.Key.Key_Space)"
if test_content.count(test_old) != 1:
    raise RuntimeError("Expected detailed-mode keyboard activation exactly once")
test_path.write_text(
    test_content.replace(test_old, test_new, 1),
    encoding="utf-8",
    newline="\n",
)
