"""A durable sequential queue executes actual work and promotes only current outputs."""
from __future__ import annotations

import shutil
import threading
from pathlib import Path

from . import ai
from .budget import BudgetGuard
from .store import Store, StoreError


def confined_file(root: Path, name: str) -> Path:
    if not isinstance(name, str) or not name or "\\" in name or Path(name).is_absolute() or ".." in Path(name).parts:
        raise StoreError("Invalid artifact filename.")
    path = (root / name).resolve()
    if root.resolve() not in path.parents or not path.is_file() or path.is_symlink():
        raise StoreError("Artifact is missing or outside its output directory.", 404)
    # Resolve also detects symlink parents; reject any link before resolution.
    cursor = root / name
    while cursor != root:
        if cursor.is_symlink():
            raise StoreError("Symlink artifacts are not supported.")
        cursor = cursor.parent
    if path.stat().st_size > 100 * 1024 * 1024:
        raise StoreError("Artifact exceeds the 100 MB size limit.")
    return path


def normalize_outputs(root: Path, result: dict) -> dict:
    if not isinstance(result, dict):
        raise StoreError("The renderer returned an invalid result.")
    result = dict(result)
    artifacts = result.get("artifacts", [])
    if not isinstance(artifacts, list) or len(artifacts) > 100:
        raise StoreError("The renderer returned an invalid artifact list.")
    names = set()
    for artifact in artifacts:
        if not isinstance(artifact, dict) or not isinstance(artifact.get("format"), str):
            raise StoreError("The renderer returned an invalid artifact.")
        confined_file(root, artifact.get("name"))
        if artifact["name"] in names:
            raise StoreError("Duplicate artifact filename.")
        names.add(artifact["name"])
    candidates = result.get("candidates", [])
    if not isinstance(candidates, list) or len(candidates) > 5:
        raise StoreError("The renderer returned an invalid candidate list.")
    ids = set()
    for candidate in candidates:
        if not isinstance(candidate, dict) or not isinstance(candidate.get("id"), str) or candidate["id"] in ids:
            raise StoreError("The renderer returned an invalid candidate.")
        ids.add(candidate["id"])
        confined_file(root, candidate.get("preview_name"))
        files = candidate.get("files") or []
        if not files:
            # A candidate is selectable only when its actual output is exported.
            files = [{"name": candidate["preview_name"], "format": Path(candidate["preview_name"]).suffix.lstrip(".")}]
        normalized = []
        for item in files:
            if isinstance(item, str):
                item = {"name": item, "format": Path(item).suffix.lstrip(".")}
            if not isinstance(item, dict) or not item.get("format"):
                raise StoreError("Invalid candidate artifact.")
            confined_file(root, item.get("name"))
            normalized.append(item)
        candidate["files"] = normalized
    result["artifacts"], result["candidates"] = artifacts, candidates
    return result


