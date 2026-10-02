from copy import deepcopy
import json
from pathlib import Path
from xml.etree import ElementTree

from PIL import Image

from figloom.engine import render_asset


def scientific_scene():
    graph={'nodes':[{'id':'image','label':'Encoded image'},{'id':'attention','label':'Query attention'},{'id':'tokens','label':'Output tokens'}],
           'edges':[{'source':'image','target':'attention'},{'source':'attention','target':'tokens'}], 'narrative_mode':'method_only'}
    scene={'version':1,'width_in':6.5,'height_in':3.6,'panels':[{'id':'main','role':'mechanism','title':'Query mechanism','bbox':[.02,.02,.96,.96]}],
           'objects':[
               {'id':'visual','panel':'main','operation_id':'image','kind':'tensor','label':'Encoded image','bbox':[.06,.27,.21,.50], 'params':{'shape':[4,4,3]},'evidence_refs':['operation:image']},
               {'id':'queries','panel':'main','operation_id':'attention','kind':'matrix','label':'Query attention','bbox':[.39,.27,.21,.50],'params':{'rows':4,'cols':4,'mask':'causal','schematic':True},'evidence_refs':['operation:attention']},
               {'id':'text','panel':'main','operation_id':'tokens','kind':'tokens','label':'Output tokens','bbox':[.72,.27,.21,.50],'params':{'items':['x₁','x₂','x₃']},'evidence_refs':['operation:tokens']}],
           'connections':[{'id':'image_query','source':'visual','target':'queries','semantic_edge':0,'label':''},
                          {'id':'query_text','source':'queries','target':'text','semantic_edge':1,'label':''}], 'annotations':[]}
    return {'kind':'diagram','spec':{'graph':graph,'scene':scene,'height':3.6}}


def test_native_diagram_has_real_tensors_masks_tokens_and_editable_text(tmp_path):
    result=render_asset(scientific_scene(), [], tmp_path/'out')
    assert result['review']['geometry_passed'],result['review']['quality_issues']
    assert result['review']['native_shape_elements']>45
    svg=ElementTree.parse(tmp_path/'out/figure.svg')
    assert len(svg.findall('.//{http://www.w3.org/2000/svg}text'))>=8
    assert not svg.findall('.//{http://www.w3.org/2000/svg}image')
    assert Image.open(tmp_path/'out/figure.png').size==(1950,1080)
    assert result['spec']['scene']['objects'][1]['params']['mask']=='causal'


def test_current_user_source_and_scene_edits_are_rerendered_without_locking(tmp_path):
    asset=scientific_scene()
    first=render_asset(asset,[],tmp_path/'first')
    asset['spec']=deepcopy(first['spec'])
    asset['spec']['graph']['nodes'][0]['label']='Visual features'
    asset['spec']['scene']['objects'][0]['label']='Visual features'
    second=render_asset(asset,[],tmp_path/'second')
    assert json.loads((tmp_path/'second/figure_data.json').read_text())['production_contract']['source_snapshot']['nodes'][0]['label']=='Visual features'
    assert 'Visual features' in (tmp_path/'second/figure.svg').read_text()
    assert 'source_fingerprint' not in json.dumps(second)


def test_scene_can_supply_its_own_real_graph(tmp_path):
    asset=scientific_scene();asset['spec'].pop('graph')
    result=render_asset(asset,[],tmp_path/'out')
    assert len(result['spec']['graph']['nodes'])==3
    assert len(result['spec']['graph']['edges'])==2


def test_native_export_rerenders_in_an_isolated_process(tmp_path):
    import os
    import subprocess
    import sys
    output=tmp_path/'export'
    render_asset(scientific_scene(),[],output)
    completed=subprocess.run([sys.executable,'-I',str(output/'rerender.py'),'--output','again'],cwd=output,
                             env={**os.environ,'PYTHONPATH':'','PYTHONDONTWRITEBYTECODE':'1'},capture_output=True,text=True)
    assert completed.returncode==0,completed.stderr
    assert Image.open(output/'again/figure.png').size==(1950,1080)


def test_finished_raster_file_stays_lightweight_and_export_rerenders(tmp_path):
    import os
    import subprocess
    import sys
    native=tmp_path/'native'
    render_asset(scientific_scene(),[],native)
    asset={'kind':'diagram','spec':{'finished_raster_file':'figure.png','height':3.6},'_artifact_dir':str(native)}
    from figloom.engine import _save_raster_snapshot, default_spec
    asset['spec']={**default_spec('diagram'),**asset['spec']}
    _save_raster_snapshot(native,asset['spec'],[])
    output=tmp_path/'raster-export'
    result=render_asset(asset,[],output)
    assert result['spec']['finished_raster_file']=='figure.png'
    assert len(json.dumps(result['spec']))<1000
    assert result['review']['editable_native'] is False
    completed=subprocess.run([sys.executable,'-I',str(output/'rerender.py'),'--output','again'],cwd=output,
                             env={**os.environ,'PYTHONPATH':'','PYTHONDONTWRITEBYTECODE':'1'},capture_output=True,text=True)
    assert completed.returncode==0,completed.stderr
    assert (output/'again/figure.png').read_bytes()==(native/'figure.png').read_bytes()


def test_changed_raster_science_never_reuses_old_pixels(tmp_path):
    import pytest
    from figloom.engine import _save_raster_snapshot, default_spec
    native=tmp_path/'native'
    render_asset(scientific_scene(),[],native)
    document=tmp_path/'method.txt';document.write_text('Actual input is encoded and passed into causal attention.')
    sources=[{'id':'source','name':'Method','path':str(document)}]
    spec={**default_spec('diagram'),'finished_raster_file':'figure.png','height':3.6,'brief':'Causal attention'}
    _save_raster_snapshot(native,spec,sources)
    # The retained input is also part of the normal export bundle.
    asset={'kind':'diagram','spec':spec,'_artifact_dir':str(native)}
    render_asset(asset,sources,tmp_path/'unchanged')
    spec['graph']={'nodes':[{'id':'changed','label':'Changed operation'}],'edges':[]}
    with pytest.raises(ValueError,match='changed'):
        render_asset(asset,sources,tmp_path/'changed-graph')
    spec['graph']=None
    document.write_text('Actual input now uses bidirectional attention.')
    with pytest.raises(ValueError,match='changed'):
        render_asset(asset,sources,tmp_path/'changed-document')
    assert not (tmp_path/'changed-document/figure.png').exists()
