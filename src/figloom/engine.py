"""Local scientific assets from uploaded evidence and configured model providers.

Rendering is deterministic. Scientific records are recomputed from the current
editable specification; model proposals never supply observations or statistics.
"""
from __future__ import annotations

from collections import defaultdict
from copy import deepcopy
import csv
import html
import json
import math
from pathlib import Path
import re
import shutil
from tempfile import TemporaryDirectory
import zipfile

import numpy as np
import pandas as pd
from PIL import Image
from scipy.stats import t as student_t, ttest_1samp

from figloom.providers.client import ModelClient, ProviderError
from figloom.scientific.render import figure_dimensions, render_figure

MAX_SOURCE_BYTES = 32 * 1024 * 1024
MAX_SOURCE_ROWS = 250_000
PALETTE = ['#0072B2', '#D55E00', '#009E73', '#CC79A7']


def default_spec(kind):
    if kind == 'diagram':
        return {'brief': '', 'scene': None, 'graph': None, 'composition': None,
                'width': 'wide', 'height': 4.4, 'font_size': 9,
                'narrative_mode': 'scientific_story', 'native_attempts': 3,
                'raster_finish_candidate_count': 5}
    if kind not in ('plot', 'table'):
        raise ValueError('Asset kind must be diagram, plot or table')
    result = {'source_id': None, 'x': None, 'y': None, 'group': None, 'facet': None,
              'aggregation': 'mean', 'interval': 'none', 'chart_type': 'line',
              'width': 'wide', 'xlabel': '', 'ylabel': '', 'palette': PALETTE.copy(),
              'precision': 3, 'unit_id': None, 'unit': '', 'direction': 'none', 'baseline': None, 'candidate': None}
    if kind == 'table':
        result['columns'] = []
    return result


def _readable_file(path):
    path = Path(path)
    if not path.is_file() or path.stat().st_size > MAX_SOURCE_BYTES:
        raise ValueError('Source must be an actual file no larger than 32 MiB')
    return path


def _clean_scalar(value):
    if value is None or value is pd.NA:
        return None
    if isinstance(value, np.generic):
        value = value.item()
    if isinstance(value, float) and not math.isfinite(value):
        return None
    return value


def _read_frame(path):
    path = _readable_file(path)
    suffix = path.suffix.lower()
    if suffix in ('.csv', '.tsv'):
        delimiter = '\t' if suffix == '.tsv' else ','
        with path.open(newline='', encoding='utf-8-sig') as stream:
            header = next(csv.reader(stream, delimiter=delimiter), [])
        if not header or len(set(header)) != len(header) or any(not name.strip() for name in header):
            raise ValueError('CSV/TSV columns require distinct nonempty names')
        frame = pd.read_csv(path, sep=delimiter, nrows=MAX_SOURCE_ROWS + 1)
    elif suffix == '.json':
        value = json.loads(path.read_text(encoding='utf-8'))
        if isinstance(value, dict):
            value = next((value[key] for key in ('records', 'rows', 'data') if isinstance(value.get(key), list)), None)
        if not isinstance(value, list) or not value or any(not isinstance(row, dict) for row in value):
            raise ValueError('Tabular JSON must contain a nonempty array of record objects')
        frame = pd.DataFrame(value)
    else:
        raise ValueError('Choose an uploaded CSV, TSV or JSON record source')
    if not len(frame) or len(frame) > MAX_SOURCE_ROWS or len(frame.columns) > 256:
        raise ValueError('Tabular sources require 1–250000 rows and at most 256 columns')
    if len(set(frame.columns)) != len(frame.columns):
        raise ValueError('Tabular source columns must have distinct names')
    frame.columns = [str(column) for column in frame.columns]
    return frame


def inspect_source(path):
    path = _readable_file(path)
    suffix = path.suffix.lower()
    if suffix in ('.csv', '.tsv', '.json'):
        try:
            frame = _read_frame(path)
        except ValueError:
            if suffix != '.json':
                raise
        else:
            return {'kind': 'data', 'columns': list(frame.columns),
                    'column_types': {name: str(frame[name].dtype) for name in frame.columns},
                    'preview': [{key: _clean_scalar(value) for key, value in row.items()}
                                for row in frame.head(20).to_dict(orient='records')],
                    'row_count': len(frame), 'text': '',
                    'unique_values': {name: [_clean_scalar(value) for value in frame[name].drop_duplicates().iloc[:256]] for name in frame.columns},
                    'unique_value_counts': {name: int(frame[name].nunique(dropna=False)) for name in frame.columns},
                    'unique_values_truncated': {name: bool(frame[name].nunique(dropna=False) > 256) for name in frame.columns}}
    if suffix == '.pdf':
        from pypdf import PdfReader
        reader = PdfReader(path)
        if len(reader.pages) > 1000:
            raise ValueError('PDF source exceeds 1000 pages')
        text = '\n\n'.join(page.extract_text() or '' for page in reader.pages)
        return {'kind': 'document', 'text': text, 'page_count': len(reader.pages)}
    if suffix in ('.png', '.jpg', '.jpeg'):
        with Image.open(path) as image:
            image.verify()
        with Image.open(path) as image:
            return {'kind': 'image', 'text': '', 'width': image.width, 'height': image.height,
                    'image_format': image.format}
    if suffix in ('.txt', '.md', '.tex', '.json', '.svg'):
        text = path.read_text(encoding='utf-8')
        if suffix == '.json':
            json.loads(text)
        if suffix == '.svg':
            from xml.etree import ElementTree
            if '<!DOCTYPE' in text.upper() or '<!ENTITY' in text.upper():
                raise ValueError('SVG sources cannot declare document types or XML entities')
            root = ElementTree.fromstring(text)
            if not root.tag.endswith('svg'):
                raise ValueError('SVG source must contain an SVG root')
        return {'kind': 'image' if suffix == '.svg' else 'document', 'text': text}
    raise ValueError('Supported sources are PDF, Markdown, text, TeX, CSV, TSV, JSON, PNG, JPEG and SVG')