class JobManager:
    def __init__(self, store: Store):
        self.store = store
        self.wake = threading.Event()
        self.stop = threading.Event()
        self.thread: threading.Thread | None = None

    def start(self) -> None:
        if self.thread and self.thread.is_alive():
            return
        self.store.recover_jobs()
        self.stop.clear()
        self.thread = threading.Thread(target=self._loop, name="figloom-worker", daemon=True)
        self.thread.start()

    def notify(self) -> None:
        self.wake.set()

    def close(self) -> None:
        self.stop.set()
        self.wake.set()
        if self.thread:
            self.thread.join(timeout=2)

    def cancelled(self, job_id: str) -> bool:
        if self.stop.is_set():
            return True
        try:
            return self.store.get("job", job_id)["status"] != "running"
        except StoreError:
            return True

    def _loop(self) -> None:
        while not self.stop.is_set():
            job = self.store.claim_job()
            if not job:
                self.wake.wait(timeout=1)
                self.wake.clear()
                continue
            self.execute(job)

    def _client(self, job: dict):
        from .engine import make_client
        settings = self.store.settings()
        return make_client(settings, guard=BudgetGuard(self.store, settings, cancelled=lambda: self.cancelled(job["id"])))

    def execute(self, job: dict) -> None:
        created_dirs = []
        current_target = None
        try:
            from .engine import render_asset
            if job["action"] == "chat":
                self.store.update_job(job["id"], stage="model_request")
                answer, changes, summary = ai.chat(self.store, job, self._client(job))
                self.store.finish_chat(job["id"], answer, changes, summary)
                return
            text = self.store.settings().get("text") or {}
            client = self._client(job) if job["action"] == "generate" or (job["action"] == "review" and text.get("base_url") and text.get("model")) else None
            completed = []
            for target in job["_targets"]:
                current_target = target
                if self.cancelled(job["id"]):
                    return
                asset = self.store.get("asset", target["id"])
                if asset["revision"] != target["revision"]:
                    raise StoreError("The asset changed before this job executed. Run the current revision.", 409)
                if job["action"] == "review" and asset["status"] != "ready":
                    raise StoreError("Render the current asset revision before reviewing its files.", 409)
                sources = []
                for source_id in asset["source_ids"]:
                    source = self.store.get("source", source_id)
                    source["path"] = str(confined_file(self.store.files_dir, source["_path"]))
                    sources.append(source)
                parents = [edge["source"] for edge in self.store.list("edge", job["project_id"]) if edge["target"] == asset["id"]]
                asset["upstream"] = []
                for parent_id in parents:
                    parent = self.store.get("asset", parent_id)
                    if parent["status"] != "ready":
                        raise StoreError("An upstream asset is not current. Run its ancestors before this asset.", 409)
                    parent["artifact_dir"] = str((self.store.data_dir / parent.get("_artifact_dir", "")).resolve())
                    asset["upstream"].append(parent)
                if asset.get("_artifact_dir"):
                    asset["_artifact_dir"] = str((self.store.data_dir / asset["_artifact_dir"]).resolve())
                output = self.store.files_dir / "artifacts" / asset["id"] / job["id"]
                output.mkdir(parents=True, exist_ok=False)
                created_dirs.append(output)
                self.store.update_job(job["id"], stage=f'{job["action"]}:{asset["title"]}')
                result = render_asset(asset, sources, output, client=client, action=job["action"], cancelled=lambda: self.cancelled(job["id"]))
                if self.cancelled(job["id"]):
                    return
                result = normalize_outputs(output, result)
                if not result["artifacts"] and job["action"] != "review":
                    raise StoreError("The renderer produced no files.")
                if job["action"] == "review" and asset.get("_artifact_dir"):
                    # A review produces a new report about the existing files.
                    # Retain those exact files in this revision's output bundle.
                    previous = Path(asset["_artifact_dir"])
                    keep = {a["name"] for a in asset["artifacts"]} | {c["preview_name"] for c in asset["candidates"]} | {a["name"] for c in asset["candidates"] for a in c.get("files", [])}
                    for name in keep:
                        destination = output / name
                        if not destination.exists():
                            destination.parent.mkdir(parents=True, exist_ok=True)
                            shutil.copyfile(confined_file(previous, name), destination)
                    result["artifacts"] = list({a["name"]: a for a in [*asset["artifacts"], *result["artifacts"]]}.values())
                    result["candidates"] = asset["candidates"]
                artifact_dir = str(output.relative_to(self.store.data_dir))
                promoted = self.store.promote(job["id"], target, result, artifact_dir)
                completed.append({"asset_id": promoted["id"], "revision": promoted["revision"]})
            self.store.update_job(job["id"], status="completed", stage="completed", result={"assets": completed})
        except Exception as exc:
            # Never expose credentials/provider bodies in job diagnostics.
            safe = str(exc) if isinstance(exc, (StoreError, ValueError)) or type(exc).__name__ in {"ProviderError", "FigureError", "BudgetExceeded"} else f"{type(exc).__name__}: execution failed. Check source bindings and specification."
            for provider in self.store.settings().values():
                if isinstance(provider, dict) and provider.get("api_key"):
                    safe = safe.replace(provider["api_key"], "[redacted]")
            active = not self.cancelled(job["id"])
            self.store.update_job(job["id"], status="failed", stage="failed", error=safe[:2000])
            if active and current_target and job["action"] not in {"chat", "review"}:
                with self.store.transaction() as db:
                    try:
                        asset = self.store._get(db, "asset", current_target["id"])
                    except StoreError:
                        asset = None
                    if asset and asset["revision"] == current_target["revision"]:
                        asset["status"] = "failed"
                        self.store._put(db, "asset", asset)
                        self.store._snapshot(db, asset)
        finally:
            # Failed/cancelled unpromoted files are runtime cleanup, never source
            # artifacts and never reported as successful candidates.
            referenced = {a.get("_artifact_dir") for a in self.store.list("asset", job["project_id"])}
            for directory in created_dirs:
                if str(directory.relative_to(self.store.data_dir)) not in referenced:
                    shutil.rmtree(directory, ignore_errors=True)
