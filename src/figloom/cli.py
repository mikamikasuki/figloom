"""Local server and deterministic rendering entry points."""
from __future__ import annotations

import argparse
import importlib.metadata
import json
import os
from pathlib import Path
import shutil
import sys
import threading
import time
import webbrowser
from urllib.parse import quote

from .store import StoreError


def default_data_dir() -> Path:
    base = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share"))
    current, legacy = base / "figloom", base / "paper-studio"
    if not current.exists():
        try:
            with (legacy / "studio.sqlite3").open('rb') as file:
                if file.read(16) == b'SQLite format 3\x00':
                    return legacy
        except OSError:
            pass
    return current


def default_web_dir() -> Path:
    bundled = Path(__file__).parent / "web"
    if (bundled / "index.html").is_file():
        return bundled
    return Path(__file__).resolve().parents[2] / "apps" / "web" / "dist"


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(prog="figloom", description="Scientific diagrams, statistical curves, and tables.")
    result.add_argument("--version", action="version", version="figloom 0.1.0")
    result.add_argument("--server", default=os.environ.get("FIGLOOM_SERVER", "http://127.0.0.1:8008"), help="HTTP client address; does not select a local data directory (default: %(default)s)")
    result.add_argument("--timeout", type=float, default=30, help="HTTP client timeout in seconds (default: %(default)s)")
    commands = result.add_subparsers(dest="command", required=True)
    serve = commands.add_parser("serve", help="Run the local Dashboard and API")
    serve.add_argument("--host", default="127.0.0.1", choices=["127.0.0.1", "::1", "localhost"])
    serve.add_argument("--port", type=int, default=8008)
    serve.add_argument("--data-dir", type=Path, default=default_data_dir())
    serve.add_argument("--web-dir", type=Path, default=default_web_dir())
    serve.add_argument("--open", action="store_true", help="Open the Dashboard in a browser")
    commands.add_parser("doctor", help="Check installed dependencies and the Web build")
    render = commands.add_parser("render", help="Render an editable specification with real source data")
    render.add_argument("--kind", choices=["diagram", "plot", "table"], required=True)
    render.add_argument("--spec", type=Path, required=True)
    render.add_argument("--source", type=Path, action="append", default=[])
    render.add_argument("--output", type=Path, required=True)
    render.add_argument("--title", default="")
    render.add_argument("--caption", default="")
    def group(name, help):
        top = commands.add_parser(name, help=help)
        return top.add_subparsers(dest="operation", required=True)
    def operation(group, name, help):
        item = group.add_parser(name, help=help)
        item.add_argument("--server", default=argparse.SUPPRESS, help="Override the loopback HTTP server address")
        return item
    def export_options(item):
        item.add_argument("--output", type=Path, required=True)
        item.add_argument("--force", action="store_true", help="Replace an existing output file")
    projects = group("projects", "Manage durable projects; responses are JSON")
    operation(projects, "list", "List projects")
    create = operation(projects, "create", "Create a project")
    create.add_argument("--name", required=True)
    create.add_argument("--description", default="")
    show = operation(projects, "show", "Show the complete project graph and resources")
    show.add_argument("project_id")
    export = operation(projects, "export", "Save a portable project ZIP")
    export.add_argument("project_id")
    export_options(export)
    imported = operation(projects, "import", "Import a portable project ZIP with new IDs")
    imported.add_argument("file", type=Path)
    sources = group("sources", "Upload and inspect actual project inputs")
    upload = operation(sources, "upload", "Upload a document, data file, or reference image")
    upload.add_argument("project_id")
    upload.add_argument("file", type=Path)
    listed = operation(sources, "list", "List a project's sources")
    listed.add_argument("project_id")
    assets = group("assets", "Create, edit, render, and export scientific assets")
    create = operation(assets, "create", "Create an editable asset from a JSON specification")
    create.add_argument("project_id")
    create.add_argument("--kind", choices=["diagram", "plot", "table"], required=True)
    create.add_argument("--title", required=True)
    create.add_argument("--spec", type=Path)
    create.add_argument("--source", action="append", default=[], help="Bound source ID; repeat for multiple inputs")
    create.add_argument("--caption-file", type=Path)
    show = operation(assets, "show", "Show spec, revision, review, and artifacts")
    show.add_argument("asset_id")
    update = operation(assets, "update", "Replace spec using the current expected revision")
    update.add_argument("asset_id")
    update.add_argument("--spec", type=Path)
    update.add_argument("--expected-revision", type=int, required=True)
    update.add_argument("--title")
    update.add_argument("--caption-file", type=Path)
    update.add_argument("--source", action="append", help="Replace the complete bound source ID list")
    run = operation(assets, "render", "Queue a real deterministic render and optionally wait")
    run.add_argument("asset_id")
    run.add_argument("--scope", choices=["single", "ancestors", "affected"], default="single")
    run.add_argument("--wait", action="store_true")
    run.add_argument("--wait-timeout", type=float, default=600, help="Wait deadline in seconds (default: %(default)s)")
    export = operation(assets, "export", "Save the asset, revisions, and sources as ZIP")
    export.add_argument("asset_id")
    export_options(export)
    jobs = group("jobs", "Inspect and cancel durable jobs")
    for name in ("show", "cancel", "wait"):
        item = operation(jobs, name, {"show": "Inspect actual job state", "cancel": "Cancel a queued or running job", "wait": "Resume waiting for an existing job without enqueueing new work"}[name])
        item.add_argument("job_id")
        if name == "wait":
            item.add_argument("--wait-timeout", type=float, default=600)
    models = group("models", "Read provider model metadata without submitting generation")
    for name in ("list", "test"):
        item = operation(models, name, "List available model IDs" if name == "list" else "Check metadata reachability and configured model ID")
        item.add_argument("--kind", choices=["text", "image"], default="text")
        item.add_argument("--config", type=Path, help="Temporary endpoint JSON overrides; never saved")
    settings = group("settings", "Inspect redacted settings or merge a JSON configuration")
    operation(settings, "show", "Show model settings and spending without API keys")
    configure = operation(settings, "configure", "Merge a settings JSON file; keys stay in the server's private workspace")
    configure.add_argument("--file", type=Path, required=True)
    return result


