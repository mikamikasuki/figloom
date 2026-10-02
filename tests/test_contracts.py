from fastapi.testclient import TestClient

from figloom.api import create_app


def setup(tmp_path):
    app = create_app(tmp_path / "workspace", start_worker=False)
    return app, TestClient(app, base_url="http://127.0.0.1:8008")


def check_error(response, status, code):
    assert response.status_code == status, response.text
    body = response.json()
    assert body["error"]["code"] == code
    assert body["detail"] == body["error"]["message"]
    assert body["request_id"] == response.headers["x-request-id"]


def test_openapi_only_versioned_operations_and_precise_models(tmp_path):
    app, client = setup(tmp_path)
    with client:
        response = client.get("/api/v1/openapi.json")
        assert response.status_code == 200 and response.headers["content-type"].startswith("application/json")
        schema = response.json()
        assert all(path.startswith("/api/v1/") for path in schema["paths"])
        operations = [operation for methods in schema["paths"].values() for operation in methods.values()]
        ids = [operation["operationId"] for operation in operations]
        assert len(ids) == 35 and len(set(ids)) == 35
        assert {"create_project", "run_asset", "create_chat", "download_artifact", "update_settings"} <= set(ids)
        assert schema["paths"]["/api/v1/assets/{asset_id}/run"]["post"]["responses"]["200"]["content"]["application/json"]["schema"]["$ref"].endswith("/JobDTO")
        assert set(schema["paths"]["/api/v1/projects/{project_id}/export"]["get"]["responses"]["200"]["content"]) == {"application/zip"}
        file_content = schema["paths"]["/api/v1/assets/{asset_id}/files/{name}"]["get"]["responses"]["200"]["content"]
        assert {"image/svg+xml", "image/png", "application/pdf", "application/x-tex", "application/json", "text/html"} <= set(file_content)
        for name, field in (("AssetUpdate", "spec"), ("AssetCreate", "spec"), ("SettingsUpdate", "text"), ("EndpointUpdate", "api_key"), ("ProjectUpdate", "name")):
            prop = schema["components"]["schemas"][name]["properties"][field]
            assert not any(option.get("type") == "null" for option in prop.get("anyOf", []))
            assert prop.get("default", "absent") != None
        assert any(x.get("type") == "null" for x in schema["components"]["schemas"]["ChatCreate"]["properties"]["asset_id"]["anyOf"])
        assert "api_key" not in schema["components"]["schemas"]["EndpointDTO"]["properties"]
        assert client.get("/api/v1/docs").status_code == 200
        assert client.get("/api/v1/redoc").status_code == 200
        assert client.get("/health").json() == client.get("/api/v1/health").json() == {"status": "ok"}


def test_strict_bodies_nullable_scientific_json_and_aliases(tmp_path):
    app, client = setup(tmp_path)
    with client:
        project = client.post("/api/v1/projects", json={"name": "Contract study"}).json()
        assert client.get("/api/projects").json() == client.get("/api/v1/projects").json()
        invalid = client.post("/api/v1/projects", json={"name": 1, "never-echo-private-token": "secret"})
        check_error(invalid, 422, "validation_error")
        assert "secret" not in invalid.text and "never-echo-private-token" not in invalid.text
        assert {"location", "type", "message"} == set(invalid.json()["error"]["details"][0])
        check_error(client.post("/api/projects", json={"name": 1}), 400, "invalid_request")
        check_error(client.post("/api/projects", content=b'{"name":', headers={"content-type": "application/json"}), 422, "validation_error")
        asset = client.post(f'/api/v1/projects/{project["id"]}/assets', json={"kind": "plot", "title": "Editable",
                            "spec": {"x": None, "source_id": None, "scientific_context": {"operator": "Attention", "assumption": None}}}).json()
        assert asset["review"] is None and asset["spec"]["scientific_context"]["assumption"] is None
        check_error(client.patch(f'/api/v1/assets/{asset["id"]}', json={"expected_revision": True}), 422, "validation_error")
        check_error(client.patch(f'/api/v1/assets/{asset["id"]}', json={"expected_revision": 1, "spec": None}), 422, "validation_error")
        updated = client.patch(f'/api/v1/assets/{asset["id"]}', json={"expected_revision": 1, "spec": {"y": "value"}}).json()
        assert updated["spec"] == {"y": "value"}
        history = client.get(f'/api/v1/assets/{asset["id"]}/history').json()
        assert history[1]["spec"]["scientific_context"]["operator"] == "Attention"
        check_error(client.patch(f'/api/v1/projects/{project["id"]}', json={"expected_revision": 2, "name": "Lost edit"}), 409, "revision_conflict")
        chat = client.post(f'/api/v1/projects/{project["id"]}/chat', json={"message": "Discuss the layout", "asset_id": None, "expected_revision": None}).json()
        assert chat["asset_id"] is None and chat["status"] == "queued" and "result" not in chat
        check_error(client.delete(f'/api/v1/projects/{project["id"]}'), 409, "resource_conflict")


