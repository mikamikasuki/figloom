"""Loopback-only HTTP interface for the durable local scientific workspace."""
from __future__ import annotations

import io
import json
import re
import zipfile
from contextlib import asynccontextmanager
from pathlib import Path, PurePosixPath
from urllib.parse import quote, urlparse

from fastapi import APIRouter, FastAPI, File, Form, Request, UploadFile
from fastapi.exceptions import RequestValidationError
from starlette.exceptions import HTTPException
from pydantic import BaseModel
from fastapi.responses import FileResponse, JSONResponse, Response

from . import contracts as c
from .budget import finite_number, summary
from .jobs import JobManager, confined_file
from .store import Store, StoreError, identifier, now, public


UPLOAD_LIMIT = 32 * 1024 * 1024
EXPORT_LIMIT = 128 * 1024 * 1024
SOURCE_TYPES = {".pdf", ".md", ".txt", ".tex", ".csv", ".tsv", ".json", ".png", ".jpg", ".jpeg", ".svg"}

REQUEST_ID_HEADER = {"description": "Identifier of this HTTP request, including errors.", "schema": {"type": "string"}}
ERROR_RESPONSES = {status: {"model": c.ErrorResponse, "description": description, "headers": {"X-Request-ID": REQUEST_ID_HEADER}} for status, description in {
    400: "Invalid request", 403: "Forbidden host or origin", 404: "Resource not found", 405: "Method not allowed",
    409: "Revision or resource conflict", 413: "Payload too large", 422: "Request validation failed",
    500: "Internal error", 502: "Provider metadata request failed", 503: "Service unavailable"}.items()}
ERROR_RESPONSES[200] = {"headers": {"X-Request-ID": REQUEST_ID_HEADER}}
ZIP_RESPONSE = {200: {"description": "Portable ZIP download", "content": {"application/zip": {"schema": {"type": "string", "format": "binary"}}}, "headers": {"X-Request-ID": REQUEST_ID_HEADER}}}
FILE_RESPONSE = {200: {"description": "Actual uploaded or allowlisted artifact; MIME type follows the file format.", "headers": {"X-Request-ID": REQUEST_ID_HEADER}, "content": {
    mime: {"schema": {} if mime == "application/json" else {"type": "string", "format": "binary"}}
    for mime in ("application/octet-stream", "image/png", "image/jpeg", "image/svg+xml", "application/pdf", "application/zip",
                 "application/json", "text/plain", "text/html", "text/csv", "text/tab-separated-values", "text/markdown", "text/x-tex", "application/x-tex", "text/x-python")}}}
STATUS_CODES = {400: "invalid_request", 402: "budget_exceeded", 403: "forbidden", 404: "not_found", 405: "method_not_allowed",
                409: "resource_conflict", 413: "payload_too_large", 422: "validation_error", 500: "internal_error", 503: "service_unavailable"}
VALIDATION_FIELDS = {"body", "path", "query", "header", "file", "expected_revision", "project_id", "asset_id", "source_id", "job_id", "proposal_id", "edge_id", "name"}
for _model in vars(c).values():
    if isinstance(_model, type) and issubclass(_model, BaseModel):
        VALIDATION_FIELDS.update(_model.model_fields)


def error_response(request: Request, status: int, message: str, *, code: str | None = None, details: list[dict] | None = None) -> JSONResponse:
    request_id = getattr(request.state, "request_id", None) or identifier()
    error = {"code": code or STATUS_CODES.get(status, "internal_error"), "message": message}
    if details is not None:
        error["details"] = details
    return JSONResponse({"detail": message, "error": error, "request_id": request_id}, status_code=status, headers={"X-Request-ID": request_id})


def validation_issues(exc: RequestValidationError) -> list[dict]:
    messages = {"missing": "Field is required.", "extra_forbidden": "Unknown field.", "int_type": "Expected an integer.",
                "string_type": "Expected a string.", "bool_type": "Expected a boolean.", "dict_type": "Expected an object.",
                "list_type": "Expected an array.", "literal_error": "Value is not an allowed option.", "json_invalid": "Malformed JSON.",
                "string_too_long": "String exceeds its length limit.", "string_too_short": "String is too short.",
                "greater_than_equal": "Value is below its allowed minimum.", "less_than_equal": "Value exceeds its allowed maximum."}
    return [{"location": [x if type(x) is int or x in VALIDATION_FIELDS else "field" for x in error.get("loc", ())],
             "type": error.get("type", "value_error"), "message": messages.get(error.get("type"), "Invalid field value.")}
            for error in exc.errors()]


def object_body(value: dict, allowed: set[str], required: set[str] | None = None) -> dict:
    if not isinstance(value, dict) or set(value) - allowed or (required or set()) - set(value):
        raise StoreError("Invalid request fields.")
    # Bound nesting/size and disallow JSON NaN, which SQLite and engines cannot safely interpret.
    try:
        if len(json.dumps(value, allow_nan=False)) > 1_000_000:
            raise StoreError("Request exceeds the 1 MB limit.", 413)
    except (ValueError, TypeError):
        raise StoreError("Request must contain finite JSON values.") from None
    return value


def string(value, field: str, maximum: int, *, required: bool = False) -> str:
    if not isinstance(value, str) or len(value) > maximum or (required and not value.strip()):
        raise StoreError(f"{field} must be a {'nonempty ' if required else ''}string of at most {maximum} characters.")
    return value.strip() if required else value


