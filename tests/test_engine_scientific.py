import json
import math
import os
from pathlib import Path
import subprocess
import sys
from xml.etree import ElementTree

import numpy as np
from PIL import Image
from pypdf import PdfReader
import pytest
from scipy.stats import t

from figloom.engine import default_spec, inspect_source, make_client, render_asset
from figloom.providers.client import ProviderError


def uploaded(tmp_path, text, suffix='.csv'):
    path = tmp_path / ('observations' + suffix)
    path.write_text(text)
    return [{'id': 'source1', 'name': 'Measured outcomes', 'path': str(path)}]


def base_spec(**changes):
    return {'source_id': 'source1', 'x': 'step', 'y': 'outcome', 'group': 'method',
            'unit_id': 'subject', 'interval': 'ci95', **changes}


DATA = ('step,method,outcome,subject\n'
        '1,A,2,u1\n1,A,4,u2\n1,A,6,u3\n'
        '2,A,4,u1\n2,A,6,u2\n2,A,8,u3\n'
        '1,B,4,u1\n1,B,7,u2\n1,B,10,u3\n'
        '2,B,7,u1\n2,B,10,u2\n2,B,13,u3\n')


@pytest.mark.parametrize('chart', ['line', 'bar', 'scatter', 'heatmap', 'forest'])
def test_actual_formats_and_data_bindings_for_all_chart_types(tmp_path, chart):
    sources = uploaded(tmp_path, DATA)
    spec = base_spec(chart_type=chart)
    if chart == 'forest': spec.update(baseline='A', candidate='B')
    root = tmp_path / chart
    result = render_asset({'kind': 'plot', 'spec': spec}, sources, root)
    assert result['review']['geometry_passed'], result['review']['quality_issues']
    assert result['review']['numerical_check'] == 'passed'
    assert (root / 'figure.pdf').read_bytes().startswith(b'%PDF-')
    assert Image.open(root / 'figure.png').width == 1950
    assert ElementTree.parse(root / 'figure.svg').findall('.//{http://www.w3.org/2000/svg}text')
    assert float(PdfReader(root / 'figure.pdf').pages[0].mediabox.width) == pytest.approx(6.5 * 72)
    data = json.loads((root / 'statistics.json').read_text())
    first = data['statistical_results']['records'][0]
    assert first['estimate'] == 4
    assert first['ci_low'] == pytest.approx(4 - t.ppf(.975, 2) * 2 / math.sqrt(3))
    assert first['source_rows'] == [1, 2, 3]
    assert data['statistical_results']['coverage']['mapped_rows'] == 12
    assert all(Path(artifact['name']).name == artifact['name'] for artifact in result['artifacts'])


@pytest.mark.parametrize('interval,field,expected', [('sd', 'sd', 2), ('se', 'se', 2 / math.sqrt(3))])
def test_sd_and_se_exact_arithmetic(tmp_path, interval, field, expected):
    sources = uploaded(tmp_path, DATA)
    render_asset({'kind': 'plot', 'spec': base_spec(interval=interval, chart_type='bar')}, sources, tmp_path / 'out')
    first = json.loads((tmp_path / 'out/statistics.json').read_text())['statistical_results']['records'][0]
    assert first[field] == pytest.approx(expected)
    assert 'ci_low' not in first


def test_units_are_aggregated_before_uncertainty(tmp_path):
    sources = uploaded(tmp_path, 'step,method,outcome,subject\n1,A,0,u1\n1,A,10,u1\n1,A,11,u2\n')
    render_asset({'kind': 'plot', 'spec': base_spec(chart_type='bar', interval='sd')}, sources, tmp_path / 'out')
    first = json.loads((tmp_path / 'out/statistics.json').read_text())['statistical_results']['records'][0]
    assert first['estimate'] == 8
    assert first['n_units'] == 2
    assert first['sd'] == pytest.approx(np.std([5, 11], ddof=1))


def test_paired_comparison_matches_observed_units_and_source_rows(tmp_path):
    sources = uploaded(tmp_path, DATA)
    render_asset({'kind': 'plot', 'spec': base_spec(chart_type='forest', baseline='A', candidate='B')}, sources, tmp_path / 'out')
    data = json.loads((tmp_path / 'out/statistics.json').read_text())
    pair = data['statistical_results']['comparisons'][0]
    assert pair['paired_differences'] == [2, 3, 4]
    assert pair['improvement'] == 3
    assert pair['n_pairs'] == 3
    assert pair['ci_low'] == pytest.approx(3 - t.ppf(.975, 2) / math.sqrt(3))
    assert pair['p_value'] == pytest.approx(.03509871864598465)
    assert pair['source_rows'] == [1, 2, 3, 7, 8, 9]


