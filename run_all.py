"""Run the whole system end to end: data, Stage 1, Stage 1 evaluation, Stage 2, Stage 3."""

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
steps = ["data/make_data.py", "stage1_hygiene/run_stage1.py", "eval/eval_stage1.py",
         "stage2_forecast/backtest.py", "stage3_review/build_pack.py",
         "data/make_quotes.py", "stage4_deal_desk/deal_desk.py"]
for s in steps:
    print(f"\n=== {s} ===")
    subprocess.run([sys.executable, str(ROOT / s)] + sys.argv[1:] * (s.startswith("stage1") or s.startswith("stage3")), check=True)
print("\nDone. Open outputs/stage3/review_pack.html")
