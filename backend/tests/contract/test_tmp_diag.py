"""临时诊断：写文件子进程在无 cov 下的行为。"""
from __future__ import annotations
import subprocess, sys, time
import pytest
pytestmark = pytest.mark.contract

def test_write_subprocess():
    t0 = time.time()
    p = subprocess.run(
        [sys.executable, "-c", "from pathlib import Path; Path('artifacts/diag-x.json').write_text('x'*1000, encoding='utf-8'); print('ok')"],
        capture_output=True, text=True, timeout=25,
    )
    print(f"elapsed={round(time.time()-t0,1)}s rc={p.returncode} out={p.stdout!r}", flush=True)