def api_command(args):
    from .http_client import APIClient, ClientError, load_object
    if not 0 < args.timeout <= 300:
        raise ClientError('--timeout must be greater than zero and at most 300.', code='invalid_input', exit_code=2)
    def id(value):
        return quote(value, safe='')
    def caption(path):
        if path.stat().st_size > 80_000:
            raise ClientError('Caption input is too large.', code='invalid_input', exit_code=2)
        return path.read_text(encoding='utf-8')
    def wait(client, job):
        if not 0 < args.wait_timeout <= 86_400:
            raise ClientError('--wait-timeout must be greater than zero and at most 86400.', code='invalid_input', exit_code=2)
        deadline = time.monotonic() + args.wait_timeout
        try:
            while job['status'] in ('queued', 'running'):
                if time.monotonic() >= deadline:
                    raise ClientError('Wait deadline reached; job ' + job['id'] + ' remains active. Inspect it with jobs show or resume with jobs wait.', code='wait_timeout', exit_code=4)
                time.sleep(min(.25, max(0, deadline - time.monotonic())))
                job = client.request('GET', '/jobs/' + id(job['id']))
        except KeyboardInterrupt:
            raise ClientError('Stopped waiting; job ' + job['id'] + ' remains available. Resume with jobs wait.', code='interrupted', exit_code=130) from None
        if job['status'] != 'completed':
            raise ClientError('Job ' + job['id'] + ' ' + job['status'] + '. Inspect jobs show for diagnostics.', code='job_' + job['status'], exit_code=3)
        return job
    with APIClient(args.server, args.timeout) as client:
        kind, op = args.command, args.operation
        if kind == 'projects':
            if op == 'list': return client.request('GET', '/projects')
            if op == 'create': return client.request('POST', '/projects', body={'name': args.name, 'description': args.description})
            if op == 'show': return client.request('GET', '/projects/' + id(args.project_id))
            if op == 'import': return client.request('POST', '/projects/import', upload=args.file)
            return client.download('/projects/' + id(args.project_id) + '/export', args.output, force=args.force)
        if kind == 'sources':
            path = '/projects/' + id(args.project_id)
            if op == 'list': return client.request('GET', path)['sources']
            return client.request('POST', path + '/sources', upload=args.file)
        if kind == 'assets':
            if op == 'create':
                body = {'kind': args.kind, 'title': args.title, 'source_ids': args.source}
                if args.spec is not None: body['spec'] = load_object(args.spec)
                if args.caption_file is not None: body['caption'] = caption(args.caption_file)
                return client.request('POST', '/projects/' + id(args.project_id) + '/assets', body=body)
            path = '/assets/' + id(args.asset_id)
            if op == 'show': return client.request('GET', path)
            if op == 'export': return client.download(path + '/export', args.output, force=args.force)
            if op == 'update':
                body = {'expected_revision': args.expected_revision}
                if args.spec is not None: body['spec'] = load_object(args.spec)
                if args.title is not None: body['title'] = args.title
                if args.caption_file is not None: body['caption'] = caption(args.caption_file)
                if args.source is not None: body['source_ids'] = args.source
                if len(body) == 1:
                    raise ClientError('Supply --spec, --title, --caption-file, or --source.', code='invalid_input', exit_code=2)
                return client.request('PATCH', path, body=body)
            if not 0 < args.wait_timeout <= 86_400:
                raise ClientError('--wait-timeout must be greater than zero and at most 86400.', code='invalid_input', exit_code=2)
            job = client.request('POST', path + '/run', body={'action': 'render', 'scope': args.scope})
            if not args.wait: return job
            return wait(client, job)
        if kind == 'jobs':
            path = '/jobs/' + id(args.job_id)
            if op == 'wait': return wait(client, client.request('GET', path))
            return client.request('GET' if op == 'show' else 'POST', path + ('/cancel' if op == 'cancel' else ''))
        if kind == 'models':
            return client.request('POST', '/providers/' + args.kind + ('/models' if op == 'list' else '/test'), body={'config': load_object(args.config)} if args.config else {})
        if kind == 'settings':
            return client.request('GET' if op == 'show' else 'PUT', '/settings', body=load_object(args.file) if op == 'configure' else None)
        raise ClientError('Unknown operation.', code='invalid_input', exit_code=2)


