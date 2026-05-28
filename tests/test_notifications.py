import pytest


async def signup(client, email: str, full_name: str):
    return await client._get_or_create_user(email, full_name)


async def login(client, email: str, full_name: str = "Test User"):
    await client._login_user(email, full_name)


async def logout(client):
    response = await client.post("/auth/logout")
    assert response.status_code == 204
    client._logout_user()


@pytest.mark.asyncio
async def test_user_can_list_and_read_email_backed_notifications(client):
    leader = await signup(client, "notifications-leader@example.com", "Notifications Leader")
    await client._login_user(leader.email, leader.full_name)
    created = await client.post("/projects", json={"name": "Notification Project"})
    assert created.status_code == 201
    project = created.json()
    await logout(client)

    member = await signup(client, "notifications-member@example.com", "Notifications Member")
    await client._login_user(member.email, member.full_name)
    joined = await client.post("/projects/join", json={"teamy_code": project["teamy_code"]})
    assert joined.status_code == 200
    await logout(client)

    await login(client, leader.email, leader.full_name)
    task = await client.post(
        f"/projects/{project['id']}/tasks",
        json={"title": "Read the brief", "assignee_ids": [str(member.id)], "initial_status": "todo"},
    )
    assert task.status_code == 201
    await logout(client)

    await login(client, member.email, member.full_name)
    listed = await client.get("/notifications")
    assert listed.status_code == 200
    body = listed.json()
    assert body["unread_count"] == 1
    assert body["notifications"][0]["title"] == "New task assigned: Read the brief"
    assert body["notifications"][0]["is_email_backed"] is True
    assert body["notifications"][0]["target_path"] == f"/projects/{project['id']}/task-board"

    marked = await client.patch(f"/notifications/{body['notifications'][0]['id']}/read")
    assert marked.status_code == 200
    assert marked.json()["read_at"] is not None

    relisted = await client.get("/notifications")
    assert relisted.status_code == 200
    assert relisted.json()["unread_count"] == 0

