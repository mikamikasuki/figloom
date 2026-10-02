from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import threading

from fastapi.testclient import TestClient
import pytest

from figloom.api import create_app
from figloom.providers.client import ModelClient, normalize_endpoint_base


@contextmanager
def metadata_server():
    seen = []
    state = {'status': 200, 'payload': {'data': [{'id': 'actual-model'}, {'id': 'another-model'}]}}
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            seen.append({'method': 'GET', 'path': self.path, 'authorization': self.headers.get('authorization')})
            encoded = json.dumps(state['payload']).encode()
            self.send_response(state['status'])
            self.send_header('Content-Type', 'application/json')
            self.send_header('Content-Length', str(len(encoded)))
            self.end_headers()
            self.wfile.write(encoded)
        def do_POST(self):
            seen.append({'method': 'POST', 'path': self.path})
            self.send_error(405)
        def log_message(self, *_): pass
    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f'http://127.0.0.1:{server.server_port}', state, seen
    finally:
        server.shutdown()
        server.server_close()
        thread.join(2)


@pytest.mark.parametrize('url,api,expected', [
    ('https://api.example.com', 'responses', 'https://api.example.com/v1'),
    ('https://api.example.com/v1/', 'responses', 'https://api.example.com/v1'),
    ('https://api.example.com/v1/responses', 'responses', 'https://api.example.com/v1'),
    ('https://api.example.com/v1/chat/completions', 'chat_completions', 'https://api.example.com/v1'),
    ('https://api.example.com/api/models', 'chat_completions', 'https://api.example.com/api'),
    ('https://api.example.com/models', 'chat_completions', 'https://api.example.com'),
    ('https://api.example.com/v1/images/generations', 'images', 'https://api.example.com/v1'),
    ('http://127.0.0.1:11434/api/chat', 'ollama', 'http://127.0.0.1:11434'),
    ('http://127.0.0.1:11434/api/tags', 'ollama', 'http://127.0.0.1:11434'),
])
def test_endpoint_base_accepts_full_operation_urls(url, api, expected):
    assert normalize_endpoint_base(url, api) == expected


def test_actual_metadata_http_safe_errors_and_unsaved_overrides(tmp_path):
    app = create_app(tmp_path / 'workspace', start_worker=False)
    with metadata_server() as (url, state, seen), TestClient(app, base_url='http://127.0.0.1:8008') as client:
        settings = {'text': {'base_url': url + '/v1', 'model': 'actual-model', 'api': 'chat_completions', 'api_key': 'private-key'}, 'budget_usd': 0}
        assert client.put('/api/v1/settings', json=settings).status_code == 200
        before = app.state.store.settings()
        models = client.post('/api/v1/providers/text/models', json={'config': {'base_url': url + '/v1/responses', 'api_key': ''}})
        assert models.status_code == 200, models.text
        assert models.json()['models'] == [{'id': 'actual-model'}, {'id': 'another-model'}]
        assert seen[-1]['path'] == '/v1/models' and seen[-1]['authorization'] == 'Bearer private-key'
        tested = client.post('/api/v1/providers/text/test', json={}).json()
        assert tested['reachable'] and tested['model_available'] and tested['model'] == 'actual-model'
        unavailable = client.post('/api/v1/providers/image/test', json={'config': {'base_url': url, 'model': 'missing', 'api': 'images'}}).json()
        assert unavailable['reachable'] and not unavailable['model_available']
        assert app.state.store.settings() == before
        assert client.get('/api/v1/settings').json()['spent'] == 0
        state['payload'] = {'models': [{'name': 'qwen2.5:3b'}]}
        ollama = client.post('/api/v1/providers/text/models', json={'config': {'base_url': url + '/api/chat', 'api': 'ollama', 'clear_api_key': True}})
        assert ollama.json()['models'] == [{'id': 'qwen2.5:3b'}]
        assert seen[-1]['path'] == '/api/tags' and seen[-1]['authorization'] is None
        for status, code, api_status in [(401, 'provider_authentication_failed', 502), (404, 'provider_endpoint_not_found', 502), (502, 'provider_unavailable', 503)]:
            state.update(status=status, payload={'error': 'private-key /Users/private/private-body'})
            error = client.post('/api/v1/providers/text/test', json={})
            assert error.status_code == api_status and error.json()['error']['code'] == code
            assert error.json()['request_id'] == error.headers['x-request-id']
            assert 'private-key' not in error.text and 'private-body' not in error.text
        state.update(status=200, payload={'data': None})
        error = client.post('/api/v1/providers/text/models', json={})
        assert error.status_code == 502 and error.json()['error']['code'] == 'provider_invalid_response'
        assert all(request['method'] == 'GET' for request in seen)
        assert client.post('/api/v1/providers/not-valid/models', json={}).status_code == 422
        assert client.post('/api/v1/providers/text/models', json={'config': None}).status_code == 422


