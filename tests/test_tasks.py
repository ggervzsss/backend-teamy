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
async def test_project_member_can_request_task_socket_ticket(client):
    project, _, member_one, _ = await setup_project_with_members(client)
    await logout(client)
    await login(client, member_one["email"])

    response = await client.get(f"/projects/{project['id']}/tasks/ws-ticket")

    assert response.status_code == 200
    assert response.json()["ticket"]


@pytest.mark.asyncio
async def test_project_members_can_create_tasks_but_cannot_review(client):
    project, leader, member_one, _ = await setup_project_with_members(client)
    await logout(client)
    await login(client, member_one["email"])

    create_response = await client.post(
        f"/projects/{project['id']}/tasks",
        json={"title": "Member task", "assignee_ids": [member_one["id"]]},
    )

    assert create_response.status_code == 201
    task_id = create_response.json()["id"]
    await client.patch(f"/projects/{project['id']}/tasks/{task_id}/assignees/me", json={"status": "ready_for_review"})
    await client.post(f"/projects/{project['id']}/tasks/{task_id}/submit-review")
    review_response = await client.post(f"/projects/{project['id']}/tasks/{task_id}/review", json={"action": "approve"})
    assert review_response.status_code == 403
    await logout(client)

    await login(client, leader["email"])
    approved = await client.post(f"/projects/{project['id']}/tasks/{task_id}/review", json={"action": "approve"})
    assert approved.status_code == 200
    assert approved.json()["status"] == "done"


@pytest.mark.asyncio
async def test_member_readiness_requires_explicit_submit_for_review(client):
    project, leader, member_one, member_two = await setup_project_with_members(client)
    created = await client.post(
        f"/projects/{project['id']}/tasks",
        json={"title": "Build prototype", "assignee_ids": [member_one["id"], member_two["id"]], "initial_status": "in_progress"},
    )
    task_id = created.json()["id"]
    await logout(client)

    await login(client, member_one["email"])
    first_update = await client.patch(f"/projects/{project['id']}/tasks/{task_id}/assignees/me", json={"status": "ready_for_review"})
    assert first_update.status_code == 200
    assert first_update.json()["status"] == "in_progress"
    early_submit = await client.post(f"/projects/{project['id']}/tasks/{task_id}/submit-review")
    assert early_submit.status_code == 400
    await logout(client)

    await login(client, member_two["email"])
    second_update = await client.patch(f"/projects/{project['id']}/tasks/{task_id}/assignees/me", json={"status": "ready_for_review"})
    assert second_update.status_code == 200
    assert second_update.json()["status"] == "in_progress"
    submitted = await client.post(f"/projects/{project['id']}/tasks/{task_id}/submit-review")
    assert submitted.status_code == 200
    assert submitted.json()["status"] == "for_review"
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
    await client.patch(f"/projects/{project['id']}/tasks/{task_id}/assignees/me", json={"status": "ready_for_review"})
    await logout(client)
    await login(client, member_two["email"])
    await client.patch(f"/projects/{project['id']}/tasks/{task_id}/assignees/me", json={"status": "ready_for_review"})
    await client.post(f"/projects/{project['id']}/tasks/{task_id}/submit-review")
    await logout(client)

    await login(client, leader["email"])
    reviewed = await client.post(f"/projects/{project['id']}/tasks/{task_id}/review", json={"action": "request_changes", "remarks": "Please tighten the conclusion."})

    assert reviewed.status_code == 200
    body = reviewed.json()
    assert body["status"] == "in_progress"
    assert {assignee["status"] for assignee in body["assignees"]} == {"in_progress"}
    assert all(assignee["completed_at"] is None for assignee in body["assignees"])
    assert body["review_remarks"] == "Please tighten the conclusion."


@pytest.mark.asyncio
async def test_task_stays_in_progress_until_every_assignee_is_ready(client):
    project, leader, member_one, _ = await setup_project_with_members(client)
    created = await client.post(
        f"/projects/{project['id']}/tasks",
        json={"title": "Leader final pass", "assignee_ids": [leader["id"], member_one["id"]], "initial_status": "in_progress"},
    )
    task_id = created.json()["id"]
    await logout(client)

    await login(client, member_one["email"])
    updated = await client.patch(f"/projects/{project['id']}/tasks/{task_id}/assignees/me", json={"status": "ready_for_review"})

    assert updated.status_code == 200
    body = updated.json()
    assert body["status"] == "in_progress"
    assignee_statuses = {assignee["user"]["id"]: assignee["status"] for assignee in body["assignees"]}
    assert assignee_statuses[leader["id"]] == "in_progress"
    assert assignee_statuses[member_one["id"]] == "ready_for_review"

    submit = await client.post(f"/projects/{project['id']}/tasks/{task_id}/submit-review")
    assert submit.status_code == 400


@pytest.mark.asyncio
async def test_creator_or_leader_can_edit_task_details(client):
    project, leader, member_one, member_two = await setup_project_with_members(client)
    await logout(client)
    await login(client, member_one["email"])
    created = await client.post(
        f"/projects/{project['id']}/tasks",
        json={"title": "Draft outline", "assignee_ids": [member_one["id"]], "priority": "low"},
    )
    task_id = created.json()["id"]

    creator_update = await client.patch(
        f"/projects/{project['id']}/tasks/{task_id}",
        json={"title": "Draft outline v2", "priority": "high", "assignee_ids": [member_one["id"], member_two["id"]]},
    )
    assert creator_update.status_code == 200
    assert creator_update.json()["title"] == "Draft outline v2"
    assert creator_update.json()["priority"] == "high"
    assert {assignee["user"]["id"] for assignee in creator_update.json()["assignees"]} == {member_one["id"], member_two["id"]}
    await logout(client)

    await login(client, leader["email"])
    leader_update = await client.patch(f"/projects/{project['id']}/tasks/{task_id}", json={"description": "Use the latest project template."})
    assert leader_update.status_code == 200
    assert leader_update.json()["description"] == "Use the latest project template."


@pytest.mark.asyncio
async def test_assigned_member_can_link_external_resource_to_task(client):
    project, _, member_one, _ = await setup_project_with_members(client)
    created = await client.post(
        f"/projects/{project['id']}/tasks",
        json={"title": "Collect references", "assignee_ids": [member_one["id"]]},
    )
    task_id = created.json()["id"]
    await logout(client)

    await login(client, member_one["email"])
    linked = await client.post(
        f"/projects/{project['id']}/tasks/{task_id}/linked-files",
        json={"mode": "link", "title": "Design brief", "url": "https://example.com/brief"},
    )

    assert linked.status_code == 201
    body = linked.json()
    assert body["linked_files"][0]["title"] == "Design brief"
    assert body["linked_files"][0]["url"] == "https://example.com/brief"
