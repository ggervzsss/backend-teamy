from datetime import date, timedelta

import pytest


async def signup(client, email: str, full_name: str):
    user = await client._get_or_create_user(email, full_name)
    return {"id": str(user.id), "email": user.email, "full_name": user.full_name}


async def login(client, email: str, full_name: str = "Test User"):
    user = await client._login_user(email, full_name)
    return {"id": str(user.id), "email": user.email, "full_name": user.full_name}


async def logout(client):
    response = await client.post("/auth/logout")
    assert response.status_code == 204


async def setup_project_with_members(client):
    leader = await signup(client, "leader@example.com", "Leader User")
    await login(client, leader["email"], leader["full_name"])
    created = await client.post("/projects", json={"name": "Task Project"})
    assert created.status_code == 201
    project = created.json()
    await logout(client)

    member_one = await signup(client, "member-one@example.com", "Member One")
    await login(client, member_one["email"], member_one["full_name"])
    joined = await client.post("/projects/join", json={"teamy_code": project["teamy_code"]})
    assert joined.status_code == 200
    await logout(client)

    member_two = await signup(client, "member-two@example.com", "Member Two")
    await login(client, member_two["email"], member_two["full_name"])
    joined = await client.post("/projects/join", json={"teamy_code": project["teamy_code"]})
    assert joined.status_code == 200
    await logout(client)

    await login(client, leader["email"], leader["full_name"])
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
            "due_date": "2026-05-20",
            "initial_status": "todo",
        },
    )

    assert response.status_code == 201
    body = response.json()
    assert body["title"] == "Write report"
    assert body["status"] == "todo"
    assert body["due_date"] == "2026-05-20"
    assert {assignee["user"]["id"] for assignee in body["assignees"]} == {member_one["id"], member_two["id"]}


@pytest.mark.asyncio
async def test_task_with_past_due_date_defaults_start_to_due_date(client):
    project, _, member_one, _ = await setup_project_with_members(client)
    past_due_date = (date.today() - timedelta(days=3)).isoformat()

    response = await client.post(
        f"/projects/{project['id']}/tasks",
        json={
            "title": "Already overdue follow-up",
            "assignee_ids": [member_one["id"]],
            "due_date": past_due_date,
        },
    )

    assert response.status_code == 201
    body = response.json()
    assert body["status"] == "todo"
    assert body["is_record_only"] is False
    assert body["start_date"] == past_due_date
    assert body["due_date"] == past_due_date


@pytest.mark.asyncio
async def test_in_progress_task_with_past_due_date_stays_active(client):
    project, _, member_one, _ = await setup_project_with_members(client)
    past_due_date = (date.today() - timedelta(days=4)).isoformat()

    response = await client.post(
        f"/projects/{project['id']}/tasks",
        json={
            "title": "Late work in progress",
            "assignee_ids": [member_one["id"]],
            "due_date": past_due_date,
            "initial_status": "in_progress",
        },
    )

    assert response.status_code == 201
    body = response.json()
    assert body["status"] == "in_progress"
    assert body["is_record_only"] is False
    assert body["reviewed_at"] is None
    assert body["start_date"] == past_due_date
    assert body["due_date"] == past_due_date
    assert body["assignees"][0]["status"] == "in_progress"
    assert body["assignees"][0]["completed_at"] is None


@pytest.mark.asyncio
async def test_task_due_date_cannot_be_before_explicit_start_date(client):
    project, _, member_one, _ = await setup_project_with_members(client)
    start_date = date.today().isoformat()
    due_date = (date.today() - timedelta(days=1)).isoformat()

    response = await client.post(
        f"/projects/{project['id']}/tasks",
        json={
            "title": "Impossible schedule",
            "assignee_ids": [member_one["id"]],
            "start_date": start_date,
            "due_date": due_date,
        },
    )

    assert response.status_code == 422
    assert response.json()["detail"] == "Due date cannot be before start date"


@pytest.mark.asyncio
async def test_updating_task_to_past_due_date_keeps_dates_ordered(client):
    project, _, member_one, _ = await setup_project_with_members(client)
    created = await client.post(
        f"/projects/{project['id']}/tasks",
        json={"title": "Follow-up", "assignee_ids": [member_one["id"]]},
    )
    past_due_date = (date.today() - timedelta(days=2)).isoformat()

    updated = await client.patch(
        f"/projects/{project['id']}/tasks/{created.json()['id']}",
        json={"due_date": past_due_date},
    )

    assert updated.status_code == 200
    assert updated.json()["start_date"] == past_due_date
    assert updated.json()["due_date"] == past_due_date


