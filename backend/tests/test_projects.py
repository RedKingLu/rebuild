"""Test Project API — DB-backed."""


def _create_project(client, name="Test Project", description="A test",
                    source_type="local_dir", source_config=None):
    body = {"name": name, "description": description, "source_type": source_type}
    if source_config is not None:
        body["source_config"] = source_config
    resp = client.post("/api/projects", json=body)
    assert resp.status_code == 200
    return resp.json()["data"]


class TestProjectCRUD:
    """CRUD: create -> list -> get -> update -> delete."""

    def test_create_and_list(self, client):
        # Start with empty list
        resp = client.get("/api/projects")
        assert resp.status_code == 200
        data = resp.json()
        initial_count = len(data["data"]["projects"])

        # Create two projects
        p1 = _create_project(client, "Project A")
        p2 = _create_project(client, "Project B", source_type="git",
                             source_config={"remote_url": "https://example.com/repo.git"})

        assert p1["name"] == "Project A"
        assert p1["project_status"] == "created"
        assert p1["source_status"] == "real"
        assert p1["capability_status"] == "available"
        assert "project_id" in p1

        assert p2["name"] == "Project B"
        assert p2["source_type"] == "git"
        assert p2["source_config"] == {"remote_url": "https://example.com/repo.git"}

        # List should have 2 more
        resp = client.get("/api/projects")
        assert resp.status_code == 200
        projects = resp.json()["data"]["projects"]
        assert len(projects) == initial_count + 2
        ids = {p["project_id"] for p in projects}
        assert p1["project_id"] in ids
        assert p2["project_id"] in ids

    def test_get_project(self, client):
        p = _create_project(client, "MicroOA 信创迁移", description="将 .NET Framework 办公系统迁移至信创平台")
        pid = p["project_id"]

        resp = client.get(f"/api/projects/{pid}")
        assert resp.status_code == 200
        data = resp.json()
        assert data["data"]["name"] == "MicroOA 信创迁移"
        assert data["data"]["project_status"] == "created"
        assert data["data"]["project_id"] == pid

    def test_get_nonexistent_project(self, client):
        resp = client.get("/api/projects/nonexistent-999")
        assert resp.status_code == 404

    def test_update_project(self, client):
        p = _create_project(client, "Original Name")
        pid = p["project_id"]

        resp = client.patch(f"/api/projects/{pid}", json={"name": "Updated Name"})
        assert resp.status_code == 200
        assert resp.json()["data"]["name"] == "Updated Name"

        # Verify persistence
        resp = client.get(f"/api/projects/{pid}")
        assert resp.json()["data"]["name"] == "Updated Name"

    def test_delete_project(self, client):
        """DELETE endpoint soft-deletes (archives) a project."""
        p = _create_project(client, "To Delete")
        pid = p["project_id"]

        resp = client.delete(f"/api/projects/{pid}")
        assert resp.status_code == 200
        assert resp.json()["data"]["archived"] is True

        # Verify status changed to archived
        resp = client.get(f"/api/projects/{pid}")
        assert resp.json()["data"]["project_status"] == "archived"

        # Deleting nonexistent returns 404
        resp = client.delete("/api/projects/nonexistent-999")
        assert resp.status_code == 404

    def test_archive_legacy_endpoint(self, client):
        """POST /{id}/archive still works as soft-delete."""
        p = _create_project(client, "Archive Me")
        pid = p["project_id"]

        resp = client.post(f"/api/projects/{pid}/archive")
        assert resp.status_code == 200
        assert resp.json()["data"]["archived"] is True

        resp = client.get(f"/api/projects/{pid}")
        assert resp.json()["data"]["project_status"] == "archived"

    def test_list_with_query_params(self, client):
        _create_project(client, "Active A")
        _create_project(client, "Active B")
        c = _create_project(client, "Archived C")
        # Archive the third
        client.delete(f"/api/projects/{c['project_id']}")

        # List only archived
        resp = client.get("/api/projects?status=archived")
        archived = resp.json()["data"]["projects"]
        assert len(archived) >= 1
        assert all(p["project_status"] == "archived" for p in archived)

        # Sort ascending
        resp = client.get("/api/projects?sort=name&order=asc")
        names = [p["name"] for p in resp.json()["data"]["projects"]]
        assert names == sorted(names)

        # Limit
        resp = client.get("/api/projects?limit=1")
        assert len(resp.json()["data"]["projects"]) == 1

    def test_response_envelope(self, client):
        resp = client.get("/api/projects")
        data = resp.json()
        assert "meta" in data
        assert "source_status" in data["meta"]
        assert data["status"] == "success"


class TestSourceIntegrationEndpoints:
    """Git / ZIP / GitHub integration endpoints."""

    def test_configure_git_source(self, client):
        p = _create_project(client, "Git Project")
        pid = p["project_id"]

        resp = client.post(
            f"/api/projects/{pid}/integrations/git",
            params={"remote_url": "https://git.example.com/my/repo.git", "branch": "develop", "subpath": "/src"},
        )
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["source_type"] == "git"
        assert data["source_config"]["remote_url"] == "https://git.example.com/my/repo.git"
        assert data["source_config"]["branch"] == "develop"
        assert data["source_config"]["subpath"] == "/src"

    def test_configure_zip_source(self, client):
        p = _create_project(client, "ZIP Project")
        pid = p["project_id"]

        resp = client.post(
            f"/api/projects/{pid}/integrations/zip",
            params={"file_path": "/tmp/uploads/project.zip"},
        )
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["source_type"] == "zip"
        assert data["source_config"]["file_path"] == "/tmp/uploads/project.zip"

    def test_configure_github_source(self, client):
        p = _create_project(client, "GitHub Project")
        pid = p["project_id"]

        resp = client.post(
            f"/api/projects/{pid}/integrations/github",
            params={"repo_owner": "myorg", "repo_name": "migration-target", "branch": "main"},
        )
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["source_type"] == "github"
        assert data["source_config"]["repo_owner"] == "myorg"
        assert data["source_config"]["repo_name"] == "migration-target"

    def test_generic_source_update(self, client):
        p = _create_project(client, "Generic Project")
        pid = p["project_id"]

        resp = client.put(
            f"/api/projects/{pid}/source",
            json={"source_type": "manual", "source_config": {"note": "hand-crafted"}},
        )
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["source_type"] == "manual"
        assert data["source_config"] == {"note": "hand-crafted"}