def make_client(settings, *, guard=None):
    """Use explicit text/image endpoints; accounting is owned by the API caller."""
    if not isinstance(settings, dict):
        raise ValueError('Provider settings must be an object')
    text = settings.get('text') or {key: settings.get(key) for key in
           ('base_url', 'model', 'api_key', 'api', 'input_price_per_million', 'output_price_per_million')}
    if not isinstance(text, dict) or not text.get('base_url') or not text.get('model'):
        raise ProviderError('Configure a text model endpoint and model in Settings', code='configuration')
    if guard is None:
        raise ProviderError('Model requests require an explicit budget and cancellation guard', code='configuration')
    config = deepcopy(text.get('config') or {})
    for key in ('api', 'max_output_tokens', 'context_length', 'temperature', 'model_parameters', 'timeout', 'json_mode'):
        if key in text:
            config[key] = deepcopy(text[key])
    config.setdefault('max_output_tokens', 8192)
    config.setdefault('max_retries', 0)
    config['pricing'] = {name: text[source] for name, source in
        (('input_per_million', 'input_price_per_million'), ('output_per_million', 'output_price_per_million'),
         ('cached_input_per_million', 'cached_input_price_per_million')) if text.get(source) is not None}
    api = text.get('api', config.get('api', 'chat_completions'))
    provider = {'id': 'text', 'kind': 'ollama' if api == 'ollama' else 'openai_compatible',
                'base_url': text['base_url'], 'model': text['model'], 'allow_paid': True, 'config': config}
    client = ModelClient(provider, key=text.get('api_key', ''), allow_paid=True, request_guard=guard)
    from .providers.client import effective_image_config
    image = effective_image_config(settings)
    if image.get('model'):
        image_options = {key: deepcopy(value) for key, value in image.items() if key in
                         {'model', 'max_request_usd', 'size', 'quality', 'background', 'style', 'moderation',
                          'output_format', 'response_format', 'timeout'} and (key in {'model', 'max_request_usd'} or value not in (None, ''))}
        image_config = {'api': 'chat_completions', 'image_generation': image_options, 'max_retries': 0}
        image_provider = {'id': 'image', 'kind': 'openai_compatible', 'allow_paid': True,
                          'base_url': image.get('base_url') or text['base_url'], 'model': image['model'], 'config': image_config}
        client.image_client = ModelClient(image_provider, key=image.get('api_key', ''),
                                         allow_paid=True, request_guard=guard)
        client.config['image_generation'] = image_options
    return client


def _sources_list(sources):
    values = list(sources.values()) if isinstance(sources, dict) else list(sources)
    if any(not isinstance(value, dict) or not value.get('path') for value in values):
        raise ValueError('Sources must contain actual uploaded file paths and metadata')
    return values


def _mapped_source(spec, sources):
    identifier = spec.get('source_id')
    matches = [source for source in sources if source.get('id') == identifier] if identifier else [
        source for source in sources if Path(source['path']).suffix.lower() in ('.csv', '.tsv', '.json')]
    if len(matches) != 1:
        raise ValueError('Choose one actual tabular source in source_id')
    return matches[0], _read_frame(matches[0]['path'])


def _statistics(spec, sources):
    source, frame = _mapped_source(spec, sources)
    fields = {key: spec.get(key) for key in ('x', 'y', 'group', 'facet', 'unit_id')}
    if not fields['y']:
        raise ValueError('Map y to an uploaded numerical column')
    for key, name in fields.items():
        if name is not None and name not in frame:
            raise ValueError(f'{key} refers to an absent uploaded column: {name}')
    aggregation, interval = spec.get('aggregation', 'mean'), spec.get('interval', 'none')
    if aggregation not in ('mean', 'none') or interval not in ('none', 'sd', 'se', 'ci95'):
        raise ValueError('Choose mean/none aggregation and none/sd/se/ci95 uncertainty')
    if aggregation == 'none' and interval != 'none':
        raise ValueError('Raw observations do not have estimated uncertainty; select mean aggregation')
    precision = spec.get('precision', 3)
    if isinstance(precision, bool) or not isinstance(precision, int) or not 0 <= precision <= 12:
        raise ValueError('Precision must be an integer between zero and twelve')
    values = pd.to_numeric(frame[fields['y']], errors='raise')
    if any(isinstance(value, (bool, np.bool_)) for value in frame[fields['y']]) or not np.isfinite(values.to_numpy(dtype=float)).all():
        raise ValueError('Mapped observations must all be finite numerical values; correct missing values explicitly')
    rows = frame.to_dict(orient='records')
    buckets = defaultdict(list)
    for index, row in enumerate(rows):
        key = tuple(_clean_scalar(row[fields[field]]) if fields[field] else None for field in ('x', 'group', 'facet'))
        if any(value is None and fields[field] for value, field in zip(key, ('x', 'group', 'facet'))):
            raise ValueError('Mapped group, facet and x identities must not contain missing values')
        if any(isinstance(value, (dict, list)) for value in key):
            raise ValueError('Mapped identities must be scalar values')
        buckets[key if aggregation == 'mean' else (*key, index)].append((index, float(values.iloc[index]), row))
    records, bindings = [], []
    for index, (key, observations) in enumerate(buckets.items()):
        x, group, facet = key[:3]
        sample = [item[1] for item in observations]
        if fields['unit_id'] and aggregation == 'mean':
            units = defaultdict(list)
            for _, value, row in observations:
                unit = _clean_scalar(row[fields['unit_id']])
                if unit is None or isinstance(unit, (dict, list)):
                    raise ValueError('Every repeated observation needs an actual scalar unit identity')
                units[unit].append(value)
            sample = [float(np.mean(unit_values)) for unit_values in units.values()]
        n = len(sample)
        if interval != 'none' and n < 2:
            raise ValueError('SD, SE and Student-t intervals need at least two observed units in every mapped group')
        estimate = float(np.mean(sample))
        record = {'id': f'record{index + 1}', 'dataset': str(facet) if facet is not None else source.get('name', 'Uploaded data'),
                  'method': str(group) if group is not None else fields['y'], 'metric': fields['y'], 'x': x,
                  'condition': None, 'estimate': estimate, 'n_units': n, 'unit': spec.get('unit', ''),
                  'precision': precision, 'direction': spec.get('direction', 'none'), 'refs': {},
                  'source_id': source.get('id'), 'source_rows': [item[0] + 1 for item in observations],
                  'uncertainty': {'type': {'ci95': 't_ci'}.get(interval, interval),
                                  'unit': fields['unit_id'] or 'uploaded rows',
                                  'scope': 'Within-unit means' if fields['unit_id'] else 'Uploaded rows; independence is not inferred'}}
        if interval in ('sd', 'se', 'ci95'):
            sd = float(np.std(sample, ddof=1)); se = sd / math.sqrt(n)
            if interval == 'sd':
                record['sd'] = sd
            elif interval == 'se':
                record['se'] = se
            else:
                radius = float(student_t.ppf(.975, n - 1)) * se
                record.update(ci_low=estimate - radius, ci_high=estimate + radius)
                record['uncertainty']['confidence'] = .95
        for field in ('estimate', 'n_units', 'sd', 'se', 'ci_low', 'ci_high'):
            if field not in record:
                continue
            ref = record['id'] + ':' + field
            record['refs'][field] = ref
            bindings.append({'id': ref, 'value': record[field], 'precision': precision,
                             'statistical_identity': {name: record.get(name) for name in
                              ('dataset', 'method', 'metric', 'condition', 'x')},
                             'source_id': source.get('id'), 'source_rows': record['source_rows']})
        records.append(record)
    result = {'records': records, 'comparisons': [],
              'assumptions': {'student_t': 'Independent sampling units and an approximately normal sampling distribution; unit IDs provide grouping and do not establish independence'},
              'coverage': {'complete': True, 'scope': 'Only supplied mapped observations', 'source_id': source.get('id'),
                           'input_rows': len(frame), 'mapped_rows': len(frame), 'observed_groups': len(records)},
              'axes': {'x': {'name': fields['x'], 'label': spec.get('xlabel') or fields['x'] or ''}}}
    if spec.get('baseline') is not None or spec.get('candidate') is not None:
        result['comparisons'] = _paired_comparisons(spec, fields, rows, source, bindings)
    return {'statistical_results': result, 'metric_bindings': bindings}, source, frame


