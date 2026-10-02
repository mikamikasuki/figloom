import io
import json
import zipfile

from fastapi.testclient import TestClient

from figloom.api import create_app


CSV = b"epoch,method,value,seed\n1,A,1.0,1\n1,A,3.0,2\n2,A,2.0,1\n2,A,4.0,2\n1,B,2.0,1\n1,B,4.0,2\n2,B,3.0,1\n2,B,5.0,2\n"


def client_app(tmp_path):
    app = create_app(tmp_path / "data", start_worker=False)
    return TestClient(app, base_url="http://127.0.0.1:8008"), app


def project_source(client):
    project = client.post("/api/projects", json={"name": "Scientific study"}).json()
    uploaded = client.post(f'/api/projects/{project["id"]}/sources', files={"file": ("results.csv", CSV, "text/csv")})
    assert uploaded.status_code == 200, uploaded.text
    return project, uploaded.json()


def asset(client, project, source, kind="plot"):
    response = client.post(f'/api/projects/{project["id"]}/assets', json={"kind": kind, "title": "Source-grounded result",
        "source_ids": [source["id"]], "spec": {"source_id": source["id"], "x": "epoch", "y": "value", "group": "method", "unit_id": "seed", "aggregation": "mean", "interval": "ci95", "chart_type": "line"}})
    assert response.status_code == 200, response.text
    return response.json()


def execute(app):
    claimed = app.state.store.claim_job()
    assert claimed
    app.state.jobs.execute(claimed)
    return app.state.store.get("job", claimed["id"])


def test_actual_http_csv_render_files_sqlite_and_portable_export(tmp_path):
    client, app = client_app(tmp_path)
    with client:
        assert client.get("/health").json() == {"status": "ok"}
        project, source = project_source(client)
        assert source["columns"] == ["epoch", "method", "value", "seed"]
        assert client.get(f'/api/sources/{source["id"]}/file').content == CSV
        current = asset(client, project, source)
        response = client.post(f'/api/assets/{current["id"]}/run', json={"action": "render", "scope": "single"})
        assert response.json()["status"] == "queued"
        completed = execute(app)
        assert completed["status"] == "completed", completed
        current = client.get(f'/api/assets/{current["id"]}').json()
        assert current["status"] == "ready"
        png = next(a for a in current["artifacts"] if a["name"] == "figure.png")
        assert client.get(png["url"]).content.startswith(b"\x89PNG\r\n\x1a\n")
        stats = client.get(f'/api/assets/{current["id"]}/files/statistics.json').json()
        assert [r["estimate"] for r in stats["statistical_results"]["records"]] == [2, 3, 3, 4]
        client.put("/api/settings", json={"text": {"base_url": "https://example.org/v1", "model": "configured", "api_key": "private-key"}, "budget_usd": 1})
        exported = client.get(f'/api/projects/{project["id"]}/export')
        assert exported.status_code == 200
        with zipfile.ZipFile(io.BytesIO(exported.content)) as archive:
            manifest = json.loads(archive.read("manifest.json"))
            assert b"private-key" not in archive.read("manifest.json")
            assert any(name.endswith("figure.svg") for name in archive.namelist())
            assert len(manifest["assets"][0]["history"]) >= 2
        imported = client.post("/api/projects/import", files={"file": ("backup.zip", exported.content, "application/zip")})
        assert imported.status_code == 200, imported.text
        imported_state = client.get(f'/api/projects/{imported.json()["id"]}').json()
        assert imported_state["assets"][0]["id"] != current["id"]
        assert imported_state["assets"][0]["spec"]["source_id"] == imported_state["sources"][0]["id"]
        assert client.get(imported_state["assets"][0]["artifacts"][0]["url"]).status_code == 200
    reopened = create_app(tmp_path / "data", start_worker=False)
    assert reopened.state.store.get("asset", current["id"])["status"] == "ready"


