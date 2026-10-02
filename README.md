<h1 align="center">Figloom</h1>

<p align="center">
  Editable scientific diagrams, statistical plots, and tables.
</p>

<p align="center">
  <a href="LICENSE"><img alt="License" src="https://img.shields.io/badge/license-Apache%202.0-blue"></a>
  <img alt="Python" src="https://img.shields.io/badge/python-3.11%2B-green">
  <a href="https://github.com/mikamikasuki/figloom/actions/workflows/ci.yml"><img alt="CI" src="https://github.com/mikamikasuki/figloom/actions/workflows/ci.yml/badge.svg"></a>
</p>

Figloom is a local workbench for creating scientific figures from manuscripts and experimental data. It keeps source material, editable specifications, rendered outputs, and revision history together, so changes to a figure remain inspectable and reproducible.

Use the Dashboard to compose and compare figures, the CLI to manage rendering jobs, or the HTTP API to build a custom interface.
## How It Works
<img width="1672" height="941" alt="image" src="https://github.com/user-attachments/assets/a72ab72e-0311-418b-85ef-caffe76d516f" />

Diagram generation combines source context, composition, rendering, and review.  Statistical figures use deterministic computations from the supplied records.  Both retain editable specifications and revision history.


## Features

- **Scientific diagrams:** develop mechanism and architecture diagrams from manuscript context, equations, method descriptions, and existing sketches. Edit their scene specifications and render them.
- **Statistical plots and tables:** map experimental records to comparison groups, conditions, and independent replicates. Configure aggregation, uncertainty, units, precision, and presentation.
- **Chat editing:** request changes to a figure or caption, inspect the proposed fields, and apply the revision.
- **Revision history:** restore or fork saved versions, connect figure dependencies, and rerun the affected path when an input changes.
- **Reproducible exports:** retain source data, editable specifications, captions, and plotting code alongside rendered files. Export and import complete project backups.
- **Dashboard, CLI, and API:** work with the same projects and rendering engine through three interfaces.

## Quick Start

**Requirements:** Python 3.11+. Installing the Dashboard from source also requires Node.js 22+ and npm.