def _style(spec):
    style = deepcopy(spec.get('style') or {})
    for field in ('width', 'height', 'font_size', 'palette', 'precision', 'xlabel', 'ylabel', 'unit', 'direction',
                  'rotation', 'legend_columns', 'xscale', 'cmap'):
        if spec.get(field) not in (None, ''):
            style[field] = deepcopy(spec[field])
    return style


def _dump(path, value):
    Path(path).write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False), encoding='utf-8')


def _context(asset, spec, sources):
    documents, references = [], []
    evidence = {'sources': [], 'metrics': []}
    for source in sources:
        info = inspect_source(source['path'])
        name, identifier = source.get('name', Path(source['path']).name), source.get('id')
        if info.get('text'):
            passages, scope = _source_passages(info['text'], spec.get('brief', ''), 'source:' + str(identifier))
            documents.append({'id': identifier, 'name': name, 'passage_ids': [item['id'] for item in passages], 'source_scope': scope})
            evidence['sources'].append({'id': 'source:' + str(identifier), 'name': name, 'passages': passages, 'source_scope': scope})
        if Path(source['path']).suffix.lower() in ('.png', '.jpg', '.jpeg'):
            references.append(source['path'])
            evidence['sources'].append({'id': 'source:' + str(identifier), 'name': name, 'type': 'actual_uploaded_scientific_image', 'width': info['width'], 'height': info['height']})
    for upstream in asset.get('upstream', []):
        evidence['sources'].append({'id': 'asset:' + str(upstream.get('id')), 'kind': 'definition',
                         'text': json.dumps({key: upstream.get(key) for key in ('title', 'caption', 'spec', 'review')}, ensure_ascii=False)})
    return {'method': {'brief': spec.get('brief', ''), 'documents': documents}, 'brief': spec.get('brief', ''),
            'evidence': evidence, 'caption': asset.get('caption', ''),
            'narrative_mode': spec.get('narrative_mode', 'scientific_story')}, references


def _graph_from_scene(scene):
    nodes, lookup = [], {}
    for obj in scene.get('objects', []):
        identifier = obj.get('operation_id')
        if identifier:
            lookup[obj['id']] = identifier
            if not any(node['id'] == identifier for node in nodes):
                nodes.append({'id': identifier, 'label': obj.get('label', '')})
    edges = []
    for connection in sorted(scene.get('connections', []), key=lambda item: item.get('semantic_edge', item.get('semantic_index', 0))):
        if connection.get('semantic_edge', connection.get('semantic_index')) is not None:
            edges.append({'source': lookup.get(connection['source']), 'target': lookup.get(connection['target']),
                          'label': connection.get('label', '')})
    return {'nodes': nodes, 'edges': edges, 'narrative_mode': 'method_only'}


def _latex(text):
    replacements = {'\\': r'\textbackslash{}', '&': r'\&', '%': r'\%', '$': r'\$', '#': r'\#',
                    '_': r'\_', '{': r'\{', '}': r'\}', '~': r'\textasciitilde{}', '^': r'\textasciicircum{}'}
    return ''.join(replacements.get(char, char) for char in str(text))


def _table(output, spec, sources, style):
    from figloom.scientific.tables import materialize_statistical_tables
    if spec.get('y') and spec.get('aggregation', 'mean') == 'mean':
        data, source, frame = _statistics(spec, sources)
        table_spec = {'kind': 'records', 'uncertainty': {'ci95': 'ci'}.get(spec.get('interval', 'none'), spec.get('interval', 'none')),
                      'require_complete': True, 'bold_best': bool(spec.get('bold_best', False))}
        xs = list(dict.fromkeys(record.get('x') for record in data['statistical_results']['records']))
        if xs != [None]:
            table_spec['x'] = xs
        draft = {'sections': [{'blocks': [{'type': 'table', 'argumentative_duty': spec.get('argumentative_duty', 'effectiveness'),
                  'statistics_spec': table_spec, 'caption': '', 'notes': []}]}]}
        computed = materialize_statistical_tables(draft, {'statistics': data['statistical_results'], 'metrics': data['metric_bindings']})
        block = computed['sections'][0]['blocks'][0]
        values = {item['id']: f"{item['value']:.{max(1, spec.get('precision', 3))}g}" for item in data['metric_bindings']}
        def resolve(text):
            return re.sub(r'\[\[metric:([^]]+)\]\]', lambda match: values[match[1]], str(text))
        columns, rows = [resolve(item) for item in block['columns']], [[resolve(item) for item in row] for row in block['rows']]
        notes = [resolve(item) for item in block['notes']]
        from figloom.scientific.statistical import uncertainty_notes
        notes += [note for note in uncertainty_notes(data['statistical_results']['records']) if note not in notes]
        if len(block.get('column_groups', [])) > 1:
            for group in block['column_groups']:
                for col in range(group['start'], group['start'] + group['count']):
                    columns[col] = group['label'] + ' · ' + columns[col]
        if block.get('row_groups'):
            scope_by_row = {index: group['label'] for group in block['row_groups'] for index in range(group['start'], group['start'] + group['count'])}
            columns = ['Scope', *columns]
            rows = [[scope_by_row.get(index, ''), *row] for index, row in enumerate(rows)]
        _dump(output / 'statistics.json', data)
        _dump(output / 'table_binding.json', block)
    else:
        source, frame = _mapped_source(spec, sources)
        columns = spec.get('columns') or list(frame.columns)
        if not isinstance(columns, list) or not columns or any(column not in frame for column in columns):
            raise ValueError('Choose actual uploaded table columns')
        rows = [[_clean_scalar(value) for value in row] for row in frame[columns].itertuples(index=False, name=None)]
        notes = ['Uploaded observations; no aggregation or estimated uncertainty.']
        data = None
    with (output / 'table.csv').open('w', newline='', encoding='utf-8') as stream:
        writer = csv.writer(stream); writer.writerow(columns); writer.writerows(rows)
    _dump(output / 'table.json', {'columns': columns, 'rows': rows, 'notes': notes})
    tex = ['\\begin{tabular}{' + 'l' * len(columns) + '}', r'\toprule', ' & '.join(_latex(item) for item in columns) + r' \\', r'\midrule']
    tex.extend(' & '.join(_latex('' if item is None else item) for item in row) + r' \\' for row in rows)
    tex += [r'\bottomrule', r'\end{tabular}', '\n'.join('% ' + note for note in notes)]
    (output / 'table.tex').write_text('\n'.join(tex), encoding='utf-8')
    cells = ''.join('<tr>' + ''.join('<td>' + html.escape(str('' if value is None else value)) + '</td>' for value in row) + '</tr>' for row in rows)
    (output / 'table.html').write_text('<!doctype html><meta charset="utf-8"><style>table{border-collapse:collapse;font:14px Georgia}th,td{padding:8px 14px;text-align:left}thead{border-top:2px solid;border-bottom:1px solid}tbody{border-bottom:2px solid}</style><table><thead><tr>' + ''.join('<th>' + html.escape(str(column)) + '</th>' for column in columns) + '</tr></thead><tbody>' + cells + '</tbody></table>' + ''.join('<p>' + html.escape(note) + '</p>' for note in notes), encoding='utf-8')
    paths, review = _table_images(output, columns, rows, notes, style)
    paths.update({key: str(output / filename) for key, filename in {'csv': 'table.csv', 'tex': 'table.tex', 'html': 'table.html', 'data': 'table.json'}.items()})
    return paths, review, data