def test_changed_endpoint_does_not_send_saved_provider_key(tmp_path):
    app = create_app(tmp_path / 'workspace', start_worker=False)
    with metadata_server() as (old, _, _), metadata_server() as (new, _, seen), TestClient(app, base_url='http://127.0.0.1:8008') as client:
        client.put('/api/v1/settings', json={'text': {'base_url': old, 'model': 'actual-model', 'api_key': 'old-private-key'}})
        probe = client.post('/api/v1/providers/text/test', json={'config': {'base_url': new, 'api_key': ''}})
        assert probe.status_code == 200 and seen[-1]['authorization'] is None
        assert app.state.store.settings()['text']['api_key'] == 'old-private-key'
        probe = client.post('/api/v1/providers/text/test', json={'config': {'base_url': new, 'api_key': 'new-private-key'}})
        assert probe.status_code == 200 and seen[-1]['authorization'] == 'Bearer new-private-key'
        updated = client.put('/api/v1/settings', json={'text': {'base_url': new}})
        assert updated.status_code == 200 and not updated.json()['text']['has_api_key']


def test_same_host_different_prefix_and_explicit_image_key_scope(tmp_path):
    from figloom.engine import make_client
    app = create_app(tmp_path / 'workspace', start_worker=False)
    with metadata_server() as (url, _, seen), TestClient(app, base_url='http://127.0.0.1:8008') as client:
        client.put('/api/v1/settings', json={'text': {'base_url': url + '/v1', 'model': 'actual-model', 'api_key': 'text-private-key'}})
        probe = client.post('/api/v1/providers/text/test', json={'config': {'base_url': url + '/other/v1'}})
        assert probe.status_code == 200 and seen[-1]['authorization'] is None
        image = client.post('/api/v1/providers/image/test', json={'config': {'model': 'actual-model'}})
        assert image.status_code == 200 and seen[-1]['path'] == '/v1/models' and seen[-1]['authorization'] == 'Bearer text-private-key'
        explicit = client.post('/api/v1/providers/image/test', json={'config': {'base_url': url + '/v1', 'model': 'actual-model', 'clear_api_key': True}})
        assert explicit.status_code == 200 and seen[-1]['authorization'] is None
        settings = app.state.store.settings()
        settings['image'] = {'base_url': url + '/v1', 'model': 'actual-model'}
        actual = make_client(settings, guard=lambda _: None)
        assert actual.image_client.key == ''
        settings['image'].pop('base_url')
        actual = make_client(settings, guard=lambda _: None)
        assert actual.image_client.key == 'text-private-key'
        settings['image'] = {'base_url': 'https://different.example/v1', 'model': 'actual-model'}
        actual = make_client(settings, guard=lambda _: None)
        assert actual.image_client.key == ''


def test_full_text_endpoint_normalizes_real_generation_transport():
    client = ModelClient({'base_url': 'http://127.0.0.1:1234/v1/chat/completions', 'kind': 'openai_compatible', 'model': 'test', 'config': {'api': 'chat_completions'}})
    endpoint, _ = client.build_request([{'role': 'user', 'content': 'Hello'}])
    assert client.base + endpoint == 'http://127.0.0.1:1234/v1/chat/completions'