def asset_view(asset: dict) -> dict:
    value = public(asset)
    generation = Path(asset.get("_artifact_dir", "")).name or "unrendered"
    token = quote(f'{asset["revision"]}-{generation}', safe="")
    for item in value["artifacts"]:
        item["url"] = f'/api/v1/assets/{asset["id"]}/files/{quote(item["name"], safe="/")}?generation={token}'
    for item in value["candidates"]:
        item["preview_url"] = f'/api/v1/assets/{asset["id"]}/files/{quote(item["preview_name"], safe="/")}?generation={token}'
    return value


def state_view(state: dict) -> dict:
    result = public(state)
    result["assets"] = [asset_view(asset) for asset in state["assets"]]
    return result


async def upload(file: UploadFile, root: Path, *, source: bool = True) -> tuple[Path, str, int]:
    name = Path((file.filename or "").replace("\\", "/")).name
    suffix = Path(name).suffix.lower()
    if not name or len(name) > 240 or (source and suffix not in SOURCE_TYPES) or (not source and suffix != ".zip"):
        raise StoreError("Unsupported file type.")
    destination = root / f"{identifier()}{suffix}"
    size = 0
    try:
        with destination.open("xb") as out:
            while chunk := await file.read(1024 * 1024):
                size += len(chunk)
                if size > (UPLOAD_LIMIT if source else EXPORT_LIMIT):
                    raise StoreError("Upload exceeds the size limit.", 413)
                out.write(chunk)
        if not size:
            raise StoreError("The uploaded file is empty.")
        return destination, name, size
    except BaseException:
        destination.unlink(missing_ok=True)
        raise
    finally:
        await file.close()


def settings_view(store: Store) -> dict:
    settings = store.settings()
    result = public(settings)
    for channel in ("text", "image"):
        result.setdefault(channel, {})["has_api_key"] = bool(settings.get(channel, {}).get("api_key"))
    return {**result, **summary(store)}


def validate_settings(data: dict, current: dict) -> dict:
    flat = {f"{channel}_{field}" for channel in ("text", "image") for field in ("base_url", "model", "api_key", "api", "input_price_per_million", "output_price_per_million", "max_request_usd")}
    object_body(data, {"text", "image", "budget_usd", *flat})
    merged = {**current}
    allowed = {"base_url", "model", "api_key", "clear_api_key", "api", "local", "input_price_per_million", "output_price_per_million",
               "cached_input_price_per_million", "pricing", "max_request_usd", "max_output_tokens", "timeout", "temperature",
               "max_retries", "size", "quality", "format", "count", "max_candidates", "context_length", "model_parameters"}
    for channel in ("text", "image"):
        changes = data.get(channel, {})
        if not isinstance(changes, dict) or set(changes) - allowed:
            raise StoreError(f"Invalid {channel} model settings.")
        changes = {**changes, **{field: data[f"{channel}_{field}"] for field in allowed if f"{channel}_{field}" in data}}
        config = {**current.get(channel, {}), **changes}
        if changes.get("api_key") == "":
            # An empty secret field from the settings UI preserves an existing key.
            config["api_key"] = current.get(channel, {}).get("api_key", "")
        if ("base_url" in changes or "api" in changes) and not changes.get("api_key"):
            from .providers.client import same_provider_connection
            if not same_provider_connection(config, current.get(channel, {})):
                # Never forward a saved credential to a newly selected host.
                config["api_key"] = ""
        if "clear_api_key" in changes:
            if type(changes["clear_api_key"]) is not bool:
                raise StoreError("clear_api_key must be a boolean.")
            if changes["clear_api_key"]:
                config["api_key"] = ""
        config.pop("clear_api_key", None)
        for field in ("base_url", "model", "api_key", "api"):
            if field in config:
                string(config[field], f"{channel}.{field}", 4000)
        if config.get("base_url"):
            parsed = urlparse(config["base_url"])
            if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment:
                raise StoreError("Model endpoint must be an HTTP(S) URL without credentials, query or fragment.")
            if parsed.hostname not in {"localhost", "127.0.0.1", "::1"} and parsed.scheme != "https":
                raise StoreError("External model endpoints require HTTPS.")
            if config.get("api") == "ollama" and parsed.hostname not in {"localhost", "127.0.0.1", "::1"}:
                raise StoreError("The free local Ollama mode requires a loopback endpoint.")
            config["base_url"] = config["base_url"].rstrip("/")
        if config.get("api") not in {None, "responses", "chat_completions", "ollama", "images"}:
            raise StoreError("Unsupported model API.")
        for field in ("input_price_per_million", "output_price_per_million", "cached_input_price_per_million", "max_request_usd", "temperature"):
            if field in config:
                config[field] = finite_number(config[field], f"{channel}.{field}")
        for field, maximum in (("max_output_tokens", 16_384), ("context_length", 128_000), ("count", 5), ("max_candidates", 5), ("max_retries", 2)):
            if field in config and (type(config[field]) is not int or config[field] < (0 if field == "max_retries" else 1) or config[field] > maximum):
                raise StoreError(f"Invalid {channel}.{field}.")
        if "timeout" in config and not 1 <= finite_number(config["timeout"], f"{channel}.timeout", positive=True) <= 300:
            raise StoreError("Model timeout must be between 1 and 300 seconds.")
        if "pricing" in config:
            prices = config["pricing"]
            if not isinstance(prices, dict) or set(prices) - {"input_per_million", "output_per_million", "cached_input_per_million", "currency"}:
                raise StoreError("Invalid token pricing.")
            for key, value in prices.items():
                if key == "currency":
                    if value != "USD":
                        raise StoreError("Model budgets use USD.")
                else:
                    finite_number(value, key)
        merged[channel] = config
    if "budget_usd" in data:
        merged["budget_usd"] = finite_number(data["budget_usd"], "budget_usd")
    return merged