def _table_images(output, columns, rows, notes, style):
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    from matplotlib.backends.backend_pdf import PdfPages
    import textwrap
    width, _, font = figure_dimensions(style)
    pages = [rows[index:index + 30] for index in range(0, len(rows), 30)] or [[]]
    issues, paths = [], {'pdf': str(output / 'figure.pdf')}
    with plt.rc_context({'font.family': 'DejaVu Serif', 'svg.fonttype': 'none', 'pdf.fonttype': 42}):
        with PdfPages(output / 'figure.pdf') as pdf:
            for index, page in enumerate(pages):
                height = max(2.5, (len(page) + 3) * .25 + .55)
                fig, ax = plt.subplots(figsize=(width, height)); ax.axis('off')
                try:
                    table = ax.table(cellText=[['' if value is None else str(value) for value in row] for row in page],
                                     colLabels=[textwrap.fill(str(column), max(8, int(width * 8 / len(columns)))) for column in columns],
                                     cellLoc='left', colLoc='left', loc='upper center', bbox=[0, .1, 1, .85])
                    table.auto_set_font_size(False); table.set_fontsize(font)
                    for (row, col), cell in table.get_celld().items():
                        cell.set_linewidth(.5 if row == 0 or row == len(page) else 0)
                        cell.visible_edges = 'TB' if row == 0 else 'B' if row == len(page) else ''
                        if row == 0: cell.set_text_props(weight='bold')
                    fig.text(.06, .015, textwrap.fill('; '.join(notes), max(20, int(width * 11))), fontsize=8, va='bottom')
                    fig.subplots_adjust(left=.04, right=.96, top=.98, bottom=.08)
                    fig.canvas.draw(); renderer = fig.canvas.get_renderer()
                    for cell in table.get_celld().values():
                        bounds = cell.get_text().get_window_extent(renderer)
                        own = cell.get_window_extent(renderer)
                        if bounds.width > own.width - 3 or bounds.height > own.height - 2:
                            issues.append({'code': 'table_cell_overflow', 'text': cell.get_text().get_text(), 'page': index + 1})
                    pdf.savefig(fig)
                    for extension in ('svg', 'png'):
                        if index >= 5: continue
                        name = 'figure.' + extension if index == 0 else f'table-page-{index + 1}.' + extension
                        fig.savefig(output / name, dpi=300)
                        paths[extension if index == 0 else f'page{index + 1}_{extension}'] = str(output / name)
                finally:
                    plt.close(fig)
    review = {'kind': 'table', 'geometry_passed': not issues, 'quality_issues': issues,
              'minimum_font_pt': min(font, 8), 'pages': len(pages), 'rows': len(rows),
              'columns': len(columns), 'preview_pages': min(5, len(pages)), 'preview_scope': 'First five pages; PDF and CSV contain every supplied row', 'numerical_check': 'Source-bound computation' if (output / 'statistics.json').exists() else 'Exact uploaded cells',
              'model_review_status': 'not_requested'}
    _dump(output / 'render_report.json', review); paths['report'] = str(output / 'render_report.json')
    return paths, review


def _check(cancelled):
    if cancelled and cancelled():
        raise InterruptedError('Asset operation was cancelled')


def _artifacts(output):
    return [{'name': path.name, 'format': path.suffix.lstrip('.') or 'text'}
            for path in sorted(output.iterdir()) if path.is_file() and not path.name.startswith('.')]


def _export_runtime(output, spec, asset, sources):
    """Bundle only the renderer, editable inputs and a package-independent entry."""
    package = Path(__file__).parent
    if package.parent.is_file() and zipfile.is_zipfile(package.parent):
        shutil.copyfile(package.parent, output / 'renderer.zip')
    else:
        with zipfile.ZipFile(output / 'renderer.zip', 'w', compression=zipfile.ZIP_DEFLATED) as archive:
            archive.writestr('figloom/__init__.py', '"""Standalone scientific renderer."""\n')
            for folder in ('scientific', 'providers'):
                for path in sorted((package / folder).rglob('*')):
                    if path.is_file() and path.suffix in ('.py', '.json', '.txt'):
                        archive.write(path, 'figloom/' + path.relative_to(package).as_posix())
            archive.write(package / 'engine.py', 'figloom/engine.py')
    inputs = []
    for index, source in enumerate(sources):
        name = f'input-{index + 1}' + Path(source['path']).suffix.lower()
        shutil.copyfile(source['path'], output / name)
        inputs.append({'id': source.get('id'), 'name': source.get('name', name), 'path': name})
    _dump(output / 'sources.json', inputs)
    _dump(output / 'spec.json', spec)
    _dump(output / 'asset.json', {**{key: asset.get(key) for key in ('id', 'kind', 'title', 'caption')}, '_artifact_dir': '.'})
    script = '''from pathlib import Path
import argparse, json, sys
root = Path(__file__).resolve().parent
sys.path.insert(0, str(root / 'renderer.zip'))
from figloom.engine import render_asset
parser = argparse.ArgumentParser(description='Render the editable exported scientific asset without the server')
parser.add_argument('--output', default='rerendered')
args = parser.parse_args()
asset = json.loads((root / 'asset.json').read_text())
asset['spec'] = json.loads((root / 'spec.json').read_text())
asset['_artifact_dir'] = str(root)
sources = json.loads((root / 'sources.json').read_text())
for source in sources: source['path'] = str(root / source['path'])
render_asset(asset, sources, root / args.output)
'''
    (output / 'rerender.py').write_text(script, encoding='utf-8')
    (output / 'requirements.txt').write_text('numpy\npandas\nscipy\nmatplotlib\nPillow\nhttpx\npypdf\n', encoding='utf-8')


