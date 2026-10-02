"""Transactional local project storage. Runtime files live beside this database."""
from __future__ import annotations

import json
import os
import shutil
import sqlite3
import threading
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def identifier() -> str:
    return str(uuid.uuid4())


class StoreError(Exception):
    def __init__(self, detail: str, status: int = 400, *, code: str | None = None):
        super().__init__(detail)
        self.detail, self.status = detail, status
        self.code = code


def public(value: Any) -> Any:
    if isinstance(value, dict):
        return {k: public(v) for k, v in value.items() if not k.startswith("_") and k != "api_key"}
    if isinstance(value, list):
        return [public(v) for v in value]
    return value


class Store:
    def __init__(self, data_dir: Path):
        self.data_dir = Path(data_dir).expanduser().resolve()
        package_root = Path(__file__).resolve().parent
        checkout = package_root.parent.parent
        source_root = checkout if (checkout / "pyproject.toml").is_file() and (checkout / "src" / "figloom").resolve() == package_root else package_root
        if self.data_dir == source_root or source_root in self.data_dir.parents:
            raise StoreError("Choose a data directory outside the application source tree.")
        self.data_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.files_dir = self.data_dir / "files"
        self.files_dir.mkdir(exist_ok=True, mode=0o700)
        self.db_path = self.data_dir / "studio.sqlite3"
        self.lock = threading.RLock()
        with self.transaction() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS objects (
                    kind TEXT NOT NULL, id TEXT NOT NULL, project_id TEXT,
                    data TEXT NOT NULL, PRIMARY KEY(kind,id));
                CREATE INDEX IF NOT EXISTS project_objects ON objects(project_id,kind);
                CREATE TABLE IF NOT EXISTS history (
                    asset_id TEXT NOT NULL, revision INTEGER NOT NULL,
                    data TEXT NOT NULL, PRIMARY KEY(asset_id,revision));
                CREATE TABLE IF NOT EXISTS settings (id INTEGER PRIMARY KEY CHECK(id=1), data TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS spending (
                    id TEXT PRIMARY KEY, status TEXT NOT NULL, reserved REAL NOT NULL,
                    charged REAL NOT NULL DEFAULT 0, operation TEXT NOT NULL,
                    created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
            """)
            db.execute("INSERT OR IGNORE INTO settings VALUES (1,?)", (json.dumps({"text": {}, "image": {}, "budget_usd": 0.0}),))
        os.chmod(self.db_path, 0o600)

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        with self.lock:
            db = sqlite3.connect(self.db_path, timeout=30)
            db.row_factory = sqlite3.Row
            db.execute("PRAGMA foreign_keys=ON")
            try:
                db.execute("BEGIN IMMEDIATE")
                yield db
                db.commit()
            except BaseException:
                db.rollback()
                raise
            finally:
                db.close()

    def _get(self, db: sqlite3.Connection, kind: str, object_id: str) -> dict:
        row = db.execute("SELECT data FROM objects WHERE kind=? AND id=?", (kind, object_id)).fetchone()
        if not row:
            raise StoreError(f"{kind.capitalize()} not found.", 404)
        return json.loads(row[0])

    def _list(self, db: sqlite3.Connection, kind: str, project_id: str | None = None) -> list[dict]:
        if project_id is None:
            rows = db.execute("SELECT data FROM objects WHERE kind=? ORDER BY rowid", (kind,)).fetchall()
        else:
            rows = db.execute("SELECT data FROM objects WHERE kind=? AND project_id=? ORDER BY rowid", (kind, project_id)).fetchall()
        return [json.loads(row[0]) for row in rows]

    def _put(self, db: sqlite3.Connection, kind: str, value: dict) -> dict:
        db.execute("INSERT OR REPLACE INTO objects(kind,id,project_id,data) VALUES (?,?,?,?)",
                   (kind, value["id"], value.get("project_id"), json.dumps(value, ensure_ascii=False, allow_nan=False)))
        return value

    def get(self, kind: str, object_id: str) -> dict:
        with self.transaction() as db:
            return self._get(db, kind, object_id)

    def list(self, kind: str, project_id: str | None = None) -> list[dict]:
        with self.transaction() as db:
            return self._list(db, kind, project_id)

    def put(self, kind: str, value: dict) -> dict:
        with self.transaction() as db:
            return self._put(db, kind, value)

    def create_project(self, name: str, description: str = "") -> dict:
        project = {"id": identifier(), "name": name, "description": description, "revision": 1,
                   "created_at": now(), "updated_at": now()}
        return self.put("project", project)

    @staticmethod
    def _expect(value: dict, expected: int) -> None:
        if type(expected) is not int or expected != value["revision"]:
            raise StoreError("Revision changed. Reload before applying this edit.", 409, code="revision_conflict")

    def update_project(self, project_id: str, changes: dict, expected: int) -> dict:
        with self.transaction() as db:
            project = self._get(db, "project", project_id)
            self._expect(project, expected)
            project.update(changes, revision=project["revision"] + 1, updated_at=now())
            return self._put(db, "project", project)

    def project_state(self, project_id: str) -> dict:
        with self.transaction() as db:
            return {"project": self._get(db, "project", project_id), **{
                plural: self._list(db, kind, project_id) for kind, plural in
                (("asset", "assets"), ("source", "sources"), ("edge", "edges"), ("job", "jobs"),
                 ("message", "messages"), ("proposal", "proposals"))}}

    def add_source(self, project_id: str, metadata: dict) -> dict:
        with self.transaction() as db:
            self._get(db, "project", project_id)
            source = {**metadata, "project_id": project_id, "revision": 1, "created_at": now(), "updated_at": now(), "_versions": []}
            return self._put(db, "source", source)

    def replace_source(self, source_id: str, metadata: dict, expected: int) -> dict:
        with self.transaction() as db:
            source = self._get(db, "source", source_id)
            self._expect(source, expected)
            versions = source.get("_versions", []) + [{k: v for k, v in source.items() if k != "_versions"}]
            source = {**metadata, "id": source_id, "project_id": source["project_id"], "created_at": source["created_at"],
                      "updated_at": now(), "revision": source["revision"] + 1, "_versions": versions}
            self._put(db, "source", source)
            affected = {a["id"] for a in self._list(db, "asset", source["project_id"]) if source_id in a["source_ids"]}
            self._invalidate(db, source["project_id"], affected)
            return source

    def _validate_sources(self, db: sqlite3.Connection, project_id: str, source_ids: list[str]) -> None:
        if not isinstance(source_ids, list) or any(not isinstance(x, str) for x in source_ids) or len(source_ids) > 100:
            raise StoreError("source_ids must be a list of at most 100 source IDs.")
        if len(set(source_ids)) != len(source_ids):
            raise StoreError("Duplicate source IDs.")
        for source_id in source_ids:
            if self._get(db, "source", source_id)["project_id"] != project_id:
                raise StoreError("Sources must belong to this project.")

    def _snapshot(self, db: sqlite3.Connection, asset: dict) -> None:
        db.execute("INSERT OR REPLACE INTO history VALUES (?,?,?)",
                   (asset["id"], asset["revision"], json.dumps(asset, ensure_ascii=False, allow_nan=False)))

    def create_asset(self, project_id: str, kind: str, title: str, spec: dict,
                     source_ids: list[str] | None = None, caption: str = "") -> dict:
        if kind not in {"diagram", "plot", "table"}:
            raise StoreError("Unknown asset kind.")
        with self.transaction() as db:
            self._get(db, "project", project_id)
            self._validate_sources(db, project_id, source_ids or [])
            asset = {"id": identifier(), "project_id": project_id, "kind": kind, "title": title,
                     "spec": spec, "source_ids": source_ids or [], "caption": caption, "revision": 1,
                     "status": "draft", "artifacts": [], "candidates": [], "review": None,
                     "created_at": now(), "updated_at": now()}
            self._put(db, "asset", asset)
            self._snapshot(db, asset)
            return asset

    def _descendants(self, db: sqlite3.Connection, project_id: str, asset_ids: set[str]) -> set[str]:
        result = set(asset_ids)
        edges = self._list(db, "edge", project_id)
        while True:
            more = {e["target"] for e in edges if e["source"] in result} - result
            if not more:
                return result
            result.update(more)

    def _reject_jobs(self, db: sqlite3.Connection, project_id: str, asset_ids: set[str], reason: str, *, skip_job_id: str | None = None) -> None:
        for job in self._list(db, "job", project_id):
            bound = {t["id"] for t in job.get("_targets", [])} | {job.get("asset_id")}
            if job["id"] != skip_job_id and job["status"] in {"queued", "running"} and bound & asset_ids:
                job.update(status="failed", stage="invalidated", error=reason, updated_at=now())
                self._put(db, "job", job)

    def _invalidate(self, db: sqlite3.Connection, project_id: str, asset_ids: set[str], *, exclude: set[str] | None = None) -> None:
        affected = self._descendants(db, project_id, asset_ids) - (exclude or set())
        for asset_id in affected:
            try:
                asset = self._get(db, "asset", asset_id)
            except StoreError:
                continue
            asset.update(status="stale", revision=asset["revision"] + 1, updated_at=now())
            self._put(db, "asset", asset)
            self._snapshot(db, asset)
        self._reject_jobs(db, project_id, affected | asset_ids, "Inputs or specification changed during this job. Run again with the current revision.")

    def update_asset(self, asset_id: str, changes: dict, expected: int) -> dict:
        with self.transaction() as db:
            asset = self._get(db, "asset", asset_id)
            self._expect(asset, expected)
            if "source_ids" in changes:
                self._validate_sources(db, asset["project_id"], changes["source_ids"])
            asset.update(changes, revision=asset["revision"] + 1, status="stale", updated_at=now())
            self._put(db, "asset", asset)
            self._snapshot(db, asset)
            self._invalidate(db, asset["project_id"], {asset_id}, exclude={asset_id})
            return asset

    def history(self, asset_id: str) -> list[dict]:
        with self.transaction() as db:
            self._get(db, "asset", asset_id)
            return [json.loads(row[0]) for row in db.execute("SELECT data FROM history WHERE asset_id=? ORDER BY revision DESC", (asset_id,))]

    def restore(self, asset_id: str, revision: int, expected: int) -> dict:
        copied = None
        try:
            with self.transaction() as db:
                asset = self._get(db, "asset", asset_id)
                self._expect(asset, expected)
                row = db.execute("SELECT data FROM history WHERE asset_id=? AND revision=?", (asset_id, revision)).fetchone()
                if not row:
                    raise StoreError("Revision not found.", 404)
                snapshot = json.loads(row[0])
                self._validate_sources(db, asset["project_id"], snapshot["source_ids"])
                copied = self._copy_retained(snapshot, asset_id, "restore")
                for key in ("title", "caption", "spec", "source_ids", "artifacts", "candidates", "review"):
                    asset[key] = snapshot[key]
                if copied:
                    asset["_artifact_dir"] = copied
                else:
                    asset.pop("_artifact_dir", None)
                asset.update(revision=asset["revision"] + 1, status="stale", updated_at=now())
                self._put(db, "asset", asset)
                self._snapshot(db, asset)
                self._invalidate(db, asset["project_id"], {asset_id}, exclude={asset_id})
                return asset
        except BaseException:
            if copied:
                shutil.rmtree(self.data_dir / copied, ignore_errors=True)
            raise

    def _copy_retained(self, snapshot: dict, asset_id: str, operation: str) -> str | None:
        names = {a["name"] for a in snapshot.get("artifacts", [])}
        for candidate in snapshot.get("candidates", []):
            names.add(candidate["preview_name"])
            names.update(a["name"] for a in candidate.get("files", []))
        retained = snapshot["spec"].get("finished_raster_file")
        if retained and retained not in names:
            raise StoreError("The retained raster is not part of the actual allowlisted artifact bundle.", 409)
        if not names:
            return None
        from .jobs import confined_file
        raw = snapshot.get("_artifact_dir")
        if not isinstance(raw, str):
            raise StoreError("The retained artifact bundle is missing.", 409)
        root = (self.data_dir / raw).resolve()
        if self.data_dir not in root.parents:
            raise StoreError("The retained artifact bundle is outside the data directory.")
        paths = {name: confined_file(root, name) for name in names}
        if len(paths) > 500 or sum(path.stat().st_size for path in paths.values()) > 128 * 1024 * 1024:
            raise StoreError("The retained bundle exceeds the copy limit.", 413)
        output = self.files_dir / "artifacts" / asset_id / f"{operation}-{identifier()}"
        try:
            for name, path in paths.items():
                target = output / name
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(path, target)
        except BaseException:
            shutil.rmtree(output, ignore_errors=True)
            raise
        return str(output.relative_to(self.data_dir))

    def fork_asset(self, asset_id: str, title: str | None = None) -> dict:
        copied = None
        try:
            with self.transaction() as db:
                asset = self._get(db, "asset", asset_id)
                self._validate_sources(db, asset["project_id"], asset["source_ids"])
                new_id = identifier()
                copied = self._copy_retained(asset, new_id, "fork")
                fork = {**asset, "id": new_id, "title": title or f'{asset["title"]} (fork)', "revision": 1,
                        "status": "stale" if copied else "draft", "created_at": now(), "updated_at": now(),
                        "forked_from": {"asset_id": asset_id, "revision": asset["revision"]}}
                if copied:
                    fork["_artifact_dir"] = copied
                else:
                    fork.pop("_artifact_dir", None)
                self._put(db, "asset", fork)
                self._snapshot(db, fork)
                return fork
        except BaseException:
            if copied:
                shutil.rmtree(self.data_dir / copied, ignore_errors=True)
            raise

    def delete_source(self, source_id: str) -> dict:
        with self.transaction() as db:
            source = self._get(db, "source", source_id)
            affected = {a["id"] for a in self._list(db, "asset", source["project_id"]) if source_id in a["source_ids"]}
            self._invalidate(db, source["project_id"], affected)
            for asset_id in affected:
                asset = self._get(db, "asset", asset_id)
                asset["source_ids"] = [x for x in asset["source_ids"] if x != source_id]
                if asset["spec"].get("source_id") == source_id:
                    asset["spec"]["source_id"] = None
                self._put(db, "asset", asset)
                self._snapshot(db, asset)
            db.execute("DELETE FROM objects WHERE kind='source' AND id=?", (source_id,))
            return source

    def delete_asset(self, asset_id: str) -> None:
        with self.transaction() as db:
            asset = self._get(db, "asset", asset_id)
            self._invalidate(db, asset["project_id"], {asset_id}, exclude={asset_id})
            for edge in self._list(db, "edge", asset["project_id"]):
                if asset_id in {edge["source"], edge["target"]}:
                    db.execute("DELETE FROM objects WHERE kind='edge' AND id=?", (edge["id"],))
            db.execute("DELETE FROM objects WHERE kind='asset' AND id=?", (asset_id,))
            db.execute("DELETE FROM history WHERE asset_id=?", (asset_id,))

    def delete_project(self, project_id: str) -> None:
        with self.transaction() as db:
            self._get(db, "project", project_id)
            if any(j["status"] in {"queued", "running"} for j in self._list(db, "job", project_id)):
                raise StoreError("Cancel active project jobs before deleting the project.", 409)
            for asset in self._list(db, "asset", project_id):
                db.execute("DELETE FROM history WHERE asset_id=?", (asset["id"],))
            db.execute("DELETE FROM objects WHERE project_id=? OR (kind='project' AND id=?)", (project_id, project_id))

    def add_edge(self, project_id: str, source: str, target: str) -> dict:
        with self.transaction() as db:
            self._get(db, "project", project_id)
            a, b = self._get(db, "asset", source), self._get(db, "asset", target)
            if a["project_id"] != project_id or b["project_id"] != project_id:
                raise StoreError("Dependency endpoints must belong to this project.")
            if source == target or source in self._descendants(db, project_id, {target}):
                raise StoreError("This dependency would create a cycle.")
            for edge in self._list(db, "edge", project_id):
                if edge["source"] == source and edge["target"] == target:
                    return edge
            edge = {"id": identifier(), "project_id": project_id, "source": source, "target": target, "created_at": now()}
            self._put(db, "edge", edge)
            self._invalidate(db, project_id, {target})
            return edge

    def delete_edge(self, project_id: str, edge_id: str) -> None:
        with self.transaction() as db:
            edge = self._get(db, "edge", edge_id)
            if edge["project_id"] != project_id:
                raise StoreError("Dependency not found.", 404)
            db.execute("DELETE FROM objects WHERE kind='edge' AND id=?", (edge_id,))
            self._invalidate(db, project_id, {edge["target"]})

    def enqueue(self, asset_id: str, action: str, scope: str) -> dict:
        with self.transaction() as db:
            asset = self._get(db, "asset", asset_id)
            project_id = asset["project_id"]
            edges = self._list(db, "edge", project_id)
            selected = {asset_id}
            if scope == "affected":
                selected = self._descendants(db, project_id, selected)
            elif scope == "ancestors":
                while True:
                    more = {e["source"] for e in edges if e["target"] in selected} - selected
                    if not more:
                        break
                    selected.update(more)
            targets = []
            while selected:
                ready = sorted(x for x in selected if not any(e["target"] == x and e["source"] in selected for e in edges))
                if not ready:
                    raise StoreError("Dependency graph contains a cycle.")
                for x in ready:
                    current = self._get(db, "asset", x)
                    self._validate_sources(db, project_id, current["source_ids"])
                    targets.append({"id": x, "revision": current["revision"], "source_ids": current["source_ids"]})
                    selected.remove(x)
            job = {"id": identifier(), "project_id": project_id, "asset_id": asset_id,
                   "action": action, "scope": scope, "status": "queued", "stage": "queued",
                   "created_at": now(), "updated_at": now(), "_targets": targets}
            return self._put(db, "job", job)

    def recover_jobs(self) -> None:
        with self.transaction() as db:
            for job in self._list(db, "job"):
                if job["status"] == "running":
                    job.update(status="failed", stage="interrupted", error="The service stopped during this job. Outputs were not promoted; run it again.", updated_at=now())
                    self._put(db, "job", job)
            # A request in flight at shutdown has an unknown charge, so retain its
            # reserved upper bound as spent rather than silently refunding it.
            db.execute("UPDATE spending SET status='uncertain',charged=reserved,updated_at=? WHERE status='reserved'", (now(),))

    def claim_job(self) -> dict | None:
        with self.transaction() as db:
            for job in self._list(db, "job"):
                if job["status"] == "queued":
                    job.update(status="running", stage="starting", updated_at=now())
                    return self._put(db, "job", job)
        return None

    def update_job(self, job_id: str, **changes: Any) -> dict:
        with self.transaction() as db:
            job = self._get(db, "job", job_id)
            if job["status"] in {"cancelled", "failed", "completed"}:
                return job
            job.update(changes, updated_at=now())
            return self._put(db, "job", job)

    def cancel_job(self, job_id: str) -> dict:
        return self.update_job(job_id, status="cancelled", stage="cancelled", error="Cancelled by the user. No further outputs will be promoted.")

    def promote(self, job_id: str, target: dict, result: dict, artifact_dir: str) -> dict:
        with self.transaction() as db:
            job = self._get(db, "job", job_id)
            if job["status"] != "running":
                raise StoreError("Job is no longer running.", 409)
            asset = self._get(db, "asset", target["id"])
            if asset["revision"] != target["revision"] or asset["source_ids"] != target["source_ids"]:
                raise StoreError("Asset revision changed while rendering. Run again.", 409)
            self._validate_sources(db, asset["project_id"], asset["source_ids"])
            changed = any(key in result and result[key] != asset.get(key) for key in ("caption", "spec"))
            if changed:
                asset["revision"] += 1
                all_affected = self._descendants(db, asset["project_id"], {asset["id"]})
                in_flight = {t["id"] for t in job.get("_targets", [])}
                for affected_id in all_affected - in_flight:
                    descendant = self._get(db, "asset", affected_id)
                    descendant.update(revision=descendant["revision"] + 1, status="stale", updated_at=now())
                    self._put(db, "asset", descendant)
                    self._snapshot(db, descendant)
                self._reject_jobs(db, asset["project_id"], all_affected, "An upstream generation changed the specification. Run the current revision.", skip_job_id=job_id)
            for key in ("artifacts", "candidates", "review", "caption", "spec"):
                if key in result:
                    asset[key] = result[key]
            asset.update(status="ready", _artifact_dir=artifact_dir, updated_at=now())
            self._put(db, "asset", asset)
            self._snapshot(db, asset)
            return asset

    def select(self, asset_id: str, candidate_id: str, expected: int) -> dict:
        with self.transaction() as db:
            asset = self._get(db, "asset", asset_id)
            self._expect(asset, expected)
            candidate = next((x for x in asset["candidates"] if x["id"] == candidate_id), None)
            if not candidate:
                raise StoreError("Candidate not found.", 404)
            if not candidate.get("files"):
                raise StoreError("Candidate does not have exportable files.")
            asset["artifacts"] = candidate["files"]
            if isinstance(candidate.get("spec"), dict):
                asset["spec"] = candidate["spec"]
            if isinstance(candidate.get("caption"), str):
                asset["caption"] = candidate["caption"]
            if isinstance(candidate.get("review"), dict):
                asset["review"] = candidate["review"]
            for x in asset["candidates"]:
                x["selected"] = x["id"] == candidate_id
            asset.update(revision=asset["revision"] + 1, updated_at=now())
            self._put(db, "asset", asset)
            self._snapshot(db, asset)
            self._invalidate(db, asset["project_id"], {asset_id}, exclude={asset_id})
            return asset

    def settings(self) -> dict:
        with self.transaction() as db:
            return json.loads(db.execute("SELECT data FROM settings WHERE id=1").fetchone()[0])

    def save_settings(self, settings: dict) -> None:
        with self.transaction() as db:
            db.execute("UPDATE settings SET data=? WHERE id=1", (json.dumps(settings, allow_nan=False),))

    def enqueue_chat(self, project_id: str, message: str, asset_id: str | None, expected: int | None) -> dict:
        with self.transaction() as db:
            self._get(db, "project", project_id)
            asset = self._get(db, "asset", asset_id) if asset_id else None
            if asset and asset["project_id"] != project_id:
                raise StoreError("Asset must belong to this project.")
            if asset:
                self._expect(asset, expected)
            self._put(db, "message", {"id": identifier(), "project_id": project_id, "role": "user", "content": message,
                                       "asset_id": asset_id, "created_at": now()})
            job = {"id": identifier(), "project_id": project_id, "asset_id": asset_id, "action": "chat", "scope": "single",
                   "status": "queued", "stage": "queued", "created_at": now(), "updated_at": now(),
                   "_message": message, "_base_revision": asset["revision"] if asset else None,
                   "_targets": [{"id": asset_id, "revision": asset["revision"]}] if asset else []}
            return self._put(db, "job", job)

    def finish_chat(self, job_id: str, answer: str, changes: dict | None, summary: str) -> dict:
        with self.transaction() as db:
            job = self._get(db, "job", job_id)
            if job["status"] != "running":
                raise StoreError("Chat was cancelled or invalidated.", 409)
            proposal = None
            if changes and job["asset_id"]:
                asset = self._get(db, "asset", job["asset_id"])
                if asset["revision"] != job["_base_revision"]:
                    raise StoreError("The asset changed during this chat. Request a new proposal.", 409)
                proposal = {"id": identifier(), "project_id": job["project_id"], "asset_id": job["asset_id"],
                            "base_revision": job["_base_revision"], "changes": changes, "summary": summary,
                            "status": "pending", "created_at": now()}
                self._put(db, "proposal", proposal)
            message = {"id": identifier(), "project_id": job["project_id"], "asset_id": job["asset_id"], "role": "assistant",
                       "content": answer, "created_at": now()}
            self._put(db, "message", message)
            job.update(status="completed", stage="completed", result={"message": message, "proposal": proposal}, updated_at=now())
            return self._put(db, "job", job)

    def apply_proposal(self, proposal_id: str, expected: int) -> dict:
        with self.transaction() as db:
            proposal = self._get(db, "proposal", proposal_id)
            asset = self._get(db, "asset", proposal["asset_id"])
            if proposal["status"] != "pending" or proposal["base_revision"] != expected:
                raise StoreError("This proposal is stale or has already been applied.", 409, code="revision_conflict")
            self._expect(asset, expected)
            changes = proposal["changes"]
            if "source_ids" in changes:
                self._validate_sources(db, asset["project_id"], changes["source_ids"])
            asset.update(changes, revision=asset["revision"] + 1, status="stale", updated_at=now())
            self._put(db, "asset", asset)
            self._snapshot(db, asset)
            self._invalidate(db, asset["project_id"], {asset["id"]}, exclude={asset["id"]})
            proposal.update(status="applied", applied_revision=asset["revision"])
            self._put(db, "proposal", proposal)
            return asset
