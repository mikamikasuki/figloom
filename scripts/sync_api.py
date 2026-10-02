#!/usr/bin/env python3
"""Export the live API contract and generate its TypeScript types."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="Fail if committed contracts differ from the application")
    parser.add_argument("--schema-only", action="store_true", help="Export or check OpenAPI without Node.js")
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    sys.dont_write_bytecode = True
    sys.path.insert(0, str(root / "src"))
    from figloom.api import create_app

    with tempfile.TemporaryDirectory(prefix="figloom-api-schema-") as directory:
        application = create_app(Path(directory) / "workspace", start_worker=False)
        schema = json.dumps(application.openapi(), ensure_ascii=False, allow_nan=False, indent=2, sort_keys=True) + "\n"

    target = root / "docs" / "openapi.json"
    if args.check:
        if not target.is_file() or target.read_text(encoding="utf-8") != schema:
            print("OpenAPI is out of date. Run python scripts/sync_api.py.", file=sys.stderr)
            return 1
    else:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(schema, encoding="utf-8")

    if not args.schema_only:
        node = shutil.which("node")
        cli = root / "apps/web/node_modules/openapi-typescript/bin/cli.js"
        if not node or not cli.is_file():
            print("Install frontend dependencies with npm ci in apps/web, then run this command again.", file=sys.stderr)
            return 1
        destination = root / "apps/web/src/generated/api-schema.ts"
        destination.parent.mkdir(parents=True, exist_ok=True)
        command = [node, str(cli), str(target), "--output", str(destination), "--default-non-nullable", "false"]
        if args.check:
            command.append("--check")
        result = subprocess.run(command, cwd=root / "apps/web", check=False)
        if result.returncode:
            return result.returncode
    print("API contract is current." if args.check else "API contract exported.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
