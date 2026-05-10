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


async def setup_project_with_members(client):
    leader = await signup(client, "leader@example.com", "Leader User")
    created = await client.post("/projects", json={"name": "Task Project"})
    assert created.status_code == 201
    project = created.json()
    await logout(client)

    member_one = await signup(client, "member-one@example.com", "Member One")
    joined = await client.post("/projects/join", json={"teamy_code": project["teamy_code"]})
    assert joined.status_code == 200
    await logout(client)

    member_two = await signup(client, "member-two@example.com", "Member Two")
    joined = await client.post("/projects/join", json={"teamy_code": project["teamy_code"]})
    assert joined.status_code == 200
    await logout(client)

    await login(client, leader["email"])
    return project, leader, member_one, member_two


@pytest.mark.asyncio
async def test_leader_can_create_task_assigned_to_multiple_members(client):
    project, _, member_one, member_two = await setup_project_with_members(client)

    response = await client.post(
        f"/projects/{project['id']}/tasks",
        json={
            "title": "Write report",
            "description": "Draft the final report",
            "assignee_ids": [member_one["id"], member_two["id"]],
            "priority": "high",
            "due_date": "2026-05-20",
            "initial_status": "todo",
        },
    )

    assert response.status_code == 201
    body = response.json()
    assert body["title"] == "Write report"
    assert body["status"] == "todo"
    assert body["priority"] == "high"
    assert body["due_date"] == "2026-05-20"
    assert {assignee["user"]["id"] for assignee in body["assignees"]} == {member_one["id"], member_two["id"]}


@pytest.mark.asyncio
async def test_non_leader_cannot_create_or_review_tasks(client):
    project, _, member_one, _ = await setup_project_with_members(client)
    await logout(client)
    await login(client, member_one["email"])

    create_response = await client.post(
        f"/projects/{project['id']}/tasks",
        json={"title": "Member task", "assignee_ids": [member_one["id"]]},
    )

    assert create_response.status_code == 403


@pytest.mark.asyncio
async def test_member_completion_moves_to_review_only_after_all_assignees_finish(client):
    project, leader, member_one, member_two = await setup_project_with_members(client)
    created = await client.post(
        f"/projects/{project['id']}/tasks",
        json={"title": "Build prototype", "assignee_ids": [member_one["id"], member_two["id"]], "initial_status": "in_progress"},
    )
    task_id = created.json()["id"]
    await logout(client)

    await login(client, member_one["email"])
    first_update = await client.patch(f"/projects/{project['id']}/tasks/{task_id}/assignees/me", json={"status": "done"})
    assert first_update.status_code == 200
    assert first_update.json()["status"] == "in_progress"
    await logout(client)

    await login(client, member_two["email"])
    second_update = await client.patch(f"/projects/{project['id']}/tasks/{task_id}/assignees/me", json={"status": "done"})
    assert second_update.status_code == 200
    assert second_update.json()["status"] == "for_review"
    await logout(client)

    await login(client, leader["email"])
    approved = await client.post(f"/projects/{project['id']}/tasks/{task_id}/review", json={"action": "approve"})
    assert approved.status_code == 200
    assert approved.json()["status"] == "done"
    assert approved.json()["reviewed_by"]["id"] == leader["id"]


@pytest.mark.asyncio
async def test_request_changes_resets_assignees_to_progress(client):
    project, leader, member_one, member_two = await setup_project_with_members(client)
    created = await client.post(
        f"/projects/{project['id']}/tasks",
        json={"title": "Revise chapter", "assignee_ids": [member_one["id"], member_two["id"]], "initial_status": "in_progress"},
    )
    task_id = created.json()["id"]
    await logout(client)

    await login(client, member_one["email"])
    await client.patch(f"/projects/{project['id']}/tasks/{task_id}/assignees/me", json={"status": "done"})
    await logout(client)
    await login(client, member_two["email"])
    await client.patch(f"/projects/{project['id']}/tasks/{task_id}/assignees/me", json={"status": "done"})
    await logout(client)

    await login(client, leader["email"])
    reviewed = await client.post(f"/projects/{project['id']}/tasks/{task_id}/review", json={"action": "request_changes"})

    assert reviewed.status_code == 200
    body = reviewed.json()
    assert body["status"] == "in_progress"
    assert {assignee["status"] for assignee in body["assignees"]} == {"in_progress"}
    assert all(assignee["completed_at"] is None for assignee in body["assignees"])
