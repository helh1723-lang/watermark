from __future__ import annotations

import os
import sys
import traceback
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
RUNTIME = ROOT / ".runtime"
RUNTIME.mkdir(exist_ok=True)

os.chdir(ROOT)
sys.path.insert(0, str(ROOT))

stdout_log = open(RUNTIME / "watermark_app.log", "a", encoding="utf-8", buffering=1)
stderr_log = open(RUNTIME / "watermark_app.error.log", "a", encoding="utf-8", buffering=1)
sys.stdout = stdout_log
sys.stderr = stderr_log

try:
    from watermark_app.gui import main

    main()
except Exception:
    traceback.print_exc()
    raise