@pytest.mark.asyncio
async def test_project_member_can_request_task_socket_ticket(client):
    project, _, member_one, _ = await setup_project_with_members(client)
    await logout(client)
    await login(client, member_one["email"], member_one["full_name"])

    response = await client.get(f"/projects/{project['id']}/tasks/ws-ticket")

    assert response.status_code == 200
    assert response.json()["ticket"]


@pytest.mark.asyncio
async def test_project_members_can_create_tasks_but_cannot_review(client):
    project, leader, member_one, _ = await setup_project_with_members(client)
    await logout(client)
    await login(client, member_one["email"], member_one["full_name"])

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

    await login(client, leader["email"], leader["full_name"])
    approved = await client.post(f"/projects/{project['id']}/tasks/{task_id}/review", json={"action": "approve"})
    assert approved.status_code == 200
    assert approved.json()["status"] == "done"


@pytest.mark.asyncio
async def test_leader_can_create_record_only_task_as_done(client):
    project, leader, member_one, _ = await setup_project_with_members(client)

    response = await client.post(
        f"/projects/{project['id']}/tasks",
        json={
            "title": "Completed kickoff",
            "description": "Recorded after the kickoff finished.",
            "assignee_ids": [member_one["id"]],
            "start_date": "2026-05-01",
            "due_date": "2026-05-02",
            "initial_status": "done",
        },
    )

    assert response.status_code == 201
    body = response.json()
    assert body["status"] == "done"
    assert body["is_record_only"] is True
    assert body["reviewed_by"]["id"] == leader["id"]
    assert body["reviewed_at"] is not None
    assert body["assignees"][0]["status"] == "ready_for_review"
    assert body["assignees"][0]["completed_at"] is not None


@pytest.mark.asyncio
async def test_leader_can_create_record_only_task_as_active_work(client):
    project, _, member_one, _ = await setup_project_with_members(client)
    past_due_date = (date.today() - timedelta(days=2)).isoformat()

    response = await client.post(
        f"/projects/{project['id']}/tasks",
        json={
            "title": "Backfilled active task",
            "assignee_ids": [member_one["id"]],
            "due_date": past_due_date,
            "initial_status": "in_progress",
            "is_record_only": True,
        },
    )

    assert response.status_code == 201
    body = response.json()
    assert body["status"] == "in_progress"
    assert body["is_record_only"] is True
    assert body["reviewed_at"] is None
    assert body["start_date"] == past_due_date
    assert body["due_date"] == past_due_date
    assert body["assignees"][0]["status"] == "in_progress"
    assert body["assignees"][0]["completed_at"] is None


@pytest.mark.asyncio
async def test_member_cannot_create_done_task_directly(client):
    project, _, member_one, _ = await setup_project_with_members(client)
    await logout(client)
    await login(client, member_one["email"], member_one["full_name"])

    response = await client.post(
        f"/projects/{project['id']}/tasks",
        json={"title": "Bypass review", "assignee_ids": [member_one["id"]], "initial_status": "done"},
    )

    assert response.status_code == 403
    assert response.json()["detail"] == "Only project leaders can perform this action"


@pytest.mark.asyncio
async def test_private_task_cannot_be_created_as_done(client):
    project, _, member_one, _ = await setup_project_with_members(client)
    await logout(client)
    await login(client, member_one["email"], member_one["full_name"])

    response = await client.post(
        f"/projects/{project['id']}/tasks",
        json={"title": "Already done note", "assignee_ids": [member_one["id"]], "initial_status": "done", "is_private": True},
    )

    assert response.status_code == 400
    assert response.json()["detail"] == "Private tasks cannot be record-only"


@pytest.mark.asyncio
async def test_member_cannot_create_record_only_task(client):
    project, _, member_one, _ = await setup_project_with_members(client)
    await logout(client)
    await login(client, member_one["email"], member_one["full_name"])

    response = await client.post(
        f"/projects/{project['id']}/tasks",
        json={"title": "Past task", "assignee_ids": [member_one["id"]], "is_record_only": True},
    )

    assert response.status_code == 403