def export_project(store: Store, project_id: str, *, asset_id: str | None = None) -> bytes:
    state = store.project_state(project_id)
    if any(j["status"] in {"queued", "running"} for j in state["jobs"]):
        raise StoreError("Wait for or cancel active project jobs before exporting.", 409)
    assets = state["assets"] if asset_id is None else [store.get("asset", asset_id)]
    source_ids = {source_id for asset in assets for source_id in asset["source_ids"]}
    sources = state["sources"] if asset_id is None else [s for s in state["sources"] if s["id"] in source_ids]
    manifest = {"format": "figloom", "version": 1, "project": public(state["project"]), "sources": [], "assets": [], "edges": [], "missing_source_ids": []}
    files: dict[str, Path] = {}
    for source in sources:
        value = public(source)
        value["file"] = f'sources/{source["id"]}/{source["name"]}'
        files[value["file"]] = confined_file(store.files_dir, source["_path"])
        value["versions"] = []
        for version in source.get("_versions", []):
            item = public(version)
            item["file"] = f'sources/{source["id"]}/revision-{version["revision"]}/{version["name"]}'
            files[item["file"]] = confined_file(store.files_dir, version["_path"])
            value["versions"].append(item)
        manifest["sources"].append(value)
    for asset in assets:
        snapshots = store.history(asset["id"])
        exported = []
        for snapshot in snapshots:
            value = public(snapshot)
            value["files"] = {}
            entries = [*snapshot["artifacts"], *[f for c in snapshot["candidates"] for f in c.get("files", [])],
                       *[{"name": c["preview_name"]} for c in snapshot["candidates"]]]
            for entry in entries:
                name = entry["name"]
                archive_name = f'assets/{asset["id"]}/revision-{snapshot["revision"]}/{name}'
                files[archive_name] = confined_file(store.data_dir / snapshot["_artifact_dir"], name)
                value["files"][name] = archive_name
            exported.append(value)
        manifest["assets"].append({"current": next(x for x in exported if x["revision"] == asset["revision"]), "history": exported})
    known_sources = {s["id"] for s in sources}
    manifest["missing_source_ids"] = sorted({source_id for item in manifest["assets"] for snapshot in item["history"] for source_id in snapshot["source_ids"]} - known_sources)
    allowed = {a["id"] for a in assets}
    manifest["edges"] = [public(e) for e in state["edges"] if e["source"] in allowed and e["target"] in allowed]
    if sum(p.stat().st_size for p in files.values()) > EXPORT_LIMIT:
        raise StoreError("Project exceeds the 128 MB portable export limit.", 413)
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("manifest.json", json.dumps(manifest, ensure_ascii=False, allow_nan=False, indent=2))
        for name, path in files.items():
            archive.write(path, name)
    return buffer.getvalue()


