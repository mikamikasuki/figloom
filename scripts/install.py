#!/usr/bin/env python3
"""Install the Python package and build the local Dashboard."""
from __future__ import annotations

import argparse
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import venv


def run(command, cwd, env=None):
    subprocess.run(command, cwd=cwd, env=env, check=True)


def main():
    options = argparse.ArgumentParser()
    options.add_argument("--headless", action="store_true", help="Install the API and rendering engine without the Dashboard")
    options.add_argument("--dev", action="store_true", help="Install the test dependencies")
    args = options.parse_args()
    if sys.version_info < (3, 11):
        raise SystemExit("Python 3.11 or newer is required")
    root = Path(__file__).resolve().parents[1]
    env = root / ".venv"
    if not env.exists():
        venv.EnvBuilder(with_pip=True).create(env)
    python = env / ("Scripts/python.exe" if sys.platform == "win32" else "bin/python")
    lock = "requirements-dev.lock.txt" if args.dev else "requirements.lock.txt"
    run([str(python), "-m", "pip", "install", "-r", lock], root)
    run([str(python), "-m", "pip", "install", "-e", ".[dev]" if args.dev else "."], root)
    if not args.headless:
        npm, node = shutil.which("npm"), shutil.which("node")
        if not npm or not node:
            raise SystemExit("Install Node.js 22+ and npm, then rerun this installer")
        major = int(subprocess.check_output([node, "-p", "process.versions.node.split('.')[0]"], text=True).strip())
        if major < 22:
            raise SystemExit("Node.js 22+ is required to build the Dashboard")
        with tempfile.TemporaryDirectory(prefix="figloom-npm-") as cache:
            npm_env = {**os.environ, "npm_config_cache": cache}
            run([npm, "ci"], root / "apps/web", env=npm_env)
            run([npm, "run", "build"], root / "apps/web", env=npm_env)
    print("Start with .venv/bin/figloom serve" + ("" if args.headless else " --open"))


if __name__ == "__main__":
    try:
        main()
    except subprocess.CalledProcessError as error:
        raise SystemExit(error.returncode)
