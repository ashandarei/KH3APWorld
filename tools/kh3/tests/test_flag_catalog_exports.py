"""The packaged flag catalogs remain reproducible with the Python reader."""
from pathlib import Path
import subprocess
import sys

import pytest


@pytest.mark.parametrize('catalog', ['event', 'vbonus'])
def test_export_matches_packaged_catalog_without_external_tools(tmp_path, catalog):
    root = Path(__file__).resolve().parents[3]
    result = subprocess.run(
        [sys.executable, str(root / f'tools/kh3/export_{catalog}_flags.py'), '--check'],
        cwd=tmp_path, capture_output=True, text=True, timeout=30,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert 'Verified' in result.stdout