def test_response_extensions_keys_and_complete_job_shapes(tmp_path):
    app, client = setup(tmp_path)
    with client:
        project = client.post("/api/v1/projects", json={"name": "Observed data"}).json()
        source = client.post(f'/api/v1/projects/{project["id"]}/sources', files={"file": ("flags.csv", b"group,value,flag\na,1,true\nb,2,false\n")}).json()
        actual = app.state.store.get("source", source["id"])
        actual.update(unique_values={"flag": [True, False, None]}, observation_metadata={"unit": "seed", "notes": None})
        app.state.store.put("source", actual)
        exposed = client.get(f'/api/v1/sources/{source["id"]}').json()
        assert exposed["unique_values"]["flag"] == [True, False, None]
        assert exposed["observation_metadata"] == {"unit": "seed", "notes": None}
        assert "_path" not in exposed
        settings = client.put("/api/v1/settings", json={"text": {"api_key": "confidential", "model": "chosen"}, "budget_usd": 2}).json()
        assert settings["text"]["has_api_key"] and "api_key" not in settings["text"]
        client.put("/api/v1/settings", json={"text": {"api_key": ""}})
        assert app.state.store.settings()["text"]["api_key"] == "confidential"
        check_error(client.put("/api/v1/settings", json={"text": {"api_key": None}}), 422, "validation_error")
        app.state.store.save_settings({"text": {"quality": None, "model_parameters": None}, "image": {"size": None}, "budget_usd": 2})
        assert client.get("/api/v1/settings").json()["text"]["model_parameters"] is None
        chat = client.post(f'/api/v1/projects/{project["id"]}/chat', json={"message": "Discuss results"}).json()
        app.state.store.claim_job()
        app.state.store.finish_chat(chat["id"], "A recorded explanation", None, "")
        completed = client.get(f'/api/v1/jobs/{chat["id"]}').json()
        assert completed["result"]["proposal"] is None and completed["result"]["message"]["asset_id"] is None


def test_uniform_errors_binary_names_and_internal_failure(tmp_path):
    app, client = setup(tmp_path)
    with client:
        check_error(client.get("/api/v1/health", headers={"host": "evil.example"}), 403, "forbidden")
        check_error(client.get("/api/v1/health", headers={"origin": "https://evil.example"}), 403, "forbidden")
        check_error(client.delete("/api/v1/health"), 405, "method_not_allowed")
        check_error(client.get("/api/v1/projects/missing"), 404, "not_found")
        check_error(client.post("/api/v1/projects", json={"name": "Bounded"}, headers={"content-length": "1000001"}), 413, "payload_too_large")
        project = client.post("/api/v1/projects", json={"name": "Real downloads"}).json()
        asset = app.state.store.create_asset(project["id"], "diagram", "Named artifact", {})
        folder = app.state.store.files_dir / "artifacts" / asset["id"] / "actual"
        folder.mkdir(parents=True)
        (folder / "notes#v1.txt").write_text("Actual retained bytes")
        asset.update(status="ready", artifacts=[{"name": "notes#v1.txt", "format": "txt"}], _artifact_dir=str(folder.relative_to(app.state.store.data_dir)))
        app.state.store.put("asset", asset)
        with app.state.store.transaction() as db:
            app.state.store._snapshot(db, asset)
        url = client.get(f'/api/v1/assets/{asset["id"]}').json()["artifacts"][0]["url"]
        assert "/api/v1/" in url and "%23" in url
        assert client.get(url).text == "Actual retained bytes"
        archive = client.get(f'/api/v1/projects/{project["id"]}/export')
        assert archive.status_code == 200 and archive.headers["content-type"] == "application/zip" and archive.content.startswith(b"PK")
        database = app.state.store.db_path
        backup = database.with_suffix(".saved")
        database.rename(backup)
        failure = client.get("/api/v1/projects")
        check_error(failure, 500, "internal_error")
        assert str(tmp_path) not in failure.text
        database.unlink(missing_ok=True)
        backup.rename(database)
