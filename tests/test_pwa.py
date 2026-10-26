"""Lightweight install metadata, without a service worker or new offline logic."""
import struct
from urllib.parse import urljoin

from fastapi.testclient import TestClient
from api.app import app

client = TestClient(app)


def test_install_manifest_and_page_links():
    response = client.get('/manifest.webmanifest')
    assert response.status_code == 200
    assert 'application/manifest+json' in response.headers['content-type']
    data = response.json()
    assert data['display'] == 'standalone'
    assert data['id'] == data['scope'] == './'
    for base in ['https://example.test/', 'https://example.test/paper/']:
        assert urljoin(base, data['start_url']) == base + 'read'
        for icon in data['icons']:
            assert urljoin(base, icon['src']).startswith(base + 'static/icons/')
    for route in ['/', '/read']:
        html = client.get(route).text
        assert 'rel="manifest" href="/manifest.webmanifest"' in html
        assert 'rel="apple-touch-icon" href="/static/icons/apple-touch-icon.png"' in html
        assert 'name="apple-mobile-web-app-capable" content="yes"' in html


def test_install_icons_have_declared_png_dimensions():
    data = client.get('/manifest.webmanifest').json()
    for icon in data['icons'] + [{'src': 'static/icons/apple-touch-icon.png', 'sizes': '180x180'}]:
        response = client.get('/' + icon['src'])
        assert response.status_code == 200
        assert response.content[:8] == b'\x89PNG\r\n\x1a\n'
        assert struct.unpack('>II', response.content[16:24]) == tuple(map(int, icon['sizes'].split('x')))