def render_asset(asset, sources, output_dir, *, client=None, action='render', cancelled=None):
    if action not in ('render', 'generate', 'review'):
        raise ValueError('Action must be render, generate or review')
    kind = asset.get('kind')
    spec = {**default_spec(kind), **deepcopy(asset.get('spec') or {})}
    sources = _sources_list(sources)
    output = Path(output_dir); output.mkdir(parents=True, exist_ok=True)
    _check(cancelled)
    if action == 'review' and asset.get('_artifact_dir'):
        return _review_existing(asset, spec, sources, output, client, cancelled)
    if action == 'generate' and client is None:
        raise ProviderError('Generate requires an actual configured model client', code='configuration')
    originals = []
    if client and cancelled:
        for transport in (client, getattr(client, 'image_client', None)):
            if transport:
                original = transport.request_guard
                def checked(event, saved=original):
                    if event.get('phase') == 'before': _check(cancelled)
                    return saved(event) if saved else None
                originals.append((transport, original)); transport.request_guard = checked
    try:
        with TemporaryDirectory(prefix='figloom-') as temporary:
            stage = Path(temporary)
            style = _style(spec)
            candidates, caption, statistical = [], asset.get('caption', ''), None
            if kind == 'diagram':
                from figloom.scientific.spec import build_figure_contract
                from figloom.scientific.scene import render_scene
                from figloom.scientific.composition import compile_composition
                context, references = _context(asset, spec, sources)
                graph = deepcopy(spec.get('graph'))
                scene = deepcopy(spec.get('scene'))
                if not graph:
                    for source in sources:
                        if Path(source['path']).suffix.lower() == '.json':
                            value = json.loads(Path(source['path']).read_text())
                            if isinstance(value, dict) and value.get('nodes'):
                                graph = value; break
                if action == 'generate':
                    had_finished_raster = bool(spec.get('finished_raster_file'))
                    spec.pop('finished_raster_file', None)
                    spec.pop('scientific_snapshot_file', None)
                    if graph:
                        graph.pop('production_raster', None)
                    if had_finished_raster:
                        if graph:
                            context['method']['editable_source_graph'] = {key: deepcopy(graph[key]) for key in ('nodes', 'edges', 'key_operation_id') if key in graph}
                        graph = None
                    client.figure_source_images = references
                    from figloom.scientific.model_workflow import design_method_graph
                    from figloom.scientific.production import produce_diagram
                    client.config['figure_raster_finish_candidates'] = spec['raster_finish_candidate_count']
                    if not graph:
                        # Source pixels are supplied to genuine storyboard calls.
                        client.figure_source_images = references
                        graph = design_method_graph(client, context, stage / 'design')
                    paths, selection = produce_diagram(client, stage / 'final', graph, style, context=context,
                                                      attempts=spec['native_attempts'], reference_paths=references[:4])
                    review = selection
                    for candidate_path in (stage / 'final').glob('candidate-*'):
                        if candidate_path.is_file(): shutil.copyfile(candidate_path, output / candidate_path.name)
                    manifest = stage / 'final' / 'actual_candidates.json'
                    if manifest.is_file(): candidates = json.loads(manifest.read_text())
                    scene = graph.get('production_scene')
                    spec.update(graph=graph, scene=scene, composition=graph.get('production_composition'))
                    if graph.get('production_raster'):
                        # Preserve the actual finished raster for deterministic re-export.
                        spec['finished_raster_file'] = Path(paths['png']).name
                elif spec.get('finished_raster_file'):
                    from figloom.scientific.raster_finish import _raster_pdf
                    name = spec['finished_raster_file']
                    if not isinstance(name, str) or Path(name).name != name:
                        raise ValueError('Finished raster reference must be an existing local artifact filename')
                    previous_root = Path(asset.get('_artifact_dir', '.')).resolve()
                    _validate_raster_snapshot(spec, sources, previous_root)
                    previous = (previous_root / name).resolve()
                    if not previous.is_relative_to(previous_root) or not previous.is_file() or previous.stat().st_size > 64 * 1024 * 1024:
                        raise ValueError('The retained finished raster is missing or exceeds 64 MiB')
                    target = stage / 'figure.png'; shutil.copyfile(previous, target)
                    with Image.open(target) as image: image.verify()
                    width, height, _ = figure_dimensions(style)
                    _raster_pdf(target, stage / 'figure.pdf', width, height)
                    paths = {'png': str(target), 'pdf': str(stage / 'figure.pdf')}
                    review = {'kind': 'diagram', 'render_engine': 'raster_finish', 'editable_native': False,
                              'model_review_status': 'not_requested', 'quality_scope': 'Actual retained finished pixels'}
                    spec['finished_raster_file'] = 'figure.png'
                else:
                    if not graph and scene:
                        graph = _graph_from_scene(scene)
                    if not graph:
                        raise ValueError('Provide an editable source graph and scene/composition, or generate from uploaded method text')
                    context['narrative_mode'] = graph.get('narrative_mode', spec.get('narrative_mode', 'method_only'))
                    graph['narrative_mode'] = context['narrative_mode']
                    contract = build_figure_contract(graph, context=context, style=style)
                    contract['assets'] = deepcopy(graph.get('asset_definitions') or [record['definition'] for record in graph.get('asset_data', {}).values() if isinstance(record, dict) and record.get('definition')])
                    if not scene and spec.get('composition'):
                        scene = compile_composition(spec['composition'], contract, style)
                    if not scene:
                        raise ValueError('Manual rendering needs scene or composition; Generate designs the supplied source graph')
                    graph.update(production_scene=scene, production_contract=contract,
                                 asset_data=_component_assets(spec.get('asset_data', graph.get('asset_data', {})), asset.get('_artifact_dir'))) 
                    paths = render_scene(stage, graph, style)
                    review = json.loads(Path(paths['report']).read_text())
                    spec.update(graph=graph, scene=json.loads(Path(paths['scene']).read_text()))
                if paths.get('caption_context'):
                    companion = json.loads(Path(paths['caption_context']).read_text())
                    caption = companion.get('caption_text') or caption
                if action == 'review' and client:
                    review = _model_review(client, stage, paths, review, context, 'method')
            elif kind == 'plot':
                statistical, source, frame = _statistics(spec, sources)
                chart = spec['chart_type']
                if chart == 'heatmap':
                    # Uploaded x categories form the heatmap row identities.
                    for record in statistical['statistical_results']['records']:
                        if record.get('x') is not None:
                            record['dataset'] += ' · ' + str(record['x'])
                            for ref in record['refs'].values():
                                next(binding for binding in statistical['metric_bindings'] if binding['id'] == ref)['statistical_identity']['dataset'] = record['dataset']
                if action == 'generate':
                    from figloom.scientific.model_workflow import reviewed_render
                    paths, review = reviewed_render(client, stage, statistical, style, chart,
                                                   context={'caption': caption, 'statistics': statistical['statistical_results']},
                                                   attempts=spec.get('review_attempts', 3))
                    candidates = _retain_candidates(stage, output, review.get('candidate_id'))
                    if paths.get('style'):
                        chosen_style = json.loads(Path(paths['style']).read_text())
                        chosen_style.pop('kind', None)
                        spec['style'] = chosen_style
                        for field in ('width', 'height', 'font_size', 'palette'):
                            if field in chosen_style: spec[field] = chosen_style[field]
                else:
                    paths = render_figure(stage, statistical, style, chart)
                    review = json.loads(Path(paths['report']).read_text())
                    review.update(numerical_check='passed', geometry_passed=not review.get('quality_issues'), model_review_status='not_requested')
                    if action == 'review' and client:
                        review = _model_review(client, stage, paths, review, {'caption': caption, 'statistics': statistical['statistical_results']}, chart)
                caption = caption or _statistical_caption(spec, statistical)
            else:
                paths, review, statistical = _table(stage, spec, sources, style)
                if action in ('generate', 'review') and client:
                    # The model evaluates actual typography and source-bound numbers;
                    # computed cell text is never rewritten by a language model.
                    review = _model_review(client, stage, paths, review, {'caption': caption, 'statistics': statistical}, 'table')
                caption = caption or (_statistical_caption(spec, statistical) if statistical else 'Uploaded observations in the selected columns.')
            _check(cancelled)
            for key, filename in paths.items():
                if isinstance(filename, str) and Path(filename).is_file():
                    target = output / Path(filename).name
                    shutil.copyfile(filename, target)
            if statistical:
                _dump(output / 'statistics.json', statistical)
                _dump(output / 'summary.json', {'source': statistical['statistical_results']['coverage'],
                                              'records': statistical['statistical_results']['records'],
                                              'aggregation': spec['aggregation'], 'interval': spec['interval'],
                                              'statistics': statistical['statistical_results']})
            _externalize_graph_assets(output, spec, asset)
            if spec.get('finished_raster_file'): _save_raster_snapshot(output, spec, sources)
            review = _clean_review(review)
            _dump(output / 'review.json', review)
            (output / 'caption.txt').write_text(caption, encoding='utf-8')
            _export_runtime(output, spec, {**asset, 'caption': caption}, sources)
            _candidate_specs(output, candidates, spec, asset, caption, sources)
            _standalone_sources(output, candidates)
            # Single deterministic render is an actual selectable candidate too.
            if not candidates and (output / 'figure.png').is_file():
                candidates = [{'id': 'rendered', 'label': 'Current rendered asset', 'preview_name': 'figure.png',
                               'files': [item['name'] for item in _artifacts(output)], 'selected': True}]
            return {'artifacts': _artifacts(output), 'candidates': candidates, 'review': review,
                    'caption': caption, 'spec': spec}
    finally:
        for transport, original in originals:
            transport.request_guard = original
        if client and hasattr(client, 'figure_source_images'):
            del client.figure_source_images