**Platform support:** macOS and Linux. On Windows, use [WSL 2](https://learn.microsoft.com/windows/wsl/install) and run all commands below inside the WSL terminal.

### Dashboard

```bash
git clone https://github.com/mikamikasuki/figloom.git
cd figloom

python3.11 scripts/install.py
.venv/bin/figloom serve --open
```

Open [localhost:8008](http://127.0.0.1:8008).

1. Create a project and upload your manuscript, method notes, or experiment data.
2. Add a diagram, plot, or table.
3. Map the data fields or edit the diagram specification, then render.
4. Configure model providers in **Settings** to enable chat editing, generation, and visual review.

Rendering an existing specification from local data does not require a model API.

### CLI / Headless

Install the API and rendering engine without Node.js or the Dashboard build:

```bash
git clone https://github.com/mikamikasuki/figloom.git
cd figloom

python3.11 scripts/install.py --headless
.venv/bin/figloom serve
```

Keep the service running. In another terminal, run commands from the repository root:

```bash
.venv/bin/figloom --help
.venv/bin/figloom projects create --name "Attention study"
.venv/bin/figloom projects list
.venv/bin/figloom settings show
```

See the [CLI reference](docs/CLI.md) for source uploads, specification editing, rendering, job control, and export.

## Usage

### Diagrams

Attach the material that defines the figure: manuscript passages, model components, equations, architecture descriptions, or a reference sketch. Describe the mechanism and what the figure should explain.

Figloom builds a scientific graph and an editable composition, then checks the rendered result.

### Statistical Plots and Tables

Upload CSV, TSV, or JSON records and configure:

| Field | Purpose |
|---|---|
| X / category | Checkpoint, category, or independent variable |
| Y / value | Numeric measurement |
| Series / group | Method or comparison group |
| Facet | Dataset or experimental condition |
| Independent unit | Replicate identifier |
| Aggregation | Mean or unaggregated observations |
| Uncertainty | SD, SE, 95% confidence interval, or none |

Set axis labels, units, metric direction, and numerical precision. Tables and curves use the supplied observations and retain their comparison groups and experimental conditions.

### Editing and History

Edit specifications directly or request changes through chat. Review the proposed fields before applying them, then render the new revision.

Use **History** to restore an earlier version or fork a separate design. The workflow graph records dependencies between assets; editing an upstream input marks dependent outputs stale.

See the [workflow guide](docs/WORKFLOW.md) for composition, statistical mapping, captions, and revision management.

### Export

| Asset | Outputs |
|---|---|
| Statistical plots | PDF, SVG, PNG, plotting code, data, and specification |
| Tables | LaTeX source, tabular data, and printable previews |
| Native diagrams | Rendered files and editable scene specifications |
| Projects | Backup containing source files, asset revisions, and dependencies |

LaTeX table PDF compilation requires `tectonic` or `pdflatex`.

## Configuration

Open **Settings** to configure providers and spending.

| Setting | Purpose |
|---|---|
| Text endpoint and model | Chat editing, scientific generation, captions, and review |
| Image endpoint and model | Image generation |
| API key | Authentication for the selected endpoint |
| Text pricing | Input and output prices in USD per million tokens |
| Maximum USD / request | Cost reservation for an image request |
| Total budget · USD | Available spending for model requests |

Text providers support OpenAI-compatible Chat Completions, Responses, and Ollama. Visual review requires a text model that accepts images.

Use **Load models** to read the provider’s model list and **Check connection** to inspect endpoint availability. Model IDs can also be entered manually. These checks do not submit generation requests.

For remote text requests, configure token pricing and output limits. For image requests, configure the maximum cost per request. Figloom reserves cost against the remaining budget before submitting a request and records usage afterward.

### Workspace Storage

The default data directory is:

```text
~/.local/share/figloom
```

To use a separate workspace:

```bash
.venv/bin/figloom serve --data-dir ./var
```

To run on another port:

```bash
.venv/bin/figloom serve --port 8010
```

CLI clients can connect to that server:

```bash
.venv/bin/figloom --server http://127.0.0.1:8010 projects list
```

Project backups exclude provider credentials and global settings.

## HTTP API

The versioned API separates the Dashboard from project storage, rendering jobs, provider configuration, and asset revisions.

- [API reference](docs/API.md)
- [OpenAPI schema](docs/openapi.json)
- [Typed TypeScript client](apps/web/src/client.ts)
- [Generated API types](apps/web/src/generated/api-schema.ts)

Interactive documentation is available at [localhost:8008/api/v1/docs](http://127.0.0.1:8008/api/v1/docs) while the server is running.


## Project Structure

```text
figloom/
├── apps/web/                 Dashboard and TypeScript API client
├── src/figloom/
│   ├── api.py                Versioned HTTP API
│   ├── cli.py                Command-line interface
│   ├── store.py              Project storage and revisions
│   ├── jobs.py               Background jobs
│   ├── engine.py             Rendering and generation orchestration
│   ├── ai.py                 Chat and edit proposals
│   ├── budget.py             Model cost reservations
│   ├── providers/            Model transports and connection checks
│   └── scientific/           Composition, statistics, tables, and review
├── docs/                     Workflow, CLI, and API documentation
├── scripts/                  Installation, API synchronization, and packaging
└── tests/                    Backend tests
```

## Development

Install development dependencies:

```bash
python3.11 scripts/install.py --dev
```

Run backend tests and check the API contract:

```bash
.venv/bin/python -m pytest -q -p no:cacheprovider
.venv/bin/python scripts/sync_api.py --check
```

Run frontend tests and compilation:

```bash
npm --prefix apps/web test
npm --prefix apps/web run build
```

For frontend development, start the API:

```bash
.venv/bin/figloom serve
```

Then start the frontend in another terminal:

```bash
npm --prefix apps/web run dev
```

Build the Python distribution with the Dashboard included:

```bash
.venv/bin/python scripts/build_release.py
```

## Troubleshooting

Check installed dependencies, Dashboard availability, LaTeX tools, and the default storage path:

```bash
.venv/bin/figloom doctor
```

| Problem | Action |
|---|---|
| Dashboard is unavailable | Run the installer without `--headless`; check Node.js and npm |
| CLI cannot reach the service | Start `figloom serve` and verify the client’s `--server` address |
| Model list is unavailable | Check the endpoint and key; enter the model ID manually if the provider has no listing endpoint |
| Generation is blocked by the budget | Configure pricing or image request cost, then check the remaining budget |
| LaTeX table PDF is missing | Install `tectonic` or `pdflatex`; LaTeX source remains exportable |

## Contributing

Open an issue with the expected behavior, actual behavior, and steps to reproduce.

For changes to rendering, statistics, or API behavior, include tests covering the change. Regenerate the OpenAPI schema and TypeScript types when modifying API contracts:

```bash
.venv/bin/python scripts/sync_api.py
```


## License

Apache 2.0. See [LICENSE](LICENSE) and [NOTICE](NOTICE).
