import pytest


async def signup(client, email: str, full_name: str = "Project User"):
    await client._login_user(email, full_name)


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


@pytest.mark.asyncio
async def test_leader_can_export_project_backup(client):
    leader = await client._login_user("backup-leader@example.com", "Backup Leader")
    created = await client.post("/projects", json={"name": "Backup Workspace", "description": "Portable test data"})
    assert created.status_code == 201
    project = created.json()
    await client.post("/auth/logout")

    member = await client._login_user("backup-member@example.com", "Backup Member")
    joined = await client.post("/projects/join", json={"teamy_code": project["teamy_code"]})
    assert joined.status_code == 200
    await client.post("/auth/logout")

    await client._login_user(leader.email, leader.full_name)
    task = await client.post(
        f"/projects/{project['id']}/tasks",
        json={"title": "Export task", "assignee_ids": [str(member.id)], "initial_status": "todo"},
    )
    assert task.status_code == 201
    resource = await client.post(
        f"/projects/{project['id']}/files",
        json={"kind": "link", "title": "Export link", "url": "https://example.com/export"},
    )
    assert resource.status_code == 201
    linked = await client.post(
        f"/projects/{project['id']}/tasks/{task.json()['id']}/linked-files/existing",
        json={"file_id": resource.json()["id"]},
    )
    assert linked.status_code == 201
    announcement = await client.post(f"/projects/{project['id']}/announcements", json={"title": "Export announcement", "body": "Snapshot this."})
    assert announcement.status_code == 201

    exported = await client.get(f"/projects/{project['id']}/export")

    assert exported.status_code == 200
    assert exported.headers["content-disposition"].startswith('attachment; filename="teamy-backup-workspace-')
    body = exported.json()
    assert body["format"] == "teamy_project_backup"
    assert body["schema_version"] == 1
    assert body["project"]["id"] == project["id"]
    assert {user["email"] for user in body["users"]} == {"backup-leader@example.com", "backup-member@example.com"}
    assert body["members"][0]["project_id"] == project["id"]
    assert body["tasks"][0]["title"] == "Export task"
    assert body["task_assignees"][0]["user_id"] == str(member.id)
    assert body["file_resources"][0]["url"] == "https://example.com/export"
    assert body["task_file_links"][0]["task_id"] == task.json()["id"]
    assert body["announcements"][0]["title"] == "Export announcement"
    assert body["counts"]["tasks"] == 1


@pytest.mark.asyncio
async def test_member_cannot_export_project_backup(client):
    await signup(client, "backup-owner@example.com", "Backup Owner")
    created = await client.post("/projects", json={"name": "Owner Export Only"})
    project = created.json()
    await client.post("/auth/logout")

    await signup(client, "backup-regular-member@example.com", "Backup Regular Member")
    await client.post("/projects/join", json={"teamy_code": project["teamy_code"]})

    exported = await client.get(f"/projects/{project['id']}/export")

    assert exported.status_code == 403
