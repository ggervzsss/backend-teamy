import pytest


PASSWORD = "password123"


async def signup(client, email: str, full_name: str):
    response = await client.post("/auth/signup", json={"full_name": full_name, "email": email, "password": PASSWORD})
    assert response.status_code == 201
    return response.json()["user"]


async def login(client, email: str):
    response = await client.post("/auth/login", json={"email": email, "password": PASSWORD})
    assert response.status_code == 200
    return response.json()["user"]


async def logout(client):
    response = await client.post("/auth/logout")
    assert response.status_code == 204


async def setup_project(client):
    leader = await signup(client, "files-leader@example.com", "Files Leader")
    created = await client.post("/projects", json={"name": "File Project"})
    assert created.status_code == 201
    project = created.json()
    await logout(client)

    member = await signup(client, "files-member@example.com", "Files Member")
    joined = await client.post("/projects/join", json={"teamy_code": project["teamy_code"]})
    assert joined.status_code == 200
    await logout(client)

    await login(client, leader["email"])
    return project, leader, member


@pytest.mark.asyncio
async def test_project_members_can_create_list_open_and_update_docs(client):
    project, _, member = await setup_project(client)
    await logout(client)
    await login(client, member["email"])

    created = await client.post(
        f"/projects/{project['id']}/files",
        json={
            "kind": "doc",
            "title": "Research Notes",
            "content_html": '<h1 style="color: red; position: absolute">Heading</h1><script>alert(1)</script><p><strong>Keep me</strong></p>',
        },
    )

    assert created.status_code == 201
    body = created.json()
    assert body["kind"] == "doc"
    assert "<script>" not in body["content_html"]
    assert "position" not in body["content_html"]
    assert "<strong>Keep me</strong>" in body["content_html"]

    listed = await client.get(f"/projects/{project['id']}/files")
    assert listed.status_code == 200
    assert listed.json()["files"][0]["title"] == "Research Notes"

    opened = await client.get(f"/projects/{project['id']}/files/{body['id']}")
    assert opened.status_code == 200
    assert opened.json()["content_html"] == body["content_html"]

    updated = await client.patch(
        f"/projects/{project['id']}/files/{body['id']}",
        json={"title": "Updated Notes", "content_html": '<p><a href="javascript:alert(1)">bad</a><u>Underlined</u></p>'},
    )
    assert updated.status_code == 200
    assert updated.json()["title"] == "Updated Notes"
    assert "javascript:" not in updated.json()["content_html"]
    assert "<u>Underlined</u>" in updated.json()["content_html"]


@pytest.mark.asyncio
async def test_project_members_can_create_links(client):
    project, _, _ = await setup_project(client)

    created = await client.post(
        f"/projects/{project['id']}/files",
        json={"kind": "link", "title": "Drive Folder", "url": "https://drive.google.com/example"},
    )

    assert created.status_code == 201
    body = created.json()
    assert body["kind"] == "link"
    assert body["url"] == "https://drive.google.com/example"
    assert body["content_html"] is None


@pytest.mark.asyncio
async def test_project_members_can_delete_resources_created_by_others(client):
    project, _, member = await setup_project(client)

    created = await client.post(
        f"/projects/{project['id']}/files",
        json={"kind": "link", "title": "Shared Spec", "url": "https://example.com/spec"},
    )
    assert created.status_code == 201
    file_id = created.json()["id"]

    await logout(client)
    await login(client, member["email"])

    deleted = await client.delete(f"/projects/{project['id']}/files/{file_id}")
    assert deleted.status_code == 204

    listed = await client.get(f"/projects/{project['id']}/files")
    assert listed.status_code == 200
    assert listed.json()["files"] == []

    opened = await client.get(f"/projects/{project['id']}/files/{file_id}")
    assert opened.status_code == 404


@pytest.mark.asyncio
async def test_non_members_cannot_access_file_hub(client):
    project, _, _ = await setup_project(client)
    await logout(client)
    await signup(client, "outside-filehub@example.com", "Outside User")

    response = await client.get(f"/projects/{project['id']}/files")

    assert response.status_code == 404


@pytest.mark.asyncio
async def test_creating_task_with_teamy_doc_links_file(client):
    project, _, member = await setup_project(client)

    response = await client.post(
        f"/projects/{project['id']}/tasks",
        json={
            "title": "Draft proposal",
            "assignee_ids": [member["id"]],
            "linked_file": {"mode": "doc", "title": "Proposal Doc"},
        },
    )

    assert response.status_code == 201
    body = response.json()
    assert body["linked_files"][0]["title"] == "Proposal Doc"
    assert body["linked_files"][0]["kind"] == "doc"

    listed = await client.get(f"/projects/{project['id']}/files")
    assert listed.status_code == 200
    assert listed.json()["files"][0]["linked_tasks"][0]["id"] == body["id"]


@pytest.mark.asyncio
async def test_creating_task_with_external_link_links_file(client):
    project, _, member = await setup_project(client)

    response = await client.post(
        f"/projects/{project['id']}/tasks",
        json={
            "title": "Collect references",
            "assignee_ids": [member["id"]],
            "linked_file": {"mode": "link", "title": "Source Folder", "url": "https://example.com/sources"},
        },
    )

    assert response.status_code == 201
    body = response.json()
    assert body["linked_files"][0]["title"] == "Source Folder"
    assert body["linked_files"][0]["kind"] == "link"
    assert body["linked_files"][0]["url"] == "https://example.com/sources"


@pytest.mark.asyncio
async def test_project_members_can_link_existing_resource_to_task(client):
    project, _, member = await setup_project(client)
    await logout(client)
    other_member = await signup(client, "files-other-member@example.com", "Files Other Member")
    joined = await client.post("/projects/join", json={"teamy_code": project["teamy_code"]})
    assert joined.status_code == 200
    await logout(client)
    await login(client, "files-leader@example.com")

    resource = await client.post(
        f"/projects/{project['id']}/files",
        json={"kind": "link", "title": "Existing Brief", "url": "https://example.com/brief"},
    )
    assert resource.status_code == 201

    task = await client.post(
        f"/projects/{project['id']}/tasks",
        json={"title": "Review brief", "assignee_ids": [member["id"]], "priority": "medium", "initial_status": "todo"},
    )
    assert task.status_code == 201
    await logout(client)
    await login(client, other_member["email"])

    linked = await client.post(
        f"/projects/{project['id']}/tasks/{task.json()['id']}/linked-files/existing",
        json={"file_id": resource.json()["id"]},
    )

    assert linked.status_code == 201
    body = linked.json()
    assert body["linked_files"][0]["id"] == resource.json()["id"]
    assert body["linked_files"][0]["title"] == "Existing Brief"

    relinked = await client.post(
        f"/projects/{project['id']}/tasks/{task.json()['id']}/linked-files/existing",
        json={"file_id": resource.json()["id"]},
    )
    assert relinked.status_code == 201
    assert len(relinked.json()["linked_files"]) == 1

    listed = await client.get(f"/projects/{project['id']}/files")
    assert listed.status_code == 200
    assert listed.json()["files"][0]["linked_tasks"][0]["id"] == task.json()["id"]
