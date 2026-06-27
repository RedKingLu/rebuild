"""R8 Workspace endpoint tests — file rw, source RO, path traversal, execute,
sessions, and Environment Profile (D-051). Closes R8-4 P1-02 coverage gap."""

import pytest


@pytest.fixture
def project_id(client):
    resp = client.post("/api/projects", json={
        "name": "R8 Endpoint Test", "source_type": "local_dir",
    })
    assert resp.status_code == 200
    return resp.json()["data"]["project_id"]


# ── File read/write + boundary ──────────────────────────────────────────

def test_write_and_read_material_file(client, project_id):
    w = client.put(f"/api/projects/{project_id}/file",
                   json={"path": "materials/note.md", "content": "# hi\nR8"})
    assert w.status_code == 200
    r = client.get(f"/api/projects/{project_id}/file", params={"path": "materials/note.md"})
    assert r.status_code == 200
    d = r.json()["data"]
    assert d["content"] == "# hi\nR8"
    assert d["editable"] is True and d["readonly"] is False


def test_source_dir_write_denied(client, project_id):
    w = client.put(f"/api/projects/{project_id}/file",
                   json={"path": "source/app.py", "content": "x=1"})
    assert w.status_code == 403


@pytest.mark.parametrize("bad", ["../../../tmp/evil.txt", "../../../../etc/passwd"])
def test_path_traversal_write_denied(client, project_id, bad):
    w = client.put(f"/api/projects/{project_id}/file", json={"path": bad, "content": "x"})
    assert w.status_code == 403


def test_path_traversal_read_denied(client, project_id):
    r = client.get(f"/api/projects/{project_id}/file", params={"path": "../../../../etc/passwd"})
    assert r.status_code == 403


# ── Terminal execute + deny-list ─────────────────────────────────────────

def test_execute_python_ok(client, project_id):
    resp = client.post(f"/api/projects/{project_id}/execute",
                       json={"command": "print(40+2)", "language": "python", "timeout": 20})
    assert resp.status_code == 200
    d = resp.json()["data"]
    assert d["exit_code"] == 0 and "42" in d["stdout"]
    assert d.get("session_id")


@pytest.mark.parametrize("cmd", ["rm -rf /tmp/x", "cat .env"])
def test_execute_denylist_blocked(client, project_id, cmd):
    resp = client.post(f"/api/projects/{project_id}/execute",
                       json={"command": cmd, "language": "shell"})
    assert resp.status_code == 200
    assert resp.json()["data"]["blocked"] is True


def test_sessions_listed_after_execute(client, project_id):
    client.post(f"/api/projects/{project_id}/execute",
                json={"command": "print(1)", "language": "python"})
    s = client.get(f"/api/projects/{project_id}/sessions")
    assert s.status_code == 200
    assert len(s.json()["data"]["sessions"]) >= 1


# ── Environment Profile (D-051, WP-A) ────────────────────────────────────

def test_environment_default_profile(client, project_id):
    r = client.get(f"/api/projects/{project_id}/environment")
    assert r.status_code == 200
    d = r.json()["data"]
    assert d["project_id"] == project_id
    assert d["status"] == "unknown" and d["env_kind"] == "local"
    assert d["source"] == "default"


def test_environment_update_persists(client, project_id):
    u = client.put(f"/api/projects/{project_id}/environment",
                   json={"status": "declared", "env_kind": "remote",
                         "language_hint": "java", "source": "user"})
    assert u.status_code == 200
    d = u.json()["data"]
    assert d["status"] == "declared" and d["env_kind"] == "remote"
    assert d["language_hint"] == "java" and d["source"] == "user"
    # re-read confirms persistence
    r = client.get(f"/api/projects/{project_id}/environment")
    assert r.json()["data"]["status"] == "declared"


def test_environment_rejects_invalid_enum(client, project_id):
    bad = client.put(f"/api/projects/{project_id}/environment", json={"status": "bogus"})
    assert bad.status_code == 400


def test_environment_404_for_unknown_project(client):
    r = client.get("/api/projects/does-not-exist/environment")
    assert r.status_code == 404