def test_pipeline_table_replacement_cancellation_and_stale_output(tmp_path):
    client, app = client_app(tmp_path)
    with client:
        project, source = project_source(client)
        parent, child = asset(client, project, source), asset(client, project, source, "table")
        assert client.post(f'/api/projects/{project["id"]}/edges', json={"source": parent["id"], "target": child["id"]}).status_code == 200
        queued = client.post(f'/api/assets/{child["id"]}/run', json={"scope": "ancestors", "action": "render"}).json()
        completed = execute(app)
        assert completed["status"] == "completed", completed
        table = client.get(f'/api/assets/{child["id"]}').json()
        assert any(a["format"] == "tex" for a in table["artifacts"])
        html = next(a for a in table["artifacts"] if a["name"] == "table.html")
        downloaded = client.get(html["url"])
        assert downloaded.status_code == 200
        assert downloaded.headers["content-type"].startswith("text/html")
        assert "<table" in downloaded.text
        assert "sandbox" in downloaded.headers["content-security-policy"]
        pending = client.post(f'/api/assets/{parent["id"]}/run', json={"action": "render"}).json()
        app.state.store.claim_job()
        replaced = client.put(f'/api/sources/{source["id"]}', data={"expected_revision": source["revision"]}, files={"file": ("results.csv", CSV.replace(b"1.0", b"10.0"), "text/csv")})
        assert replaced.status_code == 200, replaced.text
        assert replaced.json()["revision"] == 2
        assert client.get(f'/api/jobs/{pending["id"]}').json()["status"] == "failed"
        assert client.get(f'/api/assets/{child["id"]}').json()["status"] == "stale"
        cancelled = client.post(f'/api/assets/{parent["id"]}/run', json={}).json()
        assert client.post(f'/api/jobs/{cancelled["id"]}/cancel').json()["status"] == "cancelled"
        assert app.state.store.claim_job() is None
        conflict = client.patch(f'/api/assets/{parent["id"]}', json={"title": "Stale overwrite", "expected_revision": 1})
        assert conflict.status_code == 409


def test_host_origin_file_traversal_and_no_fake_ai(tmp_path):
    client, app = client_app(tmp_path)
    with client:
        assert client.get("/health", headers={"host": "attacker.example"}).status_code == 403
        assert client.post("/api/projects", json={"name": "Blocked"}, headers={"origin": "https://attacker.example"}).status_code == 403
        project, source = project_source(client)
        current = asset(client, project, source)
        job = client.post(f'/api/projects/{project["id"]}/chat', json={"message": "Improve this caption", "asset_id": current["id"], "expected_revision": current["revision"]}).json()
        failed = execute(app)
        assert failed["status"] == "failed" and "Configure" in failed["error"]
        state = client.get(f'/api/projects/{project["id"]}').json()
        assert all(m["role"] == "user" for m in state["messages"])
        assert not state["proposals"]
        bad = io.BytesIO()
        with zipfile.ZipFile(bad, "w") as archive:
            archive.writestr("../outside.txt", "bad")
        assert client.post("/api/projects/import", files={"file": ("bad.zip", bad.getvalue())}).status_code == 400
        assert client.get(f'/api/assets/{current["id"]}/files/studio.sqlite3').status_code == 404
        settings = client.put("/api/settings", json={"text": {"base_url": "https://example.org/v1", "model": "configured", "api_key": "secret"}, "budget_usd": 10}).json()
        assert settings["text"]["has_api_key"] and "api_key" not in settings["text"]
        assert {"spent", "reserved", "available"} <= set(settings)


def test_actual_review_retains_artifacts_and_backup_keeps_source_history(tmp_path):
    client, app = client_app(tmp_path)
    with client:
        project, source = project_source(client)
        current = asset(client, project, source)
        client.post(f'/api/assets/{current["id"]}/run', json={})
        assert execute(app)["status"] == "completed"
        rendered = client.get(f'/api/assets/{current["id"]}').json()
        before = client.get(f'/api/assets/{current["id"]}/files/figure.png').content
        client.post(f'/api/assets/{current["id"]}/run', json={"action": "review"})
        reviewed = execute(app)
        assert reviewed["status"] == "completed", reviewed
        assert client.get(f'/api/assets/{current["id"]}/files/figure.png').content == before
        assert client.get(f'/api/assets/{current["id"]}/files/review.json').json()["model_review_status"] == "not_requested"
        client.put(f'/api/sources/{source["id"]}', data={"expected_revision": 1}, files={"file": ("revised.csv", CSV.replace(b"1.0", b"10.0"))})
        assert client.get(f'/api/assets/{current["id"]}/export').status_code == 409
        backup = client.get(f'/api/projects/{project["id"]}/export').content
        restored = client.post("/api/projects/import", files={"file": ("backup.zip", backup)}).json()
        new_source = app.state.store.project_state(restored["id"])["sources"][0]
        assert new_source["revision"] == 2 and len(new_source["_versions"]) == 1
        client.delete(f'/api/sources/{source["id"]}')
        backup = client.get(f'/api/projects/{project["id"]}/export')
        assert backup.status_code == 200, backup.text
        imported = client.post("/api/projects/import", files={"file": ("deleted-source.zip", backup.content)})
        assert imported.status_code == 200, imported.text
        state = client.get(f'/api/projects/{imported.json()["id"]}').json()
        assert not state["sources"] and state["assets"][0]["status"] == "stale"


