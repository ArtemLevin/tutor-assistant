from pathlib import Path

path = Path("src/tutor_assistant/ui/app.py")
text = path.read_text(encoding="utf-8")
old = '''from .accessibility import sync_text_status\nfrom .crm import SchedulePage, StudentsPage\nfrom .localization import select_subject, set_subject_combo, subject_value\nfrom .latex_monitor_presentation import (\n    LatexMonitorPresentation,\n    build_latex_monitor_failure_presentation,\n    build_latex_monitor_no_update_presentation,\n    build_latex_monitor_result_presentation,\n    build_latex_monitor_scanning_presentation,\n    build_latex_monitor_toggle_presentation,\n)\nfrom .normalization import NormalizationReviewDialog\n'''
new = '''from .accessibility import sync_text_status\nfrom .crm import SchedulePage, StudentsPage\nfrom .latex_monitor_presentation import (\n    LatexMonitorPresentation,\n    build_latex_monitor_failure_presentation,\n    build_latex_monitor_no_update_presentation,\n    build_latex_monitor_result_presentation,\n    build_latex_monitor_scanning_presentation,\n    build_latex_monitor_toggle_presentation,\n)\nfrom .localization import select_subject, set_subject_combo, subject_value\nfrom .normalization import NormalizationReviewDialog\n'''
if text.count(old) != 1:
    raise RuntimeError(f"expected import block once, found {text.count(old)}")
path.write_text(text.replace(old, new, 1), encoding="utf-8")
