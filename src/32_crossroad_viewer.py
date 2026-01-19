import runpy
from pathlib import Path

print("[DEPRECATED] 32_crossroad_viewer.py は廃止予定です。32_crossroad_report.py を使用してください。")
runpy.run_path(str(Path(__file__).with_name("32_crossroad_report.py")), run_name="__main__")
