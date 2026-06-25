"""Test Project API."""


def test_list_projects(client):
    resp = client.get("/api/projects")
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "success"
    assert "data" in data
    projects = data["data"]["projects"]
    assert len(projects) == 3
    assert all("project_id" in p for p in projects)
    assert all("source_status" in p for p in projects)


def test_get_project(client):
    resp = client.get("/api/projects/proj-001")
    assert resp.status_code == 200
    data = resp.json()
    project = data["data"]
    assert project["name"] == "MicroOA 信创迁移"
    assert project["project_status"] == "ready"
    # Verify envelope
    assert data["meta"]["source_status"] == "mock"


def test_get_nonexistent_project(client):
    resp = client.get("/api/projects/nonexistent")
    assert resp.status_code == 404


def test_create_project(client):
    resp = client.post("/api/projects", json={
        "name": "Test Project",
        "description": "A test",
        "source_type": "local_dir",
    })
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "success"
    p = data["data"]
    assert p["name"] == "Test Project"
    assert p["project_status"] == "created"
    assert p["project_id"].startswith("proj-")


def test_update_project(client):
    resp = client.patch("/api/projects/proj-001", json={"name": "Updated Name"})
    assert resp.status_code == 200
    assert resp.json()["data"]["name"] == "Updated Name"


def test_archive_project(client):
    resp = client.post("/api/projects/proj-001/archive")
    assert resp.status_code == 200
    assert resp.json()["data"]["archived"] is True
    # Verify status changed
    resp2 = client.get("/api/projects/proj-001")
    assert resp2.json()["data"]["project_status"] == "archived"


def test_response_envelope(client):
    resp = client.get("/api/projects")
    data = resp.json()
    assert "meta" in data
    assert "source_status" in data["meta"]
    assert "request_id" in data or "request_id" in data