def main(argv=None) -> int:
    args = parser().parse_args(argv)
    from .http_client import ClientError
    try:
        if args.command not in {'serve', 'doctor', 'render'}:
            print(json.dumps(api_command(args), ensure_ascii=False, indent=2, allow_nan=False))
            return 0
        if args.command == "doctor":
            packages = ["fastapi", "uvicorn", "httpx", "numpy", "pandas", "scipy", "matplotlib", "Pillow", "pypdf", "python-multipart"]
            missing = []
            for name in packages:
                try:
                    print(f"{name}: {importlib.metadata.version(name)}")
                except importlib.metadata.PackageNotFoundError:
                    missing.append(name)
                    print(f"{name}: missing")
            web_dir = default_web_dir()
            print(f"Dashboard: {'ready' if (web_dir / 'index.html').is_file() else 'run python3.11 scripts/install.py'}")
            print(f"LaTeX table PDF: {shutil.which('tectonic') or shutil.which('pdflatex') or 'install tectonic or pdflatex; LaTeX source still exports'}")
            print(f"Default data directory: {default_data_dir()}")
            return 1 if missing else 0
        if args.command == "render":
            from .engine import inspect_source, render_asset
            spec = json.loads(args.spec.read_text(encoding="utf-8"))
            if not isinstance(spec, dict):
                raise ValueError("The specification must be a JSON object")
            sources = []
            for index, path in enumerate(args.source):
                path = path.expanduser().resolve(strict=True)
                sources.append({"id": f"source-{index + 1}", "name": path.name, "path": str(path), **inspect_source(path)})
            if sources and not spec.get("source_id"):
                spec["source_id"] = sources[0]["id"]
            result = render_asset({"kind": args.kind, "spec": spec, "title": args.title, "caption": args.caption,
                                   "source_ids": [source["id"] for source in sources]}, sources, args.output.expanduser().resolve())
            print(json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False))
            return 0
        if not 1 <= args.port <= 65535:
            raise ValueError("Port must be between 1 and 65535")
        import uvicorn
        from .api import create_app
        data_dir = args.data_dir.expanduser().resolve()
        web_dir = args.web_dir.expanduser().resolve()
        if not (web_dir / "index.html").is_file():
            print("Dashboard build missing. Run python3.11 scripts/install.py, or use npm run dev in apps/web.", file=sys.stderr)
        app = create_app(data_dir, web_dir)
        address = f"http://{'[::1]' if args.host == '::1' else args.host}:{args.port}"
        print(f"Figloom: {address}\nWorkspace: {data_dir}", flush=True)
        if args.open:
            threading.Timer(1.0, lambda: webbrowser.open(address)).start()
        uvicorn.run(app, host=args.host, port=args.port, access_log=False)
        return 0
    except ClientError as error:
        print(json.dumps({'error': {'code': error.code, 'message': str(error), **({'status': error.status} if error.status else {})}}, ensure_ascii=False), file=sys.stderr)
        return error.exit_code
    except KeyboardInterrupt:
        print(json.dumps({'error': {'code': 'interrupted', 'message': 'Interrupted. Submitted jobs remain available through jobs show.'}}), file=sys.stderr)
        return 130
    except (ValueError, OSError, RuntimeError, StoreError) as error:
        if args.command not in {'serve', 'doctor', 'render'}:
            print(json.dumps({'error': {'code': 'invalid_input', 'message': 'Cannot read the requested input or complete the operation.'}}), file=sys.stderr)
            return 2
        print(str(error), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