@pytest.mark.asyncio
async def test_private_tasks_are_only_visible_in_my_tasks(client):
    project, leader, member_one, member_two = await setup_project_with_members(client)
    await logout(client)
    await login(client, member_one["email"], member_one["full_name"])

    private_response = await client.post(
        f"/projects/{project['id']}/tasks",
        json={
            "title": "Personal follow-up",
            "description": "Keep this note private.",
            "assignee_ids": [member_one["id"]],
            "is_private": True,
            "personal_kind": "note",
        },
    )

    assert private_response.status_code == 201
    private_task = private_response.json()
    assert private_task["is_private"] is True
    assert private_task["personal_kind"] == "note"

    board_for_owner = await client.get(f"/projects/{project['id']}/tasks")
    assert all(task["id"] != private_task["id"] for task in board_for_owner.json()["tasks"])

    my_tasks = await client.get(f"/projects/{project['id']}/tasks/me")
    assert {task["id"] for task in my_tasks.json()["tasks"]} == {private_task["id"]}

    completed = await client.patch(f"/projects/{project['id']}/tasks/{private_task['id']}/assignees/me", json={"status": "done"})
    assert completed.status_code == 200
    assert completed.json()["status"] == "done"
    assert completed.json()["assignees"][0]["completed_at"] is not None

    await logout(client)
    await login(client, member_two["email"], member_two["full_name"])
    member_two_tasks = await client.get(f"/projects/{project['id']}/tasks/me")
    assert all(task["id"] != private_task["id"] for task in member_two_tasks.json()["tasks"])
    member_two_update = await client.patch(f"/projects/{project['id']}/tasks/{private_task['id']}/assignees/me", json={"status": "in_progress"})
    assert member_two_update.status_code == 404

    await logout(client)
    await login(client, leader["email"], leader["full_name"])
    leader_update = await client.patch(f"/projects/{project['id']}/tasks/{private_task['id']}", json={"title": "Leader should not see this"})
    assert leader_update.status_code == 404


@pytest.mark.asyncio
async def test_my_tasks_includes_shared_assigned_tasks(client):
    project, _, member_one, member_two = await setup_project_with_members(client)
    created = await client.post(
        f"/projects/{project['id']}/tasks",
        json={"title": "Shared assignment", "assignee_ids": [member_one["id"]]},
    )
    task_id = created.json()["id"]
    await logout(client)

    await login(client, member_one["email"], member_one["full_name"])
    member_one_tasks = await client.get(f"/projects/{project['id']}/tasks/me")
    assert {task["id"] for task in member_one_tasks.json()["tasks"]} == {task_id}

    await logout(client)
    await login(client, member_two["email"], member_two["full_name"])
    member_two_tasks = await client.get(f"/projects/{project['id']}/tasks/me")
    assert all(task["id"] != task_id for task in member_two_tasks.json()["tasks"])