def _model_review(client, stage, paths, report, context, kind):
    from figloom.scientific.model_workflow import review_with_models
    from figloom.scientific.workflow import REVIEW_ROLES
    bundle = {'version': 1, 'kind': kind, 'production_review_only': True,
              'candidates': [{'id': 'rendered', 'outputs': paths, 'report': report,
                              'style': {}, 'vector_content': Path(paths['svg']).read_text() if paths.get('svg') else ''}],
              'review_roles': list(REVIEW_ROLES)}
    reviews = review_with_models(client, bundle, stage / 'review', context)
    from figloom.scientific.workflow import select_candidate
    try:
        selection = select_candidate(bundle, reviews, require_alternatives=False)
    except ValueError as error:
        if not str(error).startswith('No figure candidate satisfies'):
            raise
        selection = {'publication_gate_passed': False, 'quality_status': 'revise', 'reason': str(error)}
    else:
        selection['publication_gate_passed'] = not report.get('quality_issues')
    return {**report, 'model_review_status': 'completed', 'independent_review': reviews, **selection}


def _retain_candidates(stage, output, selected=None):
    candidates = []
    manifests = sorted(stage.glob('iterations/*/candidate_manifest.json'), key=lambda path: int(path.parent.name))
    for manifest in manifests[-1:]:
        bundle = json.loads(manifest.read_text())
        for candidate in bundle['candidates']:
            identifier = 'round' + manifest.parent.name + '-' + candidate['id']
            names, outputs = [], {}
            for key, filename in candidate['outputs'].items():
                path = Path(filename)
                if path.is_file():
                    name = identifier + '-' + path.name
                    shutil.copyfile(path, output / name); names.append(name); outputs[key] = name
            candidates.append({'id': identifier, 'label': identifier, 'preview_name': identifier + '-figure.png',
                               'files': names, 'outputs': outputs, 'style': candidate.get('style', {}), 'selected': candidate['id'] == selected})
    return candidates


def _statistical_caption(spec, data):
    result = data['statistical_results']
    notes = []
    from figloom.scientific.statistical import uncertainty_notes
    notes = uncertainty_notes(result['comparisons'] or result['records'])
    if spec.get('chart_type') == 'forest' and result['comparisons']:
        baseline, candidate = spec['baseline'], spec['candidate']
        effect = str(baseline) + ' − ' + str(candidate) if spec.get('direction') == 'lower' else str(candidate) + ' − ' + str(baseline)
        return ('Paired ' + effect + ' differences in ' + str(spec.get('y')) + '. ' + '; '.join(notes)).strip()
    return (('Mean ' if spec.get('aggregation') == 'mean' else 'Observed ') + str(spec.get('y', 'values')) +
            (' by ' + str(spec['x']) if spec.get('x') else '') + '. ' + '; '.join(notes)).strip()


def _paired_comparisons(spec, fields, rows, source, bindings):
    if not fields['group'] or not fields['unit_id'] or spec.get('baseline') is None or spec.get('candidate') is None:
        raise ValueError('Paired comparisons require actual group, baseline, candidate and unit_id mappings')
    baseline, candidate = str(spec['baseline']), str(spec['candidate'])
    if baseline == candidate:
        raise ValueError('A paired comparison needs distinct observed groups')
    by_scope = defaultdict(lambda: defaultdict(lambda: defaultdict(list)))
    for index, row in enumerate(rows):
        method = str(_clean_scalar(row[fields['group']]))
        if method not in (baseline, candidate):
            continue
        unit = _clean_scalar(row[fields['unit_id']])
        if unit is None or isinstance(unit, (dict, list)):
            raise ValueError('Paired records require actual scalar unit identities')
        scope = tuple(_clean_scalar(row[fields[key]]) if fields[key] else None for key in ('x', 'facet'))
        by_scope[scope][method][unit].append((float(row[fields['y']]), index + 1))
    comparisons = []
    for index, ((x, facet), methods) in enumerate(by_scope.items()):
        if set(methods) != {baseline, candidate} or set(methods[baseline]) != set(methods[candidate]):
            raise ValueError('Paired comparisons require the same actual observed unit IDs in both groups at every scope')
        units = list(methods[baseline])
        if len(units) < 2:
            raise ValueError('A paired interval needs at least two matched observed units')
        sign = -1 if spec.get('direction') == 'lower' else 1
        deltas = [sign * (np.mean([v for v, _ in methods[candidate][unit]]) - np.mean([v for v, _ in methods[baseline][unit]])) for unit in units]
        mean = float(np.mean(deltas)); sd = float(np.std(deltas, ddof=1)); se = sd / math.sqrt(len(units))
        radius = float(student_t.ppf(.975, len(units) - 1)) * se
        p_value = None if sd == 0 else float(ttest_1samp(deltas, 0).pvalue)
        record = {'id': f'paired{index + 1}', 'dataset': str(facet) if facet is not None else source.get('name', 'Uploaded data'),
                  'metric': fields['y'], 'condition': None, 'x': x, 'baseline': baseline, 'candidate': candidate,
                  'improvement': mean, 'ci_low': mean - radius, 'ci_high': mean + radius, 'n_pairs': len(units),
                  'p_value': p_value, 'confidence': .95, 'unit': spec.get('unit', ''),
                  'precision': spec.get('precision', 3), 'direction': spec.get('direction', 'none'),
                  'uncertainty': {'type': 't_ci', 'unit': fields['unit_id'], 'confidence': .95,
                                  'scope': 'Matched unit means; two-sided paired Student-t test'},
                  'test': 'paired Student-t', 'test_status': 'undefined_zero_observed_variance' if sd == 0 else 'computed', 'effect_definition': 'baseline - candidate' if sign == -1 else 'candidate - baseline',
                  'unit_ids': units, 'paired_differences': [float(value) for value in deltas], 'refs': {}}
        record['source_rows'] = sorted(row for method in methods.values() for observations in method.values() for _, row in observations)
        for field in ('improvement', 'ci_low', 'ci_high', 'n_pairs', 'p_value', 'confidence'):
            if record[field] is None: continue
            ref = record['id'] + ':' + field; record['refs'][field] = ref
            bindings.append({'id': ref, 'value': record[field], 'precision': spec.get('precision', 3),
                             'statistical_identity': {name: record.get(name) for name in ('dataset', 'metric', 'condition', 'x', 'baseline', 'candidate')},
                             'source_id': source.get('id'), 'source_rows': record['source_rows']})
        comparisons.append(record)
    if not comparisons:
        raise ValueError('The chosen baseline and candidate do not exist in the uploaded observations')
    return comparisons


