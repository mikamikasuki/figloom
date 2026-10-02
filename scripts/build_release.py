#!/usr/bin/env python3
"""Build a wheel containing the production Dashboard."""
from pathlib import Path
import os
import shutil
import subprocess
import sys
import tempfile

root = Path(__file__).resolve().parents[1]
with tempfile.TemporaryDirectory(prefix="figloom-npm-") as cache:
    npm_env = {**os.environ, "npm_config_cache": cache}
    subprocess.run(["npm", "ci"], cwd=root / "apps/web", env=npm_env, check=True)
    subprocess.run(["npm", "run", "build"], cwd=root / "apps/web", env=npm_env, check=True)
bundled = root / "src/figloom/web"
if bundled.exists():
    raise SystemExit("Remove the previous generated src/figloom/web directory before packaging")
try:
    shutil.copytree(root / "apps/web/dist", bundled)
    subprocess.run([sys.executable, "-m", "build"], cwd=root, check=True)
finally:
    shutil.rmtree(bundled, ignore_errors=True)
