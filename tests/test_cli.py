from contextlib import contextmanager
import json
import socket
import threading
import time
import zipfile

import uvicorn

from figloom.api import create_app
from figloom.cli import default_data_dir, main, parser
from figloom.http_client import APIClient, ClientError
from figloom.store import Store


@contextmanager
def running_server(tmp_path):
    app = create_app(tmp_path / 'workspace')
    sock = socket.socket()
    sock.bind(('127.0.0.1', 0))
    port = sock.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(app, host='127.0.0.1', port=port, log_level='critical', access_log=False))
    thread = threading.Thread(target=server.run, kwargs={'sockets': [sock]}, daemon=True)
    thread.start()
    deadline = time.monotonic() + 10
    while not server.started:
        assert thread.is_alive() and time.monotonic() < deadline
        time.sleep(.02)
    try:
        yield f'http://127.0.0.1:{port}', app
    finally:
        server.should_exit = True
        thread.join(10)
        sock.close()


def test_actual_cli_http_upload_edit_render_and_export(tmp_path, capsys):
    with running_server(tmp_path) as (server, app):
        def command(*args):
            assert main(['--server', server, *args]) == 0
            captured = capsys.readouterr()
            assert not captured.err
            return json.loads(captured.out)
        project = command('projects', 'create', '--name', 'CLI scientific study')
        assert command('projects', 'list')[0]['id'] == project['id']
        data = tmp_path / 'observations.csv'
        data.write_text('epoch,method,value,seed\n1,A,1,1\n1,A,3,2\n2,A,2,1\n2,A,4,2\n')
        source = command('sources', 'upload', project['id'], str(data))
        assert command('sources', 'list', project['id'])[0]['id'] == source['id']
        spec = tmp_path / 'spec.json'
        spec.write_text(json.dumps({'source_id': source['id'], 'x': 'epoch', 'y': 'value', 'group': 'method', 'unit_id': 'seed', 'aggregation': 'mean', 'interval': 'ci95', 'chart_type': 'line'}))
        asset = command('assets', 'create', project['id'], '--kind', 'plot', '--title', 'Real results', '--source', source['id'], '--spec', str(spec))
        current = command('assets', 'update', asset['id'], '--expected-revision', '1', '--spec', str(spec), '--title', 'Revised result')
        assert current['revision'] == 2
        job = command('assets', 'render', asset['id'], '--wait', '--scope', 'ancestors', '--wait-timeout', '30')
        assert job['status'] == 'completed'
        assert command('jobs', 'show', job['id'])['status'] == 'completed'
        assert command('jobs', 'wait', job['id'], '--wait-timeout', '1')['status'] == 'completed'
        rendered = command('assets', 'show', asset['id'])
        assert rendered['status'] == 'ready' and any(a['name'] == 'figure.png' for a in rendered['artifacts'])
        assert command('projects', 'show', project['id'])['assets'][0]['id'] == asset['id']
        backup = tmp_path / 'backup.zip'
        command('projects', 'export', project['id'], '--output', str(backup))
        with zipfile.ZipFile(backup) as archive:
            assert json.loads(archive.read('manifest.json'))['format'] == 'figloom'
            assert any(name.endswith('figure.svg') for name in archive.namelist())
        imported = command('projects', 'import', str(backup))
        assert imported['id'] != project['id']
        legacy = tmp_path / 'legacy.zip'
        with zipfile.ZipFile(backup) as source_archive, zipfile.ZipFile(legacy, 'w') as legacy_archive:
            for item in source_archive.infolist():
                content = source_archive.read(item)
                if item.filename == 'manifest.json':
                    manifest = json.loads(content)
                    manifest['format'] = 'paper-studio'
                    content = json.dumps(manifest).encode()
                legacy_archive.writestr(item, content)
        assert command('projects', 'import', str(legacy))['id'] != project['id']
        command('assets', 'export', asset['id'], '--output', str(tmp_path / 'asset.zip'))
        assert command('settings', 'show')['budget_usd'] == 0
        settings = tmp_path / 'settings.json'
        settings.write_text(json.dumps({'text': {'model': 'chosen'}, 'budget_usd': 2}))
        assert command('settings', 'configure', '--file', str(settings))['text']['model'] == 'chosen'
        assert main(['assets', 'show', asset['id'], '--server', server]) == 0
        assert json.loads(capsys.readouterr().out)['id'] == asset['id']
        assert main(['--server', server, 'assets', 'update', asset['id'], '--expected-revision', '1', '--title', 'Conflict']) == 1
        failure = capsys.readouterr()
        assert not failure.out and json.loads(failure.err)['error']['code'] == 'revision_conflict'
        assert main(['--server', server, 'projects', 'export', project['id'], '--output', str(backup)]) == 2
        assert json.loads(capsys.readouterr().err)['error']['code'] == 'output_exists'