def import_project(store: Store, data: bytes, *, name: str | None = None) -> dict:
    try:
        archive = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile:
        raise StoreError("Not a valid project ZIP.") from None
    with archive:
        infos = archive.infolist()
        names = [i.filename for i in infos]
        if len(infos) > 1000 or len(set(names)) != len(names) or sum(i.file_size for i in infos) > EXPORT_LIMIT:
            raise StoreError("Project ZIP exceeds the extraction limit or contains duplicate entries.", 413)
        for info in infos:
            parts = PurePosixPath(info.filename)
            if parts.is_absolute() or ".." in parts.parts or "\\" in info.filename or ((info.external_attr >> 16) & 0o170000) == 0o120000:
                raise StoreError("Project ZIP contains an unsafe path.")
        try:
            if archive.getinfo("manifest.json").file_size > 5_000_000:
                raise StoreError("Project manifest exceeds its size limit.", 413)
            manifest = json.loads(archive.read("manifest.json"))
        except (KeyError, ValueError):
            raise StoreError("Project ZIP has no valid manifest.") from None
        if not isinstance(manifest, dict) or manifest.get("format") not in {"figloom", "paper-studio"} or manifest.get("version") != 1:
            raise StoreError("Unsupported project export format.")
        if not isinstance(manifest.get("sources"), list) or not isinstance(manifest.get("assets"), list) or not isinstance(manifest.get("edges"), list):
            raise StoreError("Invalid project manifest collections.")
        project_info = manifest.get("project", {})
        project_name = string(name or project_info.get("name"), "name", 200, required=True)
        description = string(project_info.get("description", ""), "description", 20_000)
        source_map, asset_map = {}, {}
        missing = manifest.get("missing_source_ids", [])
        if not isinstance(missing, list) or any(not isinstance(x, str) for x in missing) or len(set(missing)) != len(missing):
            raise StoreError("Invalid missing-source bindings in project export.")
        missing_map = {x: identifier() for x in missing}
        prepared_sources, prepared_assets, prepared_edges = [], [], []
        created = []
        def materialize(entry: str, target: Path) -> None:
            if not isinstance(entry, str) or entry not in names or archive.getinfo(entry).is_dir():
                raise StoreError("An exported source or artifact is missing.")
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(archive.read(entry))
            created.append(target)
        try:
            for source in manifest["sources"]:
                if not isinstance(source, dict) or not isinstance(source.get("id"), str) or source["id"] in source_map or source["id"] in missing_map:
                    raise StoreError("Invalid source records in project export.")
                new_id = identifier()
                source_map[source["id"]] = new_id
                source_name = string(source.get("name"), "source name", 240, required=True)
                if Path(source_name).name != source_name or "\\" in source_name or Path(source_name).suffix.lower() not in SOURCE_TYPES:
                    raise StoreError("Invalid exported source filename.")
                path = store.files_dir / f'{identifier()}{Path(source_name).suffix.lower()}'
                materialize(source.get("file"), path)
                from .engine import inspect_source
                metadata = inspect_source(path)
                versions = []
                if not isinstance(source.get("versions", []), list) or type(source.get("revision", 1)) is not int or source.get("revision", 1) < 1:
                    raise StoreError("Invalid source revision history.")
                for version in source.get("versions", []):
                    if not isinstance(version, dict) or type(version.get("revision")) is not int or not 1 <= version["revision"] < source.get("revision", 1):
                        raise StoreError("Invalid exported source revision.")
                    version_name = string(version.get("name"), "source name", 240, required=True)
                    if Path(version_name).name != version_name or "\\" in version_name or Path(version_name).suffix.lower() not in SOURCE_TYPES:
                        raise StoreError("Invalid exported source revision filename.")
                    version_path = store.files_dir / f'{identifier()}{Path(version_name).suffix.lower()}'
                    materialize(version.get("file"), version_path)
                    versions.append({"id": new_id, "name": version_name, "size": version_path.stat().st_size,
                                     "revision": version["revision"], "created_at": now(), "updated_at": now(),
                                     "_path": str(version_path.relative_to(store.files_dir)), **inspect_source(version_path)})
                prepared_sources.append({"id": new_id, "name": source_name, "kind": Path(source_name).suffix[1:].lower(), "size": path.stat().st_size,
                                         "revision": source.get("revision", 1), "created_at": now(), "updated_at": now(), "_path": str(path.relative_to(store.files_dir)), "_versions": versions, **metadata})
            for item in manifest["assets"]:
                if not isinstance(item, dict) or not isinstance(item.get("current"), dict) or not isinstance(item.get("history"), list):
                    raise StoreError("Invalid exported asset.")
                old_id = item["current"].get("id")
                if not isinstance(old_id, str) or old_id in asset_map:
                    raise StoreError("Duplicate exported asset IDs.")
                asset_map[old_id] = identifier()
            def remap(value):
                if isinstance(value, dict):
                    return {k: remap(v) for k, v in value.items() if not k.startswith("_") and k not in {"api_key", "url", "preview_url"}}
                if isinstance(value, list):
                    return [remap(v) for v in value]
                if isinstance(value, str):
                    return source_map.get(value, asset_map.get(value, missing_map.get(value, value)))
                return value
            for item in manifest["assets"]:
                old_id = item["current"]["id"]
                snapshots = []
                seen = set()
                for raw in item["history"]:
                    if not isinstance(raw, dict) or raw.get("id") != old_id or type(raw.get("revision")) is not int or raw["revision"] < 1 or raw["revision"] in seen:
                        raise StoreError("Invalid asset revision history.")
                    seen.add(raw["revision"])
                    value = remap(raw)
                    value.pop("files", None)
                    if value.get("kind") not in {"diagram", "plot", "table"} or not isinstance(value.get("spec"), dict) or not isinstance(value.get("source_ids"), list) or any(x not in {*source_map.values(), *missing_map.values()} for x in value["source_ids"]):
                        raise StoreError("Invalid asset specification or source bindings.")
                    if value.get("status") not in {"draft", "stale", "ready", "failed"}:
                        raise StoreError("Invalid imported asset status.")
                    if set(value["source_ids"]) & set(missing_map.values()):
                        value["status"] = "stale"
                    string(value.get("title"), "title", 500, required=True)
                    string(value.get("caption", ""), "caption", 20_000)
                    output = store.files_dir / "artifacts" / asset_map[old_id] / f'import-{identifier()}'
                    mappings = raw.get("files", {})
                    if not isinstance(mappings, dict):
                        raise StoreError("Invalid exported artifact list.")
                    for filename, entry in mappings.items():
                        if not isinstance(filename, str) or Path(filename).is_absolute() or ".." in Path(filename).parts or "\\" in filename:
                            raise StoreError("Invalid artifact path in project export.")
                        materialize(entry, output / filename)
                    value["_artifact_dir"] = str(output.relative_to(store.data_dir))
                    if not mappings and value.get("status") == "ready":
                        value["status"] = "draft"
                    for artifact in value.get("artifacts", []):
                        confined_file(output, artifact.get("name"))
                    for candidate in value.get("candidates", []):
                        confined_file(output, candidate.get("preview_name"))
                        for artifact in candidate.get("files", []):
                            confined_file(output, artifact.get("name"))
                    snapshots.append(value)
                current = next((x for x in snapshots if x["revision"] == item["current"].get("revision")), None)
                if current is None:
                    raise StoreError("The current asset revision is missing.")
                prepared_assets.append((current, snapshots))
            pairs = set()
            for edge in manifest["edges"]:
                if not isinstance(edge, dict) or edge.get("source") not in asset_map or edge.get("target") not in asset_map:
                    raise StoreError("Invalid imported dependency.")
                source, target = asset_map[edge["source"]], asset_map[edge["target"]]
                if source == target or (source, target) in pairs:
                    raise StoreError("Invalid or duplicate imported dependency.")
                pairs.add((source, target))
                prepared_edges.append({"id": identifier(), "source": source, "target": target, "created_at": now()})
            pending = set(asset_map.values())
            while pending:
                ready = {x for x in pending if not any(t == x and s in pending for s, t in pairs)}
                if not ready:
                    raise StoreError("Imported dependencies contain a cycle.")
                pending -= ready
            project = {"id": identifier(), "name": project_name, "description": description, "revision": 1, "created_at": now(), "updated_at": now()}
            with store.transaction() as db:
                store._put(db, "project", project)
                for source in prepared_sources:
                    source["project_id"] = project["id"]
                    store._put(db, "source", source)
                for current, snapshots in prepared_assets:
                    current["project_id"] = project["id"]
                    store._put(db, "asset", current)
                    for snapshot in snapshots:
                        snapshot["project_id"] = project["id"]
                        store._snapshot(db, snapshot)
                for edge in prepared_edges:
                    edge["project_id"] = project["id"]
                    store._put(db, "edge", edge)
            return project
        except BaseException:
            for path in created:
                path.unlink(missing_ok=True)
            raise


