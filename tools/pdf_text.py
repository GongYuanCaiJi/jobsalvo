"""Extract text from a PDF using the repository's Python environment."""
from __future__ import annotations

import re
import subprocess
import sys


def pages(path: str) -> int:
    result = subprocess.run(
        [sys.executable, '-c', 'import sys,pypdf;print(len(pypdf.PdfReader(sys.argv[1]).pages))', path],
        capture_output=True, text=True, timeout=120)
    if result.returncode:
        raise ValueError((result.stderr or 'PDF 無法讀取')[-300:])
    return int(result.stdout.strip())


def extract(path: str) -> str:
    result = subprocess.run(
        [sys.executable, '-c',
         'import sys,pypdf;print("\\n".join((p.extract_text() or "") for p in pypdf.PdfReader(sys.argv[1]).pages))',
         path],
        capture_output=True, text=True, timeout=120)
    if result.returncode:
        raise ValueError((result.stderr or 'PDF 無法讀取')[-300:])
    return re.sub(r'\n{3,}', '\n\n', result.stdout).strip()
