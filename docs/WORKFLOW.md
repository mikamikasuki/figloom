# Workflow

## Sources and projects

Keep one manuscript and its supporting visuals in a project. Upload PDFs or text sources for scientific context, and CSV, TSV, or JSON records for statistical analysis. Sources retain their original files. Inspect the fields and sample rows before assigning a numeric measurement column.

Describe the scientific claim, model components, equations, and figure purpose in the project description and diagram brief. AI editing receives the selected asset, its current specification, and the project's source context. Uploaded text is source material; it cannot grant permission to run code or change provider settings.

## Statistical mapping

Each row should represent a recorded observation. Map:

| Field | Purpose |
| --- | --- |
| `source_id` | The uploaded data file |
| `y` | Numeric measurement |
| `x` | Checkpoint or independent variable |
| `group` | Method or comparison series |
| `facet` | Dataset or experimental condition |
| `unit_id` | Independent replicate identifier when rows contain repeated measurements |
| `aggregation` | Mean within each mapped cell, or unaggregated observations |
| `interval` | SD, SE, 95% confidence interval, or none |
| `direction` | Whether higher or lower values are preferable |

Use independent experimental units for uncertainty. The engine reports group counts and computes intervals from the supplied observations; choosing a confidence interval does not create independent replications. Missing, nonfinite, or incompatible values produce an error rather than an invented result.

Set the axis names and units explicitly. A change in field mapping or statistical definition is an analysis change; a change in color or typography is a presentation change. Both create a new editable revision and invalidate the old render.

Tables and curves retain full comparator and facet coverage. Table values use one precision policy and include the statistical definition. Exported plotting code reads the exported data and specification.

## Diagrams

Supply the actual mechanism and the argumentative purpose in the brief. Attach relevant manuscript pages, equations, architecture descriptions, or existing sketches. Generation builds a scientific graph, then an editable scene and composition. Native objects, labels, connections, panels, and their bounds remain in the scene specification.

Generation checks scientific coverage and rendered readability, then attempts bounded repair. If the composition needs further refinement and an image provider is configured, the final pass supplies the rendered candidate, scientific description, and review feedback to the image model. It generates five candidates and uses visual reviews to choose an initial output. All candidates remain available beside the preview. Click a thumbnail to inspect it; apply the candidate to change the saved figure.

The final raster image is exported as a whole PNG and embedded PDF. Numeric data plots and statistical tables stay on the deterministic rendering path.

## Chat and edits

Select the asset before asking for a change. Requests such as “make the labels readable at single-column width,” “compare the methods with a 95% interval,” or “move the mechanism detail below the overview” are grounded in the current specification.

AI returns an answer and an edit proposal. Review the changed fields and apply the proposal, or choose **Apply & render** to produce the updated output. An edit prepared against an older revision cannot overwrite a newer version.

Direct specification and caption edits use the same revision system. Restore a previous revision to undo a change, or fork the asset to compare a different design. Forking a project creates a separate working path with its source files and dependency relationships.

## Paths and execution

An edge records that one asset depends on another. The workflow rejects cycles. Run a single asset, its ancestors, or its affected descendants in dependency order. Editing or removing an upstream input marks downstream outputs stale.

Rendering, generation, and review are actual background jobs. The job inspector displays the current stage and any actionable error. Cancellation stops further stages and provider requests; an in-flight external request can finish before the process observes cancellation. Interrupted jobs are recorded as interrupted rather than reported as completed.

## Captions and scientific interpretation

Explain the visual's strongest supported point and its experimental condition. Keep the prose focused on the scientific argument: the problem, the mechanism or comparison, and the evidence. Numeric statements must come from the recorded observations. A visual design review does not establish statistical significance or scientific validity.

## Export and recovery

Asset exports contain the selected render, editable specification, source observations, source code where applicable, and caption. Project backups retain owned source files, asset revisions, and dependencies. Provider credentials and global settings are excluded.

Import a project backup into the same application on another machine. The imported assets receive fresh IDs, with their source and dependency references remapped. Original data and specifications remain editable.