@pytest.mark.asyncio
async def test_member_readiness_requires_explicit_submit_for_review(client):
    project, leader, member_one, member_two = await setup_project_with_members(client)
    created = await client.post(
        f"/projects/{project['id']}/tasks",
        json={"title": "Build prototype", "assignee_ids": [member_one["id"], member_two["id"]], "initial_status": "in_progress"},
    )
    task_id = created.json()["id"]
    await logout(client)

    await login(client, member_one["email"], member_one["full_name"])
    first_update = await client.patch(f"/projects/{project['id']}/tasks/{task_id}/assignees/me", json={"status": "ready_for_review"})
    assert first_update.status_code == 200
    assert first_update.json()["status"] == "in_progress"
    early_submit = await client.post(f"/projects/{project['id']}/tasks/{task_id}/submit-review")
    assert early_submit.status_code == 400
    await logout(client)

    await login(client, member_two["email"], member_two["full_name"])
    second_update = await client.patch(f"/projects/{project['id']}/tasks/{task_id}/assignees/me", json={"status": "ready_for_review"})
    assert second_update.status_code == 200
    assert second_update.json()["status"] == "in_progress"
    submitted = await client.post(f"/projects/{project['id']}/tasks/{task_id}/submit-review")
    assert submitted.status_code == 200
    assert submitted.json()["status"] == "for_review"
    await logout(client)

    await login(client, leader["email"], leader["full_name"])
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

    await login(client, member_one["email"], member_one["full_name"])
    await client.patch(f"/projects/{project['id']}/tasks/{task_id}/assignees/me", json={"status": "ready_for_review"})
    await logout(client)
    await login(client, member_two["email"], member_two["full_name"])
    await client.patch(f"/projects/{project['id']}/tasks/{task_id}/assignees/me", json={"status": "ready_for_review"})
    await client.post(f"/projects/{project['id']}/tasks/{task_id}/submit-review")
    await logout(client)

    await login(client, leader["email"], leader["full_name"])
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

    await login(client, member_one["email"], member_one["full_name"])
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
    await login(client, member_one["email"], member_one["full_name"])
    created = await client.post(
        f"/projects/{project['id']}/tasks",
        json={"title": "Draft outline", "assignee_ids": [member_one["id"]]},
    )
    task_id = created.json()["id"]

    creator_update = await client.patch(
        f"/projects/{project['id']}/tasks/{task_id}",
        json={"title": "Draft outline v2", "assignee_ids": [member_one["id"], member_two["id"]]},
    )
    assert creator_update.status_code == 200
    assert creator_update.json()["title"] == "Draft outline v2"
    assert {assignee["user"]["id"] for assignee in creator_update.json()["assignees"]} == {member_one["id"], member_two["id"]}
    await logout(client)

    await login(client, leader["email"], leader["full_name"])
    leader_update = await client.patch(f"/projects/{project['id']}/tasks/{task_id}", json={"description": "Use the latest project template."})
    assert leader_update.status_code == 200
    assert leader_update.json()["description"] == "Use the latest project template."


@pytest.mark.asyncio
async def test_creator_or_leader_can_edit_done_task_details(client):
    project, leader, member_one, member_two = await setup_project_with_members(client)
    created = await client.post(
        f"/projects/{project['id']}/tasks",
        json={"title": "Final report", "assignee_ids": [member_one["id"]], "initial_status": "in_progress"},
    )
    task_id = created.json()["id"]
    await logout(client)

    await login(client, member_one["email"], member_one["full_name"])
    ready = await client.patch(f"/projects/{project['id']}/tasks/{task_id}/assignees/me", json={"status": "ready_for_review"})
    assert ready.status_code == 200
    submit = await client.post(f"/projects/{project['id']}/tasks/{task_id}/submit-review")
    assert submit.status_code == 200
    await logout(client)

    await login(client, leader["email"], leader["full_name"])
    approved = await client.post(f"/projects/{project['id']}/tasks/{task_id}/review", json={"action": "approve"})
    assert approved.status_code == 200
    assert approved.json()["status"] == "done"

    updated = await client.patch(
        f"/projects/{project['id']}/tasks/{task_id}",
        json={"title": "Final report archived", "description": "Clean copy.", "assignee_ids": [member_one["id"], member_two["id"]]},
    )

    assert updated.status_code == 200
    assert updated.json()["title"] == "Final report archived"
    assert updated.json()["status"] == "done"
    assert {assignee["user"]["id"] for assignee in updated.json()["assignees"]} == {member_one["id"], member_two["id"]}
    assert all(assignee["status"] == "ready_for_review" for assignee in updated.json()["assignees"])
    assert all(assignee["completed_at"] is not None for assignee in updated.json()["assignees"])


@pytest.mark.asyncio
async def test_assigned_member_can_link_external_resource_to_task(client):
    project, _, member_one, _ = await setup_project_with_members(client)
    created = await client.post(
        f"/projects/{project['id']}/tasks",
        json={"title": "Collect references", "assignee_ids": [member_one["id"]]},
    )
    task_id = created.json()["id"]
    await logout(client)

    await login(client, member_one["email"], member_one["full_name"])
    linked = await client.post(
        f"/projects/{project['id']}/tasks/{task_id}/linked-files",
        json={"mode": "link", "title": "Design brief", "url": "https://example.com/brief"},
    )

    assert linked.status_code == 201
    body = linked.json()
    assert body["linked_files"][0]["title"] == "Design brief"
    assert body["linked_files"][0]["url"] == "https://example.com/brief"