def test_explicit_key_clear_preserve_and_strict_origins(tmp_path):
    client, app = client_app(tmp_path)
    with client:
        client.put("/api/settings", json={"text": {"api_key": "secret"}})
        assert client.put("/api/settings", json={"text": {"api_key": ""}}).json()["text"]["has_api_key"]
        assert not client.put("/api/settings", json={"text": {"clear_api_key": True}}).json()["text"]["has_api_key"]
        assert "clear_api_key" not in app.state.store.settings()["text"]
        assert client.get("/health", headers={"origin": "http://127.0.0.1:8008"}).status_code == 200
        assert client.get("/health", headers={"origin": "http://127.0.0.1:9000"}).status_code == 403


def test_promotion_changes_preview_url_even_at_same_spec_revision(tmp_path):
    client, app = client_app(tmp_path)
    with client:
        project, source = project_source(client)
        current = asset(client, project, source)
        client.post(f'/api/assets/{current["id"]}/run', json={})
        assert execute(app)["status"] == "completed"
        first = client.get(f'/api/assets/{current["id"]}').json()
        url1 = next(a["url"] for a in first["artifacts"] if a["name"] == "figure.png")
        client.post(f'/api/assets/{current["id"]}/run', json={})
        assert execute(app)["status"] == "completed"
        second = client.get(f'/api/assets/{current["id"]}').json()
        url2 = next(a["url"] for a in second["artifacts"] if a["name"] == "figure.png")
        assert first["revision"] == second["revision"] and url1 != url2
        assert client.get(url2).content.startswith(b"\x89PNG")


def test_actual_paired_csv_blocks_undefined_and_wrong_direction_claims(tmp_path):
    import pytest
    from figloom.ai import _proposal
    from figloom.engine import _statistics
    from figloom.store import StoreError
    path = tmp_path / "paired.csv"
    def computed(deltas, direction="higher"):
        rows = ["method,value,unit"]
        for i, delta in enumerate(deltas):
            rows.extend([f"baseline,{10 + i},{i}", f"candidate,{10 + i + delta},{i}"])
        path.write_text("\n".join(rows) + "\n")
        spec = {"y": "value", "group": "method", "unit_id": "unit", "aggregation": "mean", "interval": "ci95",
                "baseline": "baseline", "candidate": "candidate", "direction": direction, "paired": True}
        result, _, _ = _statistics(spec, [{"id": "source", "name": "paired.csv", "path": str(path)}])
        return {"kind": "plot", "spec": spec, "source_ids": ["source"], "caption": ""}, {"statistics.json": result}
    def proposed(caption):
        return {"answer": "Describe the source-bound paired comparison.", "summary": "Update the comparison caption", "changes": {"caption": caption}}
    asset, values = computed([1, 1, 1])
    record = values["statistics.json"]["statistical_results"]["comparisons"][0]
    assert record["p_value"] is None and record["test_status"] == "undefined_zero_observed_variance"
    with pytest.raises(StoreError, match="finite computed statistical test"):
        _proposal(proposed("The candidate significantly improves quality (alpha=0.05)."), asset, [], values)
    asset, values = computed([1, 1.1, .9, 1.05, .95, 1.2])
    assert _proposal(proposed("The candidate significantly improves quality (alpha=0.05)."), asset, [], values)[1]
    with pytest.raises(StoreError, match="explicit valid alpha"):
        _proposal(proposed("The candidate significantly improves quality."), asset, [], values)
    asset, values = computed([1, 1.1, .9, 1.05, .95, 1.2], direction="lower")
    with pytest.raises(StoreError, match="contrary"):
        _proposal(proposed("The candidate significantly improves quality (alpha=0.05)."), asset, [], values)
