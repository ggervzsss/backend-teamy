import pytest


async def signup(client, email: str):
    response = await client.post("/auth/signup", json={"full_name": "Project User", "email": email, "password": "password123"})
    assert response.status_code == 201


@pytest.mark.asyncio
async def test_create_project_adds_creator_as_leader(client):
    await signup(client, "leader@example.com")

    response = await client.post("/projects", json={"name": "Senior Thesis", "description": "Research workspace"})

    assert response.status_code == 201
    body = response.json()
    assert body["name"] == "Senior Thesis"
    assert body["description"] == "Research workspace"
    assert body["role"] == "leader"
    assert body["member_count"] == 1
    assert body["teamy_code"].startswith("TMY-")


@pytest.mark.asyncio
async def test_list_projects_returns_only_current_user_memberships(client):
    await signup(client, "leader@example.com")
    own_project = await client.post("/projects", json={"name": "Owned Project"})
    assert own_project.status_code == 201
    await client.post("/auth/logout")

    await signup(client, "other@example.com")
    other_project = await client.post("/projects", json={"name": "Other Project"})
    assert other_project.status_code == 201

    response = await client.get("/projects")

    assert response.status_code == 200
    projects = response.json()["projects"]
    assert [project["name"] for project in projects] == ["Other Project"]


@pytest.mark.asyncio
async def test_join_valid_code_adds_member_and_is_idempotent(client):
    await signup(client, "leader@example.com")
    created = await client.post("/projects", json={"name": "Shared Project"})
    teamy_code = created.json()["teamy_code"]
    await client.post("/auth/logout")

    await signup(client, "member@example.com")
    first_join = await client.post("/projects/join", json={"teamy_code": teamy_code.lower()})
    second_join = await client.post("/projects/join", json={"teamy_code": teamy_code})

    assert first_join.status_code == 200
    assert second_join.status_code == 200
    assert first_join.json()["role"] == "member"
    assert second_join.json()["member_count"] == 2


@pytest.mark.asyncio
async def test_join_invalid_code_returns_404(client):
    await signup(client, "member@example.com")

    response = await client.post("/projects/join", json={"teamy_code": "TMY-NOPE-404"})

    assert response.status_code == 404


@pytest.mark.asyncio
async def test_non_member_cannot_fetch_project(client):
    await signup(client, "leader@example.com")
    created = await client.post("/projects", json={"name": "Private Project"})
    project_id = created.json()["id"]
    await client.post("/auth/logout")

    await signup(client, "outsider@example.com")
    response = await client.get(f"/projects/{project_id}")

    assert response.status_code == 404


@pytest.mark.asyncio
async def test_leader_can_rename_archive_and_delete_project(client):
    await signup(client, "leader@example.com")
    created = await client.post("/projects", json={"name": "Original Workspace"})
    project_id = created.json()["id"]

    renamed = await client.patch(f"/projects/{project_id}", json={"name": "Renamed Workspace"})
    archived = await client.post(f"/projects/{project_id}/archive", json={"confirm_archive": True})
    blocked_write = await client.post(f"/projects/{project_id}/announcements", json={"title": "Update", "body": "Archived now"})
    deleted = await client.request("DELETE", f"/projects/{project_id}", json={"confirm_name": "Renamed Workspace"})
    missing = await client.get(f"/projects/{project_id}")

    assert renamed.status_code == 200
    assert renamed.json()["name"] == "Renamed Workspace"
    assert archived.status_code == 200
    assert archived.json()["archived_at"] is not None
    assert blocked_write.status_code == 409
    assert deleted.status_code == 204
    assert missing.status_code == 404


@pytest.mark.asyncio
async def test_member_cannot_manage_project_settings(client):
    await signup(client, "leader@example.com")
    created = await client.post("/projects", json={"name": "Owner Only"})
    project_id = created.json()["id"]
    teamy_code = created.json()["teamy_code"]
    await client.post("/auth/logout")

    await signup(client, "member@example.com")
    await client.post("/projects/join", json={"teamy_code": teamy_code})

    rename = await client.patch(f"/projects/{project_id}", json={"name": "Member Rename"})
    archive = await client.post(f"/projects/{project_id}/archive", json={"confirm_archive": True})
    delete = await client.request("DELETE", f"/projects/{project_id}", json={"confirm_name": "Owner Only"})

    assert rename.status_code == 403
    assert archive.status_code == 403
    assert delete.status_code == 403