def _source_passages(text, brief, identifier, maximum=120_000):
    parts = re.split(r'\n\s*\n', text)
    passages = []
    offset = 0
    for index, part in enumerate(parts):
        start = text.find(part, offset)
        offset = start + len(part)
        if not part.strip(): continue
        for chunk_start in range(0, len(part), 8000):
            chunk = part[chunk_start:chunk_start + 8000]
            passages.append({'id': identifier + ':passage' + str(index + 1) + 'part' + str(chunk_start // 8000 + 1),
                             'paragraph': index + 1, 'character_start': start + chunk_start,
                             'character_end': start + chunk_start + len(chunk), 'text': chunk})
    if len(text) <= maximum:
        return passages, {'selection': 'complete_text', 'total_characters': len(text), 'included_characters': len(text)}
    terms = {term.lower() for term in re.findall(r'\w{3,}', brief)}
    scored = sorted(passages, key=lambda item: (sum(item['text'].lower().count(term) for term in terms), -item['paragraph']), reverse=True)
    selected, used = [], 0
    for passage in scored:
        if used + len(passage['text']) <= maximum:
            selected.append(passage); used += len(passage['text'])
    selected.sort(key=lambda item: item['paragraph'])
    return selected, {'selection': 'brief_relevant_whole_passages', 'total_characters': len(text),
                      'included_characters': used, 'omitted_passages': len(passages) - len(selected),
                      'source_is_complete': False}


def _review_existing(asset, spec, sources, output, client, cancelled):
    root = Path(asset['_artifact_dir']).resolve()
    actual = {}
    for artifact in asset.get('artifacts', []):
        name = artifact.get('name', '')
        path = (root / name).resolve()
        if Path(name).name != name or not path.is_relative_to(root) or not path.is_file():
            raise ValueError('Review requires actual current allowlisted asset files')
        actual[name] = path
    png = next((path for name, path in actual.items() if name.endswith('.png') and 'figure' in name), None)
    if png is None:
        raise ValueError('Review requires the actual rendered preview pixels')
    with Image.open(png) as image:
        image.verify()
    reports = [path for name, path in actual.items() if name.endswith('report.json')]
    report = json.loads(reports[0].read_text()) if reports else deepcopy(asset.get('review') or {})
    if asset['kind'] in ('plot', 'table') and spec.get('y'):
        computed, _, _ = _statistics(spec, sources)
        saved = next((path for name, path in actual.items() if name.endswith('statistics.json')), None)
        if saved:
            old = json.loads(saved.read_text())
            # Heatmap aliases are presentation, so compare the numerical bindings
            # and original record IDs against freshly computed uploaded values.
            left = {item['id']: item['value'] for item in old.get('metric_bindings', [])}
            right = {item['id']: item['value'] for item in computed['metric_bindings']}
            if left != right:
                raise ValueError('Current uploaded observations differ from the files under review; render this revision first')
        report['numerical_check'] = 'passed against current uploaded observations'
    report.update(actual_files=[path.name for path in actual.values()], model_review_status='not_requested')
    _check(cancelled)
    if client:
        context, _ = _context(asset, spec, sources)
        paths = {'png': str(png)}
        for key, ending in (('svg', '.svg'), ('data', 'figure_data.json'), ('caption_context', 'caption_context.json')):
            match = next((path for name, path in actual.items() if name.endswith(ending)), None)
            if match: paths[key] = str(match)
        report = _model_review(client, output, paths, report, context, 'method' if asset['kind'] == 'diagram' else 'table' if asset['kind'] == 'table' else spec['chart_type'])
    report = _clean_review(report)
    _dump(output / 'review.json', report)
    return {'artifacts': [{'name': 'review.json', 'format': 'json'}], 'candidates': [], 'review': report}


def _candidate_specs(output, candidates, spec, asset, caption, sources):
    common = [name for name in ('renderer.zip', 'sources.json', 'requirements.txt', 'statistics.json', 'summary.json', 'scientific_sources.json') if (output / name).exists()]
    common += [path.name for path in output.glob('input-*')]
    for candidate in candidates:
        own = deepcopy(spec)
        outputs = candidate.get('outputs', {})
        data_path = output / outputs.get('data', '') if outputs.get('data') else None
        if data_path and data_path.is_file() and asset['kind'] == 'diagram':
            graph = json.loads(data_path.read_text())
            own.update(graph=graph, scene=graph.get('production_scene'), composition=graph.get('production_composition'))
            if graph.get('production_raster'):
                own['finished_raster_file'] = candidate['preview_name']
            else:
                own.pop('finished_raster_file', None)
        if candidate.get('style'):
            own['style'] = candidate['style']
            for field in ('width', 'height', 'font_size', 'palette'):
                if field in candidate['style']: own[field] = candidate['style'][field]
        _externalize_graph_assets(output, own, asset, prefix='candidate-' + candidate['id'] + '-')
        if own.get('finished_raster_file'):
            _save_raster_snapshot(output, own, sources, prefix='candidate-' + candidate['id'] + '-')
        candidate['spec'] = own
        prefix = 'candidate-' + candidate['id']
        _dump(output / (prefix + '-spec.json'), own)
        _dump(output / (prefix + '-asset.json'), {key: asset.get(key) for key in ('id', 'kind', 'title', 'caption')})
        script = (output / 'rerender.py').read_text().replace("root / 'spec.json'", "root / '" + prefix + "-spec.json'").replace("root / 'asset.json'", "root / '" + prefix + "-asset.json'")
        (output / (prefix + '-rerender.py')).write_text(script)
        candidate['files'] += [prefix + '-spec.json', prefix + '-asset.json', prefix + '-rerender.py', *common]
        candidate['files'] += [path.name for path in output.glob(prefix + '-component-*.png')]
        if own.get('scientific_snapshot_file'):
            candidate['files'] += [own['scientific_snapshot_file'], 'scientific_sources.json']
        companion = output / outputs.get('caption_context', '') if outputs.get('caption_context') else None
        candidate['caption'] = json.loads(companion.read_text()).get('caption_text', caption) if companion and companion.is_file() else caption
        candidate_report = output / outputs.get('report', '') if outputs.get('report') else None
        if candidate_report and candidate_report.is_file():
            candidate['review'] = _clean_review(json.loads(candidate_report.read_text()))
        candidate['files'] = list(dict.fromkeys(candidate['files']))


def _component_assets(records, artifact_dir):
    import base64
    records = deepcopy(records)
    for identifier, record in records.items():
        if not isinstance(record, dict) or not record.get('file'):
            continue
        name = record['file']
        if not isinstance(name, str) or Path(name).name != name:
            raise ValueError('Component images must reference local artifact filenames')
        asset_root = Path(artifact_dir or '.').resolve()
        path = (asset_root / name).resolve()
        if not path.is_relative_to(asset_root) or not path.is_file() or path.stat().st_size > 32 * 1024 * 1024:
            raise ValueError('Actual component image is missing or exceeds 32 MiB')
        with Image.open(path) as image:
            if image.format != 'PNG': raise ValueError('Native illustration components must be PNG')
            image.verify()
        record['base64'] = base64.b64encode(path.read_bytes()).decode('ascii')
    return records


def _externalize_graph_assets(output, spec, asset, prefix=''):
    import base64
    graph = spec.get('graph')
    if not isinstance(graph, dict):
        return
    # A contract is recreated from the current editable graph for every render.
    previous_contract = graph.pop('production_contract', None)
    if isinstance(previous_contract, dict) and previous_contract.get('assets'):
        graph['asset_definitions'] = deepcopy(previous_contract['assets'])
    records = graph.get('asset_data', {})
    for identifier, record in records.items():
        if not isinstance(record, dict): continue
        if not isinstance(identifier, str) or not re.fullmatch(r'[A-Za-z][A-Za-z0-9_-]{0,79}', identifier):
            raise ValueError('Component images require simple source IDs')
        encoded = record.pop('base64', None)
        name = prefix + 'component-' + identifier + '.png'
        if encoded:
            payload = base64.b64decode(encoded, validate=True)
            (output / name).write_bytes(payload)
            record['file'] = name
        elif record.get('file'):
            path = Path(asset.get('_artifact_dir', '.')).resolve() / record['file']
            if path.is_file() and path != output / record['file']:
                shutil.copyfile(path, output / record['file'])
    if spec.get('asset_data'):
        spec['asset_data'] = deepcopy(records)


def _clean_review(value):
    if isinstance(value, dict):
        return {key: _clean_review(item) for key, item in value.items() if key != 'response_path'}
    if isinstance(value, list):
        return [_clean_review(item) for item in value]
    return value


def _standalone_sources(output, candidates):
    preamble = "from pathlib import Path\nimport sys\nsys.path.insert(0, str(Path(__file__).resolve().parent / 'renderer.zip'))\n"
    for name in ('plot.py', 'render_scene.py'):
        path = output / name
        if path.is_file():
            path.write_text(preamble + path.read_text())
    for candidate in candidates:
        outputs = candidate.get('outputs', {})
        source = outputs.get('source')
        if not source or not source.endswith('.py'): continue
        path = output / source
        text = path.read_text()
        for name in outputs.values():
            if not isinstance(name, str): continue
            # Generated companions retain the same local filename suffix.
            canonical = re.sub(r'^(?:candidate-[A-Za-z0-9_-]+-|round\d+-[A-Za-z0-9_-]+-)(?=figure_data\.json|style\.json|scene\.json)', '', name)
            if canonical != name:
                text = text.replace(repr(canonical), repr(name)).replace('"' + canonical + '"', '"' + name + '"')
        path.write_text(preamble + text)


def _semantic_spec(spec):
    graph = spec.get('graph') or {}
    return {'brief': spec.get('brief', ''), 'narrative_mode': spec.get('narrative_mode', 'scientific_story'),
            'graph': {key: deepcopy(graph[key]) for key in ('nodes', 'edges', 'storyboard', 'key_operation_id', 'narrative_mode', 'story_context', 'source_note', 'asset_definitions') if key in graph},
            'scene': deepcopy(spec.get('scene')), 'composition': deepcopy(spec.get('composition')),
            'print_style': _style(spec)}


def _scientific_sources(sources):
    records = []
    for index, source in enumerate(sources):
        info = inspect_source(source['path'])
        record = {'id': source.get('id'), 'name': source.get('name'), 'kind': info['kind']}
        if info['kind'] == 'data':
            frame = _read_frame(source['path'])
            record['rows'] = [{key: _clean_scalar(value) for key, value in row.items()} for row in frame.to_dict(orient='records')]
        elif Path(source['path']).suffix.lower() in ('.png', '.jpg', '.jpeg'):
            record['input_file'] = f'input-{index + 1}' + Path(source['path']).suffix.lower()
            record.update(width=info['width'], height=info['height'])
        else:
            record['text'] = info.get('text', '')
        records.append(record)
    return records


def _save_raster_snapshot(output, spec, sources, prefix=''):
    name = prefix + 'scientific_snapshot.json'
    _dump(output / 'scientific_sources.json', _scientific_sources(sources))
    _dump(output / name, {'spec': _semantic_spec(spec), 'sources_file': 'scientific_sources.json'})
    spec['scientific_snapshot_file'] = name


def _validate_raster_snapshot(spec, sources, artifact_dir):
    name = spec.get('scientific_snapshot_file')
    if not isinstance(name, str) or Path(name).name != name:
        raise ValueError('The finished raster lacks its scientific source comparison; generate this revision again')
    path = (artifact_dir / name).resolve()
    if not path.is_relative_to(artifact_dir) or not path.is_file():
        raise ValueError('The finished raster source comparison is missing; generate this revision again')
    saved = json.loads(path.read_text())
    source_name = saved.get('sources_file')
    if not isinstance(source_name, str) or Path(source_name).name != source_name:
        raise ValueError('Invalid scientific source comparison file')
    source_file = (artifact_dir / source_name).resolve()
    if not source_file.is_relative_to(artifact_dir) or not source_file.is_file():
        raise ValueError('The actual retained scientific sources are missing')
    current = _scientific_sources(sources)
    changed = saved.get('spec') != _semantic_spec(spec) or json.loads(source_file.read_text()) != current
    for record, source in zip(current, sources):
        if record.get('input_file'):
            retained = (artifact_dir / record['input_file']).resolve()
            if not retained.is_relative_to(artifact_dir) or not retained.is_file() or retained.read_bytes() != Path(source['path']).read_bytes():
                changed = True
    if changed:
        raise ValueError('Scientific content or source inputs changed after raster generation; choose Generate for this revision, or remove finished_raster_file and render an editable native scene/composition')
