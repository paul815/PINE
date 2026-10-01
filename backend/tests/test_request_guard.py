"""The two checks that stand between a loopback server and the open web.

PINE binds 127.0.0.1, which is not the same thing as being unreachable from a
web page. Two ways in exist, and CORS closes neither:

  * DNS rebinding. A page on evil.com re-points its own name at 127.0.0.1 and
    waits out the TTL. From then on ``http://evil.com:5000/api/...`` *is* the
    page's own origin, so no CORS check is ever made and every transcript in
    the app is readable. The give-away is the Host header: it says evil.com.

  * CSRF. Cross-origin JSON needs a preflight, so most of the API is out of
    reach — but multipart uploads and POSTs that act on their URL alone are
    "simple requests" that browsers send without asking. The reply is
    unreadable; the upload still happened. The give-away is the Origin header.

These tests pin both, and — just as important — pin the traffic that must keep
working: every loopback spelling of this server, and every non-browser caller.
"""

import io

import pytest

from app import _hostname_of, _is_local_hostname


# ── The Host check ───────────────────────────────────────────────────────────

@pytest.mark.parametrize('base_url', [
    'http://pine.localhost:5000',   # what the launcher opens
    'http://127.0.0.1:5000',        # what a user types
    'http://localhost:5000',        # what a user types more often
    'http://[::1]:5000',            # same machine, other stack
    'http://localhost',             # no port at all (the test client's default)
])
def test_every_loopback_spelling_is_served(client, base_url):
    assert client.get('/api/projects', base_url=base_url).status_code == 200


def test_a_rebound_dns_name_is_refused(client):
    """The rebinding case: right address, someone else's name."""
    response = client.get('/api/projects', base_url='http://evil.com:5000')

    assert response.status_code == 403
    assert response.get_json()['error'] == 'Invalid host'


def test_a_rebound_name_cannot_reach_the_route_at_all(client):
    """Refusal happens in before_request, so the handler never runs."""
    blocked = client.post('/api/projects', json={'name': 'Stolen'},
                          base_url='http://evil.com:5000')
    assert blocked.status_code == 403

    assert client.get('/api/projects').get_json()['active'] == []


def test_an_operator_can_vouch_for_another_host(client, monkeypatch):
    """PINE_ALLOWED_HOSTS is the escape hatch for a reverse proxy."""
    assert client.get('/api/projects',
                      base_url='http://pine.example.com').status_code == 403

    monkeypatch.setenv('PINE_ALLOWED_HOSTS', 'pine.example.com, other.internal')

    assert client.get('/api/projects',
                      base_url='http://pine.example.com').status_code == 200


# ── The Origin check ─────────────────────────────────────────────────────────

def test_a_cross_site_post_is_refused(client):
    response = client.post('/api/projects', json={'name': 'Forged'},
                           headers={'Origin': 'https://evil.com'})

    assert response.status_code == 403
    assert response.get_json()['error'] == 'Cross-site request refused'
    assert client.get('/api/projects').get_json()['active'] == []


def test_a_cross_site_upload_is_refused(client):
    """The one CSRF vector CORS lets through: multipart needs no preflight."""
    project_id = client.post('/api/projects', json={'name': 'Notes'}).get_json()['id']

    response = client.post(
        f'/api/projects/{project_id}/attachments',
        data={'file': (io.BytesIO(b'payload'), 'planted.txt')},
        content_type='multipart/form-data',
        headers={'Origin': 'https://evil.com'},
    )

    assert response.status_code == 403
    assert client.get(f'/api/projects/{project_id}/attachments').get_json() == []


@pytest.mark.parametrize('origin', [
    'http://localhost',             # the page this server served
    'http://pine.localhost:5000',   # ...under the launcher's name
    'http://127.0.0.1:5000',        # ...under the numeric one
])
def test_the_apps_own_origins_are_served(client, origin):
    response = client.post('/api/projects', json={'name': 'Real'},
                           headers={'Origin': origin})

    assert response.status_code == 201


def test_a_caller_without_an_origin_is_served(client):
    """urllib, the launcher, curl: no Origin header, so no browser, no CSRF."""
    assert client.post('/api/projects', json={'name': 'Scripted'}).status_code == 201


def test_a_cross_site_read_is_left_to_cors(client):
    """GET is unchanged: the browser already cannot read the reply."""
    response = client.get('/api/projects', headers={'Origin': 'https://evil.com'})

    assert response.status_code == 200


# ── The host-parsing helpers ─────────────────────────────────────────────────

@pytest.mark.parametrize('authority,expected', [
    ('127.0.0.1:5000', '127.0.0.1'),
    ('pine.localhost', 'pine.localhost'),
    ('[::1]:5001', '::1'),
    ('[::1]', '::1'),
    ('', ''),
])
def test_hostname_of_drops_the_port(authority, expected):
    assert _hostname_of(authority) == expected


@pytest.mark.parametrize('hostname,expected', [
    ('127.0.0.1', True),
    ('LocalHost', True),
    ('pine.localhost', True),
    ('::1', True),
    ('evil.com', False),
    ('localhost.evil.com', False),   # the suffix trick
    ('', False),
])
def test_only_names_that_cannot_move_count_as_local(hostname, expected):
    assert _is_local_hostname(hostname) is expected