@pytest.mark.parametrize('bad_spec,text,reason', [
    (base_spec(interval='sd'), 'step,method,outcome,subject\n1,A,3,u1\n', 'at least two'),
    (base_spec(), 'step,method,outcome,subject\n1,A,,u1\n1,A,3,u2\n', 'finite'),
    (base_spec(y='absent'), DATA, 'absent'),
    (base_spec(aggregation='none'), DATA, 'Raw observations'),
    (base_spec(chart_type='forest', baseline='A', candidate='B'), DATA.replace('1,B,7,u2','1,B,7,other'), 'same actual'),
])
def test_invalid_science_fails_before_publishing(tmp_path, bad_spec, text, reason):
    sources = uploaded(tmp_path, text)
    with pytest.raises(ValueError, match=reason):
        render_asset({'kind': 'plot', 'spec': bad_spec}, sources, tmp_path / 'out')
    assert not (tmp_path / 'out/figure.png').exists()


def test_raw_scatter_preserves_each_observation(tmp_path):
    sources = uploaded(tmp_path, DATA)
    result = render_asset({'kind': 'plot', 'spec': base_spec(aggregation='none', interval='none', chart_type='scatter')}, sources, tmp_path / 'out')
    assert result['review']['displayed_points'] == 12
    records = json.loads((tmp_path / 'out/statistics.json').read_text())['statistical_results']['records']
    assert [item['estimate'] for item in records] == [2,4,6,4,6,8,4,7,10,7,10,13]
    assert all(item['n_units'] == 1 and 'sd' not in item for item in records)


def test_booktabs_table_preserves_scope_and_exact_uncertainty(tmp_path):
    sources = uploaded(tmp_path, DATA)
    result = render_asset({'kind': 'table', 'spec': base_spec()}, sources, tmp_path / 'out')
    assert result['review']['geometry_passed']
    tex = (tmp_path / 'out/table.tex').read_text()
    assert '\\toprule' in tex and '\\bottomrule' in tex
    assert 'step = 1' in tex and 'step = 2' in tex
    table = json.loads((tmp_path / 'out/table.json').read_text())
    assert table['columns'] == ['Scope', 'Method', 'Outcome']
    assert len(table['rows']) == 4
    assert any('Student-t' in note for note in table['notes'])


def test_raw_table_does_not_drop_rows_or_create_statistics(tmp_path):
    sources = uploaded(tmp_path, DATA)
    render_asset({'kind': 'table', 'spec': {'source_id': 'source1', 'columns': ['subject', 'outcome']}}, sources, tmp_path / 'out')
    table = json.loads((tmp_path / 'out/table.json').read_text())
    assert len(table['rows']) == 12
    assert table['rows'][0] == ['u1', 2]
    assert not (tmp_path / 'out/statistics.json').exists()


@pytest.mark.parametrize('suffix,text', [('.tsv','x\ty\n1\t2\n3\t4\n'),('.json','[{"x":1,"y":2},{"x":3,"y":4}]')])
def test_inspection_reports_actual_rows_and_columns(tmp_path, suffix, text):
    path = Path(uploaded(tmp_path, text, suffix)[0]['path'])
    result = inspect_source(path)
    assert result['columns'] == ['x', 'y']
    assert result['row_count'] == 2
    assert result['preview'] == [{'x': 1, 'y': 2}, {'x': 3, 'y': 4}]
    assert result['unique_values']['x'] == [1, 3]
    assert result['unique_value_counts']['x'] == 2


def test_export_rerenders_without_installed_project_or_original_repo(tmp_path):
    sources = uploaded(tmp_path, DATA)
    output = tmp_path / 'export'
    render_asset({'kind':'plot','spec':base_spec(chart_type='bar')}, sources, output)
    env = {**os.environ, 'PYTHONPATH': '', 'PYTHONDONTWRITEBYTECODE': '1'}
    completed = subprocess.run([sys.executable, '-I', str(output / 'rerender.py'), '--output', 'again'],
                               cwd=output, env=env, capture_output=True, text=True)
    assert completed.returncode == 0, completed.stderr
    assert Image.open(output / 'again/figure.png').size == Image.open(output / 'figure.png').size
    assert json.loads((output / 'again/statistics.json').read_text()) == json.loads((output / 'statistics.json').read_text())


def test_review_inspects_retained_files_and_detects_changed_observations(tmp_path):
    sources = uploaded(tmp_path, DATA)
    asset = {'kind':'plot','spec':base_spec(chart_type='bar')}
    output = tmp_path / 'out'
    result = render_asset(asset, sources, output)
    asset.update(spec=result['spec'], artifacts=result['artifacts'], _artifact_dir=str(output))
    original = (output / 'figure.png').read_bytes()
    reviewed = render_asset(asset, sources, tmp_path / 'review', action='review')
    assert reviewed['review']['numerical_check'] == 'passed against current uploaded observations'
    assert (output / 'figure.png').read_bytes() == original
    Path(sources[0]['path']).write_text(DATA.replace('1,A,2,u1', '1,A,22,u1'))
    with pytest.raises(ValueError, match='differ'):
        render_asset(asset, sources, tmp_path / 'changed-review', action='review')


