"""Shared versioned HTTP transport for Figloom command-line clients."""
from __future__ import annotations

import json
import os
from pathlib import Path
import tempfile
from urllib.parse import urlparse, urlunparse

import httpx


class ClientError(RuntimeError):
    def __init__(self, message: str, *, code: str = 'client_error', status: int | None = None, exit_code: int = 1):
        super().__init__(message)
        self.code, self.status, self.exit_code = code, status, exit_code


def load_object(path: Path) -> dict:
    if path.stat().st_size > 1_000_000:
        raise ClientError('JSON input exceeds 1 MB.', code='invalid_input', exit_code=2)
    try:
        value = json.loads(path.read_text(encoding='utf-8'), parse_constant=lambda _: (_ for _ in ()).throw(ValueError()))
    except (ValueError, UnicodeError):
        raise ClientError('Input file must contain a finite JSON object.', code='invalid_input', exit_code=2) from None
    if not isinstance(value, dict):
        raise ClientError('Input file must contain a JSON object.', code='invalid_input', exit_code=2)
    return value


class APIClient:
    def __init__(self, server='http://127.0.0.1:8008', timeout=30):
        parsed = urlparse(server)
        try:
            parsed.port
        except ValueError:
            raise ClientError('Invalid server port.', code='invalid_input', exit_code=2) from None
        if parsed.scheme not in ('http', 'https') or parsed.hostname not in ('127.0.0.1', 'localhost', '::1') or parsed.username or parsed.password or parsed.query or parsed.fragment or parsed.path.rstrip('/') not in ('', '/api/v1'):
            raise ClientError('--server must be a loopback HTTP(S) address, optionally ending in /api/v1.', code='invalid_input', exit_code=2)
        self.base = urlunparse((parsed.scheme, parsed.netloc, '/api/v1', '', '', ''))
        self.client = httpx.Client(timeout=timeout, follow_redirects=False, trust_env=False)

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.client.close()

    def _error(self, response):
        if response.is_success:
            return
        try:
            error = response.json()['error']
            if not isinstance(error['code'], str) or not isinstance(error['message'], str):
                raise ValueError
        except (ValueError, KeyError, TypeError):
            error = {'code': 'http_error', 'message': 'The server rejected the request.'}
        raise ClientError(error['message'], code=error['code'], status=response.status_code)

    def request(self, method, path, *, body=None, upload: Path | None = None):
        try:
            if upload is None:
                response = self.client.request(method, self.base + path, json=body)
            else:
                with upload.open('rb') as file:
                    response = self.client.request(method, self.base + path, files={'file': (upload.name, file)})
            self._error(response)
            return response.json()
        except httpx.RequestError:
            raise ClientError('Cannot reach Figloom. Start figloom serve or check --server.', code='connection_failed') from None
        except (ValueError, UnicodeError):
            raise ClientError('The server returned an invalid JSON response.', code='invalid_response') from None

    def download(self, path: str, output: Path, *, force=False) -> dict:
        output = output.expanduser().resolve()
        if output.exists() and not force:
            raise ClientError('Output exists. Choose another path or pass --force.', code='output_exists', exit_code=2)
        output.parent.mkdir(parents=True, exist_ok=True)
        temporary = None
        try:
            with self.client.stream('GET', self.base + path) as response:
                response.read() if not response.is_success else None
                self._error(response)
                size = 0
                with tempfile.NamedTemporaryFile(dir=output.parent, prefix='.figloom-', delete=False) as file:
                    temporary = Path(file.name)
                    for chunk in response.iter_bytes():
                        size += len(chunk)
                        if size > 134_217_728:
                            raise ClientError('Download exceeds the 128 MB export limit.', code='payload_too_large')
                        file.write(chunk)
                if output.exists() and not force:
                    raise ClientError('Output exists. Choose another path or pass --force.', code='output_exists', exit_code=2)
                os.replace(temporary, output)
                return {'path': str(output), 'bytes': size}
        except httpx.RequestError:
            raise ClientError('Export download failed. Check --server.', code='connection_failed') from None
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)
