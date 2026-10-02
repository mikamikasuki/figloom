import pytest

from figloom.store import Store, StoreError, identifier


def test_durable_revisions_dependencies_and_stale_jobs(tmp_path):
    store = Store(tmp_path / "data")
    project = store.create_project("Study")
    a = store.create_asset(project["id"], "plot", "Curve", {"x": "epoch"})
    b = store.create_asset(project["id"], "table", "Table", {})
    store.add_edge(project["id"], a["id"], b["id"])
    b = store.get("asset", b["id"])
    assert b["revision"] == 2
    job = store.enqueue(b["id"], "render", "ancestors")
    assert [t["id"] for t in job["_targets"]] == [a["id"], b["id"]]
    store.update_asset(a["id"], {"title": "Updated curve"}, 1)
    assert store.get("job", job["id"])["status"] == "failed"
    assert store.get("asset", b["id"])["revision"] == 3
    with pytest.raises(StoreError, match="Revision changed"):
        store.update_asset(a["id"], {"title": "Lost edit"}, 1)
    with pytest.raises(StoreError, match="cycle"):
        store.add_edge(project["id"], b["id"], a["id"])
    reopened = Store(tmp_path / "data")
    assert reopened.get("asset", a["id"])["title"] == "Updated curve"
    restored = reopened.restore(a["id"], 1, 2)
    assert restored["revision"] == 3 and restored["title"] == "Curve"
    fork = reopened.fork_asset(a["id"])
    assert fork["revision"] == 1 and fork["spec"] == restored["spec"]


def test_generation_preserves_history_and_stales_proposal(tmp_path):
    store = Store(tmp_path)
    project = store.create_project("Study")
    asset = store.create_asset(project["id"], "diagram", "Mechanism", {"brief": "Original"})
    job = store.enqueue(asset["id"], "generate", "single")
    store.claim_job()
    promoted = store.promote(job["id"], job["_targets"][0], {"spec": {"brief": "Generated from input"}, "artifacts": [], "candidates": []}, "files/output")
    assert promoted["revision"] == 2
    history = store.history(asset["id"])
    assert history[1]["spec"]["brief"] == "Original"
    proposal = {"id": identifier(), "asset_id": asset["id"], "project_id": project["id"], "base_revision": 1,
                "changes": {"caption": "A proposal based on the earlier revision"}, "summary": "Change caption", "status": "pending"}
    store.put("proposal", proposal)
    with pytest.raises(StoreError):
        store.apply_proposal(proposal["id"], 2)


def test_recovery_does_not_invent_success_and_delete_guards(tmp_path):
    store = Store(tmp_path)
    project = store.create_project("Study")
    asset = store.create_asset(project["id"], "plot", "Curve", {})
    job = store.enqueue(asset["id"], "render", "single")
    store.claim_job()
    with pytest.raises(StoreError, match="Cancel active"):
        store.delete_project(project["id"])
    store.recover_jobs()
    assert store.get("job", job["id"])["status"] == "failed"
    assert store.get("asset", asset["id"])["status"] == "draft"
    store.delete_project(project["id"])
    with pytest.raises(StoreError):
        store.get("project", project["id"])


def test_cross_project_sources_are_rejected(tmp_path):
    store = Store(tmp_path)
    first, second = store.create_project("First"), store.create_project("Second")
    source = store.add_source(first["id"], {"id": identifier(), "name": "input.csv", "_path": "actual.csv"})
    with pytest.raises(StoreError, match="belong"):
        store.create_asset(second["id"], "table", "Forbidden", {}, [source["id"]])


def test_retained_raster_fork_and_restore_render_actual_files(tmp_path):
    from PIL import Image
    from figloom.jobs import JobManager
    from figloom.engine import _save_raster_snapshot, default_spec
    store = Store(tmp_path / "data")
    project = store.create_project("Imported scientific figure")
    original = store.create_asset(project["id"], "diagram", "Retained figure", {**default_spec("diagram"), "finished_raster_file": "original.png"})
    bundle = store.files_dir / "artifacts" / original["id"] / "imported"
    bundle.mkdir(parents=True)
    Image.new("RGB", (64, 48), "#467a96").save(bundle / "original.png")
    _save_raster_snapshot(bundle, original["spec"], [])
    original.update(status="ready", artifacts=[{"name": path.name, "format": path.suffix[1:]} for path in bundle.iterdir()], _artifact_dir=str(bundle.relative_to(store.data_dir)))
    store.put("asset", original)
    with store.transaction() as db:
        store._snapshot(db, original)
    fork = store.fork_asset(original["id"])
    assert fork["status"] == "stale" and fork["_artifact_dir"] != original["_artifact_dir"]
    assert (store.data_dir / fork["_artifact_dir"] / "original.png").read_bytes() == (bundle / "original.png").read_bytes()
    manager = JobManager(store)
    job = store.enqueue(fork["id"], "render", "single")
    manager.execute(store.claim_job())
    assert store.get("job", job["id"])["status"] == "completed"
    current = store.get("asset", fork["id"])
    with Image.open(store.data_dir / current["_artifact_dir"] / "figure.png") as image:
        assert image.getpixel((0, 0)) == (70, 122, 150)
    assert current["spec"]["finished_raster_file"] == "figure.png"
    restored = store.restore(fork["id"], 1, current["revision"])
    assert restored["spec"]["finished_raster_file"] == "original.png"
    assert (store.data_dir / restored["_artifact_dir"] / "original.png").is_file()
    job = store.enqueue(fork["id"], "render", "single")
    manager.execute(store.claim_job())
    assert store.get("job", job["id"])["status"] == "completed"
    assert (bundle / "original.png").is_file()


def test_actual_installed_package_boundary_allows_sibling_data(tmp_path):
    import importlib.util
    from pathlib import Path
    import figloom.store as module
    installed = tmp_path / "installed" / "figloom"
    installed.mkdir(parents=True)
    copied = installed / "store.py"
    copied.write_text(Path(module.__file__).read_text())
    spec = importlib.util.spec_from_file_location("installed_store_boundary", copied)
    imported = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(imported)
    imported.Store(tmp_path / "installed" / "workspace")
    with pytest.raises(imported.StoreError, match="outside"):
        imported.Store(installed / "data")
    checkout = tmp_path / "checkout"
    package = checkout / "src" / "figloom"
    package.mkdir(parents=True)
    (checkout / "pyproject.toml").write_text('[project]\nname="figloom"\n')
    (package / "store.py").write_text(copied.read_text())
    spec = importlib.util.spec_from_file_location("checkout_store_boundary", package / "store.py")
    imported = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(imported)
    with pytest.raises(imported.StoreError, match="outside"):
        imported.Store(checkout / "var")