def test_model_settings_use_actual_ollama_and_distinct_image_transport():
    events=[]
    client = make_client({'text': {'api':'ollama','base_url':'http://127.0.0.1:11434','model':'qwen2.5:3b','local':True},
                          'image': {'base_url':'https://example.org/v1','model':'configured-image','max_request_usd':.4}}, guard=lambda event:events.append(event))
    endpoint, request = client.build_request([{'role':'user','content':'Actual text'}])
    assert endpoint == '/api/chat' and request['model'] == 'qwen2.5:3b'
    assert client.image_client.base == 'https://example.org/v1'
    assert client.image_client.config['image_generation']['model'] == 'configured-image'
    assert events == []  # Settings validation makes no model calls.
    with pytest.raises(ProviderError, match='guard'):
        make_client({'text':{'base_url':'https://example.org/v1','model':'configured'}})


def test_generate_never_simulates_missing_model(tmp_path):
    with pytest.raises(ProviderError, match='configured'):
        render_asset({'kind':'diagram','spec':{'brief':'Actual method'}},[],tmp_path/'out',action='generate')
    assert default_spec('diagram')['scene'] is None


def test_ollama_accepts_explicit_response_schema_without_affecting_default():
    client=make_client({'text':{'api':'ollama','base_url':'http://127.0.0.1:11434','model':'qwen2.5:3b'}},guard=lambda event:None)
    message=[{'role':'user','content':'Actual request'}]
    assert client.build_request(message)[1]['format']=='json'
    schema={'type':'object','properties':{'answer':{'type':'string'}},'required':['answer']}
    client.config['response_schema']=schema
    assert client.build_request(message)[1]['format']==schema
    assert 'format' not in client.build_request(message,json_mode=False)[1]


def test_constant_paired_data_does_not_fabricate_a_significance_test(tmp_path):
    sources=uploaded(tmp_path,'step,method,outcome,subject\n1,A,2,u1\n1,A,4,u2\n1,B,3,u1\n1,B,5,u2\n')
    render_asset({'kind':'plot','spec':base_spec(chart_type='forest',baseline='A',candidate='B')},sources,tmp_path/'out')
    comparison=json.loads((tmp_path/'out/statistics.json').read_text())['statistical_results']['comparisons'][0]
    assert comparison['improvement']==1
    assert comparison['p_value'] is None
    assert comparison['test_status']=='undefined_zero_observed_variance'
    assert 'p_value' not in comparison['refs']


def test_blank_optional_image_settings_are_omitted():
    client=make_client({'text':{'api':'ollama','base_url':'http://127.0.0.1:11434','model':'qwen2.5:3b'},
                        'image':{'base_url':'https://example.org/v1','model':'actual-image','max_request_usd':.3,'size':'','quality':None}},guard=lambda event:None)
    from figloom.scientific.images import build_image_request
    options,payload=build_image_request(client,'Actual scientific mechanism')
    assert 'size' not in options and 'quality' not in payload


def test_svg_inspection_never_resolves_entities(tmp_path):
    path=tmp_path/'source.svg'
    path.write_text('<svg xmlns="http://www.w3.org/2000/svg"><text>Actual diagram</text></svg>')
    assert 'Actual diagram' in inspect_source(path)['text']
    path.write_text('<!DOCTYPE svg [<!ENTITY secret SYSTEM "file:///etc/passwd">]><svg>&secret;</svg>')
    with pytest.raises(ValueError,match='entities'):
        inspect_source(path)


def test_exported_plot_source_itself_runs_without_project_imports(tmp_path):
    sources=uploaded(tmp_path,DATA)
    output=tmp_path/'export'
    render_asset({'kind':'plot','spec':base_spec(chart_type='bar')},sources,output)
    completed=subprocess.run([sys.executable,'-I',str(output/'plot.py')],cwd=output,
                             env={**os.environ,'PYTHONPATH':'','PYTHONDONTWRITEBYTECODE':'1'},capture_output=True,text=True)
    assert completed.returncode==0,completed.stderr
    assert (output/'figure.pdf').read_bytes().startswith(b'%PDF-')


def test_real_alternative_candidates_keep_actual_statistics_for_chat(tmp_path):
    from figloom.engine import _statistics,_retain_candidates,_candidate_specs,_export_runtime,_dump,_style
    from figloom.scientific.workflow import render_candidates
    sources=uploaded(tmp_path,DATA)
    spec={**default_spec('plot'),**base_spec(chart_type='bar')}
    data,_,_=_statistics(spec,sources)
    stage=tmp_path/'stage';output=tmp_path/'out';output.mkdir()
    render_candidates(stage/'iterations/1',data,_style(spec),'bar')
    candidates=_retain_candidates(stage,output)
    _dump(output/'statistics.json',data)
    _dump(output/'summary.json',{'statistics':data['statistical_results']})
    asset={'kind':'plot','spec':spec}
    _export_runtime(output,spec,asset,sources)
    _candidate_specs(output,candidates,spec,asset,'Actual observed outcomes.',sources)
    assert len(candidates)==3
    for candidate in candidates:
        assert 'statistics.json' in candidate['files'] and 'summary.json' in candidate['files']
        saved=json.loads((output/'statistics.json').read_text())
        assert saved['statistical_results']['records'][0]['estimate']==4
        assert (output/candidate['preview_name']).is_file()