def test_cli_wait_deadline_cancel_and_input_errors(tmp_path, capsys, monkeypatch):
    with running_server(tmp_path) as (server, app):
        app.state.jobs.close()
        project = app.state.store.create_project('Queued study', '')
        asset = app.state.store.create_asset(project['id'], 'diagram', 'Queued diagram', {})
        assert main(['--server', server, 'assets', 'render', asset['id'], '--wait', '--wait-timeout', '.03']) == 4
        assert json.loads(capsys.readouterr().err)['error']['code'] == 'wait_timeout'
        job = app.state.store.project_state(project['id'])['jobs'][0]
        assert job['status'] == 'queued'
        assert main(['--server', server, 'jobs', 'wait', job['id'], '--wait-timeout', '.03']) == 4
        waiting = json.loads(capsys.readouterr().err)
        assert waiting['error']['code'] == 'wait_timeout' and job['id'] in waiting['error']['message']
        assert len(app.state.store.project_state(project['id'])['jobs']) == 1
        with monkeypatch.context() as interrupted:
            def stop_wait(_):
                raise KeyboardInterrupt
            interrupted.setattr('figloom.cli.time.sleep', stop_wait)
            assert main(['--server', server, 'jobs', 'wait', job['id']]) == 130
        interruption = json.loads(capsys.readouterr().err)
        assert interruption['error']['code'] == 'interrupted' and job['id'] in interruption['error']['message']
        assert app.state.store.get('job', job['id'])['status'] == 'queued'
        assert main(['--server', server, 'jobs', 'cancel', job['id']]) == 0
        assert json.loads(capsys.readouterr().out)['status'] == 'cancelled'
        assert main(['--server', server, 'jobs', 'wait', job['id']]) == 3
        assert job['id'] in json.loads(capsys.readouterr().err)['error']['message']
        invalid = tmp_path / 'invalid.json'
        invalid.write_text('[1,2]')
        assert main(['--server', server, 'settings', 'configure', '--file', str(invalid)]) == 2
        assert json.loads(capsys.readouterr().err)['error']['code'] == 'invalid_input'


def test_cli_help_default_legacy_workspace_and_server_boundary(tmp_path, monkeypatch):
    monkeypatch.setenv('XDG_DATA_HOME', str(tmp_path))
    assert default_data_dir() == tmp_path / 'figloom'
    legacy = tmp_path / 'paper-studio'
    Store(legacy)
    assert default_data_dir() == legacy
    (tmp_path / 'figloom').mkdir()
    assert default_data_dir() == tmp_path / 'figloom'
    assert parser().parse_args(['serve', '--data-dir', str(tmp_path)]).data_dir == tmp_path
    for args in [['projects', 'create', '--name', 'study'], ['models', 'list'], ['settings', 'configure', '--file', 'settings.json'], ['assets', 'render', 'id', '--wait']]:
        assert parser().parse_args(args).command
    try:
        APIClient('https://external.example')
    except ClientError as error:
        assert error.exit_code == 2
    else:
        raise AssertionError('External server must not receive model keys through this local client')
