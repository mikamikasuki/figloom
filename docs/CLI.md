# CLI

`figloom serve` runs the local API and worker. Client commands connect to `http://127.0.0.1:8008` by default. Use `--server` or `FIGLOOM_SERVER` to select another loopback server. An optional `/api/v1` suffix is accepted.

```bash
figloom --server http://127.0.0.1:8010 projects list
```

`serve --data-dir` chooses the server's storage directory. Client commands use HTTP and do not open or modify SQLite directly.

## Project and source files

```bash
figloom projects create --name "Attention study" --description "Query-aware selection"
figloom projects list
figloom projects show PROJECT_ID
figloom sources upload PROJECT_ID observations.csv
figloom sources list PROJECT_ID
```

Use the IDs returned by these commands for `PROJECT_ID`, `SOURCE_ID`, `ASSET_ID`, and `JOB_ID` below. Commands return JSON on stdout.

## Specifications and rendering

Save the complete plot, table, or diagram specification in a JSON file. Reference the uploaded source ID in `source_id` when using data.

```bash
figloom assets create PROJECT_ID --kind plot --title "Accuracy" \
  --source SOURCE_ID --spec plot.json
figloom assets show ASSET_ID
figloom assets render ASSET_ID --scope single --wait
```

Run scopes are `single`, `ancestors`, and `affected`. Rendering returns a job ID. `--wait` polls that job until it finishes; `--wait-timeout` sets the deadline in seconds.

Edit the specification locally, then submit it with the current revision from `assets show`:

```bash
figloom assets update ASSET_ID --spec plot.json --expected-revision 3
```

`--spec` replaces the complete specification. `--source` replaces the complete bound source list. A stale revision returns a conflict; reload the current asset and reconcile the local edit before submitting it again.

For local rendering without a running service:

```bash
figloom render --kind plot --spec plot.json \
  --source observations.csv --output rendered
```

## Jobs

```bash
figloom jobs show JOB_ID
figloom jobs wait JOB_ID --wait-timeout 600
figloom jobs cancel JOB_ID
```

Ending a wait with Ctrl-C or a deadline leaves the job running. `jobs wait` reconnects to the existing job without submitting it again. Failed and cancelled jobs return a nonzero exit status.

## Models and settings

```bash
figloom settings show
figloom settings configure --file settings.json
figloom models list --kind text
figloom models test --kind image
figloom models test --kind text --config endpoint.json
```

`settings.json` uses the API's `SettingsUpdate` structure. `endpoint.json` contains endpoint fields such as `base_url`, `api`, and `model`; it supplies temporary overrides for a metadata check and is not saved. See [API settings](API.md#settings-and-files) for key preservation and clearing.

Model commands read provider metadata without submitting generation requests. Keys are excluded from responses. Keep configuration files containing credentials outside the repository.

## Export and import

```bash
figloom assets export ASSET_ID --output figure.zip
figloom projects export PROJECT_ID --output project.zip
figloom projects import project.zip
```

Exports refuse to overwrite an existing file unless `--force` is supplied. Asset export requires a current render; project backups can retain draft and stale revisions. Import creates new IDs and restores source and dependency bindings.

Errors go to stderr as JSON. Exit status `1` indicates a request or runtime failure; `2` indicates invalid input; `3` indicates a failed or cancelled job; `4` indicates an expired wait deadline. Network failures are not automatically retried.