def create_app(data_dir: Path, web_dir: Path | None = None, *, start_worker: bool = True) -> FastAPI:
    store = Store(data_dir)
    manager = JobManager(store)
    @asynccontextmanager
    async def lifespan(app: FastAPI):
        if start_worker:
            manager.start()
        try:
            yield
        finally:
            manager.close()
    app = FastAPI(title="Figloom API", version="1.0.0", lifespan=lifespan,
                  openapi_url="/api/v1/openapi.json", docs_url="/api/v1/docs", redoc_url="/api/v1/redoc")
    router = APIRouter(responses=ERROR_RESPONSES)
    app.state.store, app.state.jobs = store, manager

    @app.exception_handler(StoreError)
    async def store_error(request, exc):
        message = exc.detail
        for endpoint in store.settings().values():
            if isinstance(endpoint, dict) and endpoint.get("api_key"):
                message = message.replace(endpoint["api_key"], "[redacted]")
        if re.search(r"(?:^|\s)(?:/(?:Users|home|private|tmp|var|opt|etc)/|[A-Za-z]:[\\/])", message):
            message = "The operation could not use the requested resource."
        return error_response(request, exc.status, message, code=exc.code)

    @app.exception_handler(RequestValidationError)
    async def validation_error(request, exc):
        # Older endpoints historically treated invalid JSON fields as 400. The
        # versioned contract uses 422 consistently for typed request violations.
        legacy_fields = not request.url.path.startswith("/api/v1/") and all(e.get("type") != "json_invalid" and e.get("loc", (None,))[0] == "body" and
            (len(e["loc"]) > 1 and e["loc"][1] != "file" or len(e["loc"]) == 1 and e.get("type") == "value_error") for e in exc.errors())
        status = 400 if legacy_fields else 422
        return error_response(request, status, "Invalid request body or parameters.", details=validation_issues(exc))

    @app.exception_handler(HTTPException)
    async def http_error(request, exc):
        response = error_response(request, exc.status_code, {404: "Resource not found.", 405: "Method not allowed.", 403: "Request is forbidden."}.get(exc.status_code, "The request could not be completed."))
        if exc.headers and "Allow" in exc.headers:
            response.headers["Allow"] = exc.headers["Allow"]
        return response

    @app.exception_handler(ValueError)
    async def value_error(request, exc):
        return error_response(request, 400, "Invalid request. Check the source format and field mappings.")

    @app.exception_handler(Exception)
    async def internal_error(request, exc):
        return error_response(request, 500, "An internal operation failed.")

    @app.middleware("http")
    async def local_only(request: Request, call_next):
        request.state.request_id = identifier()
        try:
            parsed_host = urlparse("http://" + request.headers.get("host", ""))
            host = parsed_host.hostname
            valid_host = host in {"localhost", "127.0.0.1", "::1"} and not any((parsed_host.username, parsed_host.password, parsed_host.path, parsed_host.query, parsed_host.fragment)) and (parsed_host.port is None or 1 <= parsed_host.port <= 65535)
        except ValueError:
            valid_host = False
        if not valid_host:
            return error_response(request, 403, "Figloom accepts loopback hosts only.")
        origin = request.headers.get("origin")
        if origin:
            try:
                parsed = urlparse(origin)
                same_origin = parsed.scheme in {"http", "https"} and parsed.netloc.lower() == request.headers.get("host", "").lower()
            except ValueError:
                same_origin = False
            if not same_origin:
                return error_response(request, 403, "Cross-origin requests are not allowed.")
        length = request.headers.get("content-length")
        if length:
            try:
                limit = 1_000_000 if request.headers.get("content-type", "").split(";", 1)[0] == "application/json" else EXPORT_LIMIT + 1024 * 1024
                if int(length) < 0:
                    return error_response(request, 400, "Invalid Content-Length.")
                if int(length) > limit:
                    return error_response(request, 413, "Request exceeds the size limit.")
            except ValueError:
                return error_response(request, 400, "Invalid Content-Length.")
        try:
            response = await call_next(request)
        except Exception:
            response = error_response(request, 500, "An internal operation failed.")
        response.headers["X-Request-ID"] = request.state.request_id
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Cache-Control"] = "no-store"
        if "/files/" in request.url.path or request.url.path.endswith("/file"):
            response.headers["Content-Security-Policy"] = "sandbox; default-src 'none'; img-src data:; style-src 'unsafe-inline'"
        return response

    @router.get("/health", operation_id="get_health", tags=["health"], response_model=c.HealthDTO, response_model_exclude_unset=True)
    def health():
        return {"status": "ok"}

    @router.get("/projects", operation_id="list_projects", tags=["projects"], response_model=list[c.ProjectDTO], response_model_exclude_unset=True)
    def projects():
        return public(store.list("project"))

    @router.post("/projects", operation_id="create_project", tags=["projects"], response_model=c.ProjectDTO, response_model_exclude_unset=True)
    def create_project(body: c.ProjectCreate):
        body = body.model_dump(exclude_unset=True)
        object_body(body, {"name", "description"}, {"name"})
        return public(store.create_project(string(body["name"], "name", 200, required=True), string(body.get("description", ""), "description", 20_000)))

    @router.post("/projects/import", operation_id="import_project", tags=["projects"], response_model=c.ProjectDTO, response_model_exclude_unset=True)
    async def import_zip(file: UploadFile = File(...)):
        path, _, _ = await upload(file, store.files_dir, source=False)
        try:
            return public(import_project(store, path.read_bytes()))
        finally:
            path.unlink(missing_ok=True)

    @router.get("/projects/{project_id}", operation_id="get_project", tags=["projects"], response_model=c.ProjectStateDTO, response_model_exclude_unset=True)
    def project(project_id: str):
        return state_view(store.project_state(project_id))

    @router.patch("/projects/{project_id}", operation_id="update_project", tags=["projects"], response_model=c.ProjectDTO, response_model_exclude_unset=True)
    def edit_project(project_id: str, body: c.ProjectUpdate):
        body = body.model_dump(exclude_unset=True)
        object_body(body, {"name", "description", "expected_revision"}, {"expected_revision"})
        changes = {k: string(v, k, 200 if k == "name" else 20_000, required=k == "name") for k, v in body.items() if k != "expected_revision"}
        return public(store.update_project(project_id, changes, body["expected_revision"]))

    @router.delete("/projects/{project_id}", operation_id="delete_project", tags=["projects"], response_model=c.DeleteDTO, response_model_exclude_unset=True)
    def remove_project(project_id: str):
        store.delete_project(project_id)
        return {"deleted": True}

    @router.post("/projects/{project_id}/fork", operation_id="fork_project", tags=["projects"], response_model=c.ProjectDTO, response_model_exclude_unset=True)
    def fork_project(project_id: str, body: c.ProjectFork):
        body = body.model_dump(exclude_unset=True)
        object_body(body, {"name"})
        name = string(body["name"], "name", 200, required=True) if "name" in body else store.get("project", project_id)["name"] + " (fork)"
        return public(import_project(store, export_project(store, project_id), name=name))

    @router.get("/projects/{project_id}/export", operation_id="export_project", tags=["projects"], response_model=None, response_class=Response, responses=ZIP_RESPONSE)
    def project_export(project_id: str):
        return Response(export_project(store, project_id), media_type="application/zip", headers={"Content-Disposition": 'attachment; filename="figloom-project.zip"'})

    @router.post("/projects/{project_id}/sources", operation_id="upload_source", tags=["sources"], response_model=c.SourceDTO, response_model_exclude_unset=True)
    async def add_source(project_id: str, file: UploadFile = File(...)):
        store.get("project", project_id)
        path, name, size = await upload(file, store.files_dir)
        try:
            from .engine import inspect_source
            metadata = inspect_source(path)
            return public(store.add_source(project_id, {"id": identifier(), "name": name, "kind": path.suffix[1:], "size": size,
                                                      "_path": str(path.relative_to(store.files_dir)), **metadata}))
        except Exception:
            path.unlink(missing_ok=True)
            raise

    @router.put("/sources/{source_id}", operation_id="replace_source", tags=["sources"], response_model=c.SourceDTO, response_model_exclude_unset=True)
    async def replace_source(source_id: str, file: UploadFile = File(...), expected_revision: int = Form(...)):
        store.get("source", source_id)
        path, name, size = await upload(file, store.files_dir)
        try:
            from .engine import inspect_source
            metadata = inspect_source(path)
            return public(store.replace_source(source_id, {"name": name, "kind": path.suffix[1:], "size": size,
                                                           "_path": str(path.relative_to(store.files_dir)), **metadata}, expected_revision))
        except Exception:
            path.unlink(missing_ok=True)
            raise

    @router.get("/sources/{source_id}", operation_id="get_source", tags=["sources"], response_model=c.SourceDTO, response_model_exclude_unset=True)
    def source(source_id: str):
        return public(store.get("source", source_id))

    @router.get("/sources/{source_id}/file", operation_id="download_source", tags=["sources"], response_model=None, response_class=FileResponse, responses=FILE_RESPONSE)
    def source_file(source_id: str):
        source = store.get("source", source_id)
        return FileResponse(confined_file(store.files_dir, source["_path"]), filename=source["name"])

    @router.delete("/sources/{source_id}", operation_id="delete_source", tags=["sources"], response_model=c.DeleteDTO, response_model_exclude_unset=True)
    def remove_source(source_id: str):
        source = store.delete_source(source_id)
        # Historical files remain available to revision exports; no current
        # dependency can silently consume a deleted source.
        return {"deleted": True, "id": source["id"]}

    @router.post("/projects/{project_id}/assets", operation_id="create_asset", tags=["assets"], response_model=c.AssetDTO, response_model_exclude_unset=True)
    def add_asset(project_id: str, body: c.AssetCreate):
        body = body.model_dump(exclude_unset=True)
        object_body(body, {"kind", "title", "spec", "source_ids", "caption"}, {"kind", "title"})
        from .engine import default_spec
        if body["kind"] not in {"diagram", "plot", "table"}:
            raise StoreError("Unknown asset kind.")
        spec = body.get("spec", default_spec(body["kind"]))
        if not isinstance(spec, dict):
            raise StoreError("spec must be an object.")
        return asset_view(store.create_asset(project_id, body["kind"], string(body["title"], "title", 500, required=True), spec,
                                             body.get("source_ids", []), string(body.get("caption", ""), "caption", 20_000)))

    @router.get("/assets/{asset_id}", operation_id="get_asset", tags=["assets"], response_model=c.AssetDTO, response_model_exclude_unset=True)
    def asset(asset_id: str):
        return asset_view(store.get("asset", asset_id))

    def asset_changes(body: dict) -> dict:
        changes = {k: v for k, v in body.items() if k != "expected_revision"}
        for key in ("title", "caption"):
            if key in changes:
                changes[key] = string(changes[key], key, 500 if key == "title" else 20_000, required=key == "title")
        if "spec" in changes and not isinstance(changes["spec"], dict):
            raise StoreError("spec must be an object.")
        return changes

    @router.patch("/assets/{asset_id}", operation_id="update_asset", tags=["assets"], response_model=c.AssetDTO, response_model_exclude_unset=True, description="Requires the current expected_revision. spec and source_ids replace the entire existing object or array.")
    def edit_asset(asset_id: str, body: c.AssetUpdate):
        body = body.model_dump(exclude_unset=True)
        object_body(body, {"expected_revision", "title", "caption", "spec", "source_ids"}, {"expected_revision"})
        return asset_view(store.update_asset(asset_id, asset_changes(body), body["expected_revision"]))

    @router.delete("/assets/{asset_id}", operation_id="delete_asset", tags=["assets"], response_model=c.DeleteDTO, response_model_exclude_unset=True)
    def remove_asset(asset_id: str):
        store.delete_asset(asset_id)
        return {"deleted": True}

    @router.post("/assets/{asset_id}/fork", operation_id="fork_asset", tags=["assets"], response_model=c.AssetDTO, response_model_exclude_unset=True)
    def fork_asset(asset_id: str, body: c.AssetFork):
        body = body.model_dump(exclude_unset=True)
        object_body(body, {"title"})
        title = string(body["title"], "title", 500, required=True) if "title" in body else None
        return asset_view(store.fork_asset(asset_id, title))

    @router.get("/assets/{asset_id}/history", operation_id="list_asset_history", tags=["assets"], response_model=list[c.AssetDTO], response_model_exclude_unset=True)
    def history(asset_id: str):
        return public(store.history(asset_id))

    @router.post("/assets/{asset_id}/restore", operation_id="restore_asset", tags=["assets"], response_model=c.AssetDTO, response_model_exclude_unset=True)
    def restore(asset_id: str, body: c.AssetRestore):
        body = body.model_dump(exclude_unset=True)
        object_body(body, {"revision", "expected_revision"}, {"revision", "expected_revision"})
        return asset_view(store.restore(asset_id, body["revision"], body["expected_revision"]))

    @router.post("/projects/{project_id}/edges", operation_id="create_edge", tags=["dependencies"], response_model=c.EdgeDTO, response_model_exclude_unset=True)
    def add_edge(project_id: str, body: c.EdgeCreate):
        body = body.model_dump(exclude_unset=True)
        object_body(body, {"source", "target"}, {"source", "target"})
        return public(store.add_edge(project_id, body["source"], body["target"]))

    @router.delete("/projects/{project_id}/edges/{edge_id}", operation_id="delete_edge", tags=["dependencies"], response_model=c.DeleteDTO, response_model_exclude_unset=True)
    def remove_edge(project_id: str, edge_id: str):
        store.delete_edge(project_id, edge_id)
        return {"deleted": True}

    @router.post("/assets/{asset_id}/run", operation_id="run_asset", tags=["jobs"], response_model=c.JobDTO, response_model_exclude_unset=True, description="Accepts work into the durable asynchronous queue. HTTP 200 returns the queued job; poll get_job for its actual terminal status.")
    def run(asset_id: str, body: c.AssetRun):
        body = body.model_dump(exclude_unset=True)
        object_body(body, {"scope", "action"})
        scope, action = body.get("scope", "single"), body.get("action", "render")
        if scope not in {"single", "ancestors", "affected"} or action not in {"render", "generate", "review"}:
            raise StoreError("Unknown action or run scope.")
        job = store.enqueue(asset_id, action, scope)
        manager.notify()
        return public(job)

    @router.get("/jobs/{job_id}", operation_id="get_job", tags=["jobs"], response_model=c.JobDTO, response_model_exclude_unset=True)
    def job(job_id: str):
        return public(store.get("job", job_id))

    @router.post("/jobs/{job_id}/cancel", operation_id="cancel_job", tags=["jobs"], response_model=c.JobDTO, response_model_exclude_unset=True)
    def cancel(job_id: str):
        return public(store.cancel_job(job_id))

    @router.post("/assets/{asset_id}/select", operation_id="select_candidate", tags=["assets"], response_model=c.AssetDTO, response_model_exclude_unset=True)
    def select(asset_id: str, body: c.AssetSelect):
        body = body.model_dump(exclude_unset=True)
        object_body(body, {"candidate_id", "expected_revision"}, {"candidate_id", "expected_revision"})
        asset = store.get("asset", asset_id)
        candidate = next((c for c in asset["candidates"] if c["id"] == body["candidate_id"]), None)
        if candidate:
            for artifact in candidate.get("files", []):
                confined_file(store.data_dir / asset["_artifact_dir"], artifact["name"])
        return asset_view(store.select(asset_id, body["candidate_id"], body["expected_revision"]))

    @router.get("/assets/{asset_id}/files/{name:path}", operation_id="download_artifact", tags=["assets"], response_model=None, response_class=FileResponse, responses=FILE_RESPONSE)
    def artifact_file(asset_id: str, name: str):
        asset = store.get("asset", asset_id)
        allow = {a["name"] for a in asset["artifacts"]} | {c["preview_name"] for c in asset["candidates"]} | {a["name"] for c in asset["candidates"] for a in c.get("files", [])}
        if name not in allow:
            raise StoreError("Artifact not found.", 404)
        return FileResponse(confined_file(store.data_dir / asset["_artifact_dir"], name))

    @router.get("/assets/{asset_id}/export", operation_id="export_asset", tags=["assets"], response_model=None, response_class=Response, responses=ZIP_RESPONSE)
    def asset_export(asset_id: str):
        asset = store.get("asset", asset_id)
        if asset["status"] != "ready":
            raise StoreError("Render the current revision before exporting this asset. Project backups retain draft and stale revisions.", 409)
        return Response(export_project(store, asset["project_id"], asset_id=asset_id), media_type="application/zip", headers={"Content-Disposition": 'attachment; filename="figloom-asset.zip"'})

    @router.post("/projects/{project_id}/chat", operation_id="create_chat", tags=["chat"], response_model=c.JobDTO, response_model_exclude_unset=True, description="Accepts work into the durable asynchronous queue. HTTP 200 returns the queued job; poll get_job for its actual terminal status.")
    def chat(project_id: str, body: c.ChatCreate):
        body = body.model_dump(exclude_unset=True)
        object_body(body, {"message", "asset_id", "expected_revision"}, {"message"})
        job = store.enqueue_chat(project_id, string(body["message"], "message", 12_000, required=True), body.get("asset_id"), body.get("expected_revision"))
        manager.notify()
        return public(job)

    @router.post("/proposals/{proposal_id}/apply", operation_id="apply_proposal", tags=["chat"], response_model=c.AssetDTO, response_model_exclude_unset=True)
    def apply(proposal_id: str, body: c.ProposalApply):
        body = body.model_dump(exclude_unset=True)
        object_body(body, {"expected_revision"}, {"expected_revision"})
        return asset_view(store.apply_proposal(proposal_id, body["expected_revision"]))

    @router.get("/settings", operation_id="get_settings", tags=["settings"], response_model=c.SettingsDTO, response_model_exclude_unset=True)
    def settings():
        return settings_view(store)

    @router.put("/settings", operation_id="update_settings", tags=["settings"], response_model=c.SettingsDTO, response_model_exclude_unset=True, description="Merges supplied endpoint fields. Omitted or empty api_key preserves a saved key only for the same normalized provider connection; switching connections clears it unless a new key is supplied. clear_api_key=true removes the endpoint's own key. An omitted image base_url inherits the text endpoint and, when no own key is supplied, its key; an explicit image endpoint uses only its own key. Scientific settings remain editable.")
    def save_settings(body: c.SettingsUpdate):
        body = body.model_dump(exclude_unset=True)
        store.save_settings(validate_settings(body, store.settings()))
        return settings_view(store)

    def provider_metadata(kind: str, body: c.ProviderProbe):
        from .providers.client import effective_image_config
        from .providers.probe import ProbeError, list_models
        current = store.settings()
        changes = body.model_dump(exclude_unset=True).get("config", {})
        merged = validate_settings({kind: changes}, current)
        config = effective_image_config(merged) if kind == "image" else merged[kind]
        try:
            return config, list_models(config)
        except ProbeError as exc:
            raise StoreError(str(exc), exc.status, code=exc.code) from None

    @router.post("/providers/{kind}/models", operation_id="list_provider_models", tags=["providers"], response_model=c.ProviderModelsDTO,
                 description="Reads provider metadata using temporary configuration merged with saved settings. Does not save settings or submit generation requests.")
    def provider_models(kind: c.ProviderKind, body: c.ProviderProbe):
        _, models = provider_metadata(kind, body)
        return {"models": models, "checked_at": now()}

    @router.post("/providers/{kind}/test", operation_id="test_provider_connection", tags=["providers"], response_model=c.ProviderTestDTO,
                 description="Checks metadata reachability and whether the configured model ID is listed. This does not verify text/image generation capability or submit billable generation work.")
    def provider_test(kind: c.ProviderKind, body: c.ProviderProbe):
        config, models = provider_metadata(kind, body)
        model = config.get("model") or None
        return {"reachable": True, "model": model, "model_available": any(item["id"] == model for item in models) if model else None, "checked_at": now()}

    app.include_router(router, prefix="/api/v1")
    app.include_router(router, prefix="/api", include_in_schema=False)
    app.add_api_route("/health", health, response_model=c.HealthDTO, include_in_schema=False)

    static = Path(web_dir).resolve() if web_dir else Path(__file__).resolve().parents[2] / "apps/web/dist"
    @app.get("/{path:path}", include_in_schema=False)
    def frontend(path: str):
        if path.startswith("api/"):
            raise StoreError("API route not found.", 404)
        if ".." in Path(path).parts or "\\" in path:
            raise StoreError("Invalid path.", 404)
        requested = (static / path).resolve()
        if static not in requested.parents and requested != static:
            raise StoreError("Invalid path.", 404)
        if requested.is_file():
            return FileResponse(requested)
        if path and Path(path).suffix:
            raise StoreError("File not found.", 404)
        if (static / "index.html").is_file():
            return FileResponse(static / "index.html")
        raise StoreError("The Web workspace has not been built. Run the frontend build, then restart Figloom.", 503)
    return app
