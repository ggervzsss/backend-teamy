import json
from uuid import uuid4

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


def make_backup_payload(project_id=None, leader_id=None, task_id=None, resource_id=None, link_id=None, assignee_id=None, announcement_id=None):
    project_id = project_id or uuid4()
    leader_id = leader_id or uuid4()
    task_id = task_id or uuid4()
    resource_id = resource_id or uuid4()
    link_id = link_id or uuid4()
    assignee_id = assignee_id or uuid4()
    announcement_id = announcement_id or uuid4()
    created_at = "2026-05-29T00:00:00+00:00"
    return {
        "format": "teamy_project_backup",
        "schema_version": 1,
        "exported_at": created_at,
        "exported_by_user_id": str(leader_id),
        "project": {
            "id": str(project_id),
            "name": "Imported Production Copy",
            "description": "Copied from production",
            "teamy_code": "TMY-IMPT-001",
            "created_by_user_id": str(leader_id),
            "archived_at": None,
            "created_at": created_at,
            "updated_at": created_at,
        },
        "users": [
            {
                "id": str(leader_id),
                "email": "prod-leader@example.com",
                "full_name": "Prod Leader",
                "username": "prodleader",
                "auth_provider": "google",
                "avatar_url": None,
                "google_avatar_url": None,
                "last_online_at": None,
                "created_at": created_at,
                "updated_at": created_at,
            }
        ],
        "members": [
            {
                "id": str(uuid4()),
                "project_id": str(project_id),
                "user_id": str(leader_id),
                "role": "leader",
                "nickname": None,
                "joined_at": created_at,
            }
        ],
        "tasks": [
            {
                "id": str(task_id),
                "project_id": str(project_id),
                "title": "Imported task",
                "description": "Verify imported task data.",
                "start_date": "2026-05-29",
                "due_date": "2026-06-01",
                "status": "in_progress",
                "is_record_only": False,
                "is_private": False,
                "personal_kind": "task",
                "created_by_user_id": str(leader_id),
                "reviewed_by_user_id": None,
                "reviewed_at": None,
                "review_remarks": None,
                "created_at": created_at,
                "updated_at": created_at,
            }
        ],
        "task_assignees": [
            {
                "id": str(assignee_id),
                "task_id": str(task_id),
                "user_id": str(leader_id),
                "status": "in_progress",
                "completed_at": None,
                "created_at": created_at,
                "updated_at": created_at,
            }
        ],
        "file_resources": [
            {
                "id": str(resource_id),
                "project_id": str(project_id),
                "title": "Imported resource",
                "kind": "link",
                "url": "https://example.com/imported",
                "content_html": None,
                "created_by_user_id": str(leader_id),
                "created_at": created_at,
                "updated_at": created_at,
            }
        ],
        "task_file_links": [
            {
                "id": str(link_id),
                "task_id": str(task_id),
                "file_resource_id": str(resource_id),
                "created_at": created_at,
            }
        ],
        "announcements": [
            {
                "id": str(announcement_id),
                "project_id": str(project_id),
                "title": "Imported announcement",
                "body": "Production announcement copy.",
                "is_pinned": True,
                "deadline_date": None,
                "deadline_done_at": None,
                "is_record_only": False,
                "created_by_user_id": str(leader_id),
                "created_at": created_at,
                "updated_at": created_at,
            }
        ],
        "announcement_reads": [],
        "notifications": [],
    }


@pytest.mark.asyncio
async def test_authenticated_user_can_import_project_backup(client):
    await signup(client, "dev-importer@example.com", "Dev Importer")
    backup = make_backup_payload()

    imported = await client.post(
        "/projects/import",
        files={"backup": ("teamy-backup.json", json.dumps(backup), "application/json")},
    )

    assert imported.status_code == 201
    body = imported.json()
    assert body["id"] == backup["project"]["id"]
    assert body["name"] == "Imported Production Copy"
    assert body["role"] == "leader"
    assert body["member_count"] == 2

    tasks = await client.get(f"/projects/{body['id']}/tasks")
    assert tasks.status_code == 200
    assert tasks.json()["tasks"][0]["id"] == backup["tasks"][0]["id"]
    assert tasks.json()["tasks"][0]["linked_files"][0]["id"] == backup["file_resources"][0]["id"]

    files = await client.get(f"/projects/{body['id']}/files")
    assert files.status_code == 200
    assert files.json()["files"][0]["linked_tasks"][0]["id"] == backup["tasks"][0]["id"]

    announcements = await client.get(f"/projects/{body['id']}/announcements")
    assert announcements.status_code == 200
    assert announcements.json()["announcements"][0]["title"] == "Imported announcement"

    duplicate = await client.post(
        "/projects/import",
        files={"backup": ("teamy-backup.json", json.dumps(backup), "application/json")},
    )
    assert duplicate.status_code == 409


@pytest.mark.asyncio
async def test_project_import_remaps_existing_user_by_email(client):
    importer = await client._login_user("prod-leader@example.com", "Local Dev Leader")
    backup_user_id = uuid4()
    backup = make_backup_payload(project_id=uuid4(), leader_id=backup_user_id)

    imported = await client.post(
        "/projects/import",
        files={"backup": ("teamy-backup.json", json.dumps(backup), "application/json")},
    )

    assert imported.status_code == 201
    body = imported.json()
    assert body["member_count"] == 1

    tasks = await client.get(f"/projects/{body['id']}/tasks")
    assert tasks.status_code == 200
    assignees = tasks.json()["tasks"][0]["assignees"]
    assert assignees[0]["user"]["id"] == str(importer.id)
    assert assignees[0]["user"]["id"] != str(backup_user_id)
