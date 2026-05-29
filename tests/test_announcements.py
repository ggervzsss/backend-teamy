from datetime import date, timedelta

import pytest


async def signup(client, email: str, full_name: str):
    return await client._get_or_create_user(email, full_name)


async def login(client, email: str, full_name: str = "Test User"):
    await client._login_user(email, full_name)


async def logout(client):
    response = await client.post("/auth/logout")
    assert response.status_code == 204
    client._logout_user()


async def setup_project(client):
    leader = await signup(client, "announce-leader@example.com", "Announce Leader")
    await client._login_user(leader.email, leader.full_name)
    created = await client.post("/projects", json={"name": "Announcement Project"})
    assert created.status_code == 201
    project = created.json()
    await logout(client)

    member = await signup(client, "announce-member@example.com", "Announce Member")
    await client._login_user(member.email, member.full_name)
    joined = await client.post("/projects/join", json={"teamy_code": project["teamy_code"]})
    assert joined.status_code == 200
    await logout(client)

    await login(client, leader.email, leader.full_name)
    return project, leader, member


@pytest.mark.asyncio
async def test_project_members_can_create_and_list_announcements(client):
    project, _, member = await setup_project(client)
    await logout(client)
    await login(client, member.email, member.full_name)
    deadline_date = (date.today() + timedelta(days=7)).isoformat()

    created = await client.post(
        f"/projects/{project['id']}/announcements",
        json={"title": "Sprint demo", "body": "Demo starts at 3 PM.", "is_pinned": True, "deadline_date": deadline_date},
    )

    assert created.status_code == 201
    body = created.json()
    assert body["title"] == "Sprint demo"
    assert body["is_pinned"] is True
    assert body["deadline_date"] == deadline_date
    assert body["is_read"] is True
    assert body["created_by"]["id"] == str(member.id)

    listed = await client.get(f"/projects/{project['id']}/announcements")
    assert listed.status_code == 200
    assert listed.json()["announcements"][0]["id"] == body["id"]


@pytest.mark.asyncio
async def test_leader_can_create_record_only_announcement_without_unread_state(client):
    project, leader, member = await setup_project(client)

    created = await client.post(
        f"/projects/{project['id']}/announcements",
        json={"title": "Old town hall", "body": "Recorded after the event.", "deadline_date": "2026-05-01", "is_pinned": True, "is_record_only": True},
    )

    assert created.status_code == 201
    body = created.json()
    assert body["created_by"]["id"] == str(leader.id)
    assert body["is_record_only"] is True
    assert body["is_pinned"] is False
    assert body["is_read"] is True
    await logout(client)

    await login(client, member.email, member.full_name)
    listed_for_member = await client.get(f"/projects/{project['id']}/announcements")
    assert listed_for_member.status_code == 200
    assert listed_for_member.json()["announcements"][0]["is_record_only"] is True
    assert listed_for_member.json()["announcements"][0]["is_read"] is True


@pytest.mark.asyncio
async def test_member_cannot_create_record_only_announcement(client):
    project, _, member = await setup_project(client)
    await logout(client)
    await login(client, member.email, member.full_name)

    created = await client.post(
        f"/projects/{project['id']}/announcements",
        json={"title": "Old update", "body": "Backfilled note.", "is_record_only": True},
    )

    assert created.status_code == 403


@pytest.mark.asyncio
async def test_announcement_read_state_is_per_user(client):
    project, leader, member = await setup_project(client)
    created = await client.post(
        f"/projects/{project['id']}/announcements",
        json={"title": "Review notes", "body": "Please read before tomorrow."},
    )
    announcement_id = created.json()["id"]
    assert created.json()["is_read"] is True
    await logout(client)

    await login(client, member.email, member.full_name)
    listed_for_member = await client.get(f"/projects/{project['id']}/announcements")
    assert listed_for_member.status_code == 200
    assert listed_for_member.json()["announcements"][0]["is_read"] is False

    opened = await client.get(f"/projects/{project['id']}/announcements/{announcement_id}")
    assert opened.status_code == 200
    assert opened.json()["is_read"] is True

    listed_after_read = await client.get(f"/projects/{project['id']}/announcements")
    assert listed_after_read.json()["announcements"][0]["is_read"] is True
    await logout(client)

    await login(client, leader.email, leader.full_name)
    listed_for_leader = await client.get(f"/projects/{project['id']}/announcements")
    assert listed_for_leader.json()["announcements"][0]["is_read"] is True


@pytest.mark.asyncio
async def test_project_members_can_pin_and_unpin_announcements(client):
    project, _, member = await setup_project(client)
    created = await client.post(
        f"/projects/{project['id']}/announcements",
        json={"title": "Office hours", "body": "Drop by any time."},
    )
    announcement_id = created.json()["id"]
    await logout(client)

    await login(client, member.email, member.full_name)
    pinned = await client.patch(f"/projects/{project['id']}/announcements/{announcement_id}/pin", json={"is_pinned": True})
    assert pinned.status_code == 200
    assert pinned.json()["is_pinned"] is True

    unpinned = await client.patch(f"/projects/{project['id']}/announcements/{announcement_id}/pin", json={"is_pinned": False})
    assert unpinned.status_code == 200
    assert unpinned.json()["is_pinned"] is False


@pytest.mark.asyncio
async def test_future_dated_announcements_are_auto_pinned(client):
    project, _, member = await setup_project(client)
    await logout(client)
    await login(client, member.email, member.full_name)
    deadline_date = (date.today() + timedelta(days=3)).isoformat()

    created = await client.post(
        f"/projects/{project['id']}/announcements",
        json={"title": "Planning meeting", "body": "Meet next week.", "deadline_date": deadline_date},
    )

    assert created.status_code == 201
    assert created.json()["is_pinned"] is True


@pytest.mark.asyncio
async def test_past_dated_announcements_are_not_pinned(client):
    project, _, member = await setup_project(client)
    await logout(client)
    await login(client, member.email, member.full_name)
    deadline_date = (date.today() - timedelta(days=1)).isoformat()

    created = await client.post(
        f"/projects/{project['id']}/announcements",
        json={"title": "Past meeting", "body": "Already happened.", "is_pinned": True, "deadline_date": deadline_date},
    )

    assert created.status_code == 201
    assert created.json()["is_pinned"] is False

    pinned = await client.patch(f"/projects/{project['id']}/announcements/{created.json()['id']}/pin", json={"is_pinned": True})
    assert pinned.status_code == 400


@pytest.mark.asyncio
async def test_undated_announcement_can_be_marked_deadline_done(client):
    project, _, member = await setup_project(client)
    await logout(client)
    await login(client, member.email, member.full_name)

    created = await client.post(
        f"/projects/{project['id']}/announcements",
        json={"title": "Standing reminder", "body": "Keep the workspace updated."},
    )
    announcement_id = created.json()["id"]

    marked = await client.patch(f"/projects/{project['id']}/announcements/{announcement_id}/deadline-done")

    assert marked.status_code == 200
    assert marked.json()["deadline_done_at"] is not None


@pytest.mark.asyncio
async def test_dated_announcement_cannot_be_marked_deadline_done(client):
    project, _, member = await setup_project(client)
    await logout(client)
    await login(client, member.email, member.full_name)
    deadline_date = (date.today() + timedelta(days=3)).isoformat()

    created = await client.post(
        f"/projects/{project['id']}/announcements",
        json={"title": "Kickoff", "body": "Join the call.", "deadline_date": deadline_date},
    )

    marked = await client.patch(f"/projects/{project['id']}/announcements/{created.json()['id']}/deadline-done")

    assert marked.status_code == 400


@pytest.mark.asyncio
async def test_creator_or_leader_can_edit_announcement(client):
    project, leader, member = await setup_project(client)
    deadline_date = (date.today() + timedelta(days=7)).isoformat()
    await logout(client)
    await login(client, member.email, member.full_name)
    created = await client.post(
        f"/projects/{project['id']}/announcements",
        json={"title": "Draft update", "body": "Initial note."},
    )
    announcement_id = created.json()["id"]

    creator_update = await client.patch(
        f"/projects/{project['id']}/announcements/{announcement_id}",
        json={"title": "Updated note", "body": "Published note.", "is_pinned": True, "deadline_date": deadline_date},
    )
    assert creator_update.status_code == 200
    assert creator_update.json()["title"] == "Updated note"
    assert creator_update.json()["is_pinned"] is True
    assert creator_update.json()["deadline_date"] == deadline_date
    await logout(client)

    await login(client, leader.email, leader.full_name)
    leader_update = await client.patch(
        f"/projects/{project['id']}/announcements/{announcement_id}",
        json={"body": "Leader clarified note.", "deadline_date": None},
    )
    assert leader_update.status_code == 200
    assert leader_update.json()["body"] == "Leader clarified note."
    assert leader_update.json()["deadline_date"] is None


@pytest.mark.asyncio
async def test_creator_or_leader_can_delete_announcements(client):
    project, leader, member = await setup_project(client)
    await logout(client)
    await login(client, member.email, member.full_name)
    created = await client.post(
        f"/projects/{project['id']}/announcements",
        json={"title": "Temporary update", "body": "This can be removed."},
    )
    assert created.status_code == 201
    announcement_id = created.json()["id"]

    creator_delete = await client.delete(f"/projects/{project['id']}/announcements/{announcement_id}")
    assert creator_delete.status_code == 204

    listed_after_creator_delete = await client.get(f"/projects/{project['id']}/announcements")
    assert listed_after_creator_delete.status_code == 200
    assert listed_after_creator_delete.json()["announcements"] == []

    await logout(client)
    await login(client, member.email, member.full_name)
    second_created = await client.post(
        f"/projects/{project['id']}/announcements",
        json={"title": "Leader cleanup", "body": "Leader can remove this."},
    )
    assert second_created.status_code == 201
    second_announcement_id = second_created.json()["id"]
    await logout(client)

    await login(client, leader.email, leader.full_name)
    leader_delete = await client.delete(f"/projects/{project['id']}/announcements/{second_announcement_id}")
    assert leader_delete.status_code == 204

    listed_after_leader_delete = await client.get(f"/projects/{project['id']}/announcements")
    assert listed_after_leader_delete.status_code == 200
    assert listed_after_leader_delete.json()["announcements"] == []


@pytest.mark.asyncio
async def test_done_announcement_can_be_deleted(client):
    project, _, member = await setup_project(client)
    await logout(client)
    await login(client, member.email, member.full_name)
    created = await client.post(
        f"/projects/{project['id']}/announcements",
        json={"title": "Completed reminder", "body": "Remove after done."},
    )
    announcement_id = created.json()["id"]
    marked = await client.patch(f"/projects/{project['id']}/announcements/{announcement_id}/deadline-done")
    assert marked.status_code == 200

    deleted = await client.delete(f"/projects/{project['id']}/announcements/{announcement_id}")
    assert deleted.status_code == 204

    listed = await client.get(f"/projects/{project['id']}/announcements")
    assert listed.status_code == 200
    assert listed.json()["announcements"] == []


@pytest.mark.asyncio
async def test_non_creator_member_cannot_delete_announcement(client):
    project, _, member = await setup_project(client)
    created = await client.post(
        f"/projects/{project['id']}/announcements",
        json={"title": "Leader note", "body": "Only leader can delete this."},
    )
    announcement_id = created.json()["id"]
    await logout(client)

    await login(client, member.email, member.full_name)
    deleted = await client.delete(f"/projects/{project['id']}/announcements/{announcement_id}")

    assert deleted.status_code == 403


@pytest.mark.asyncio
async def test_non_members_cannot_access_announcements(client):
    project, _, _ = await setup_project(client)
    await logout(client)
    outsider = await signup(client, "announce-outside@example.com", "Outside User")
    await client._login_user(outsider.email, outsider.full_name)

    response = await client.get(f"/projects/{project['id']}/announcements")

    assert response.status_code == 404


@pytest.mark.asyncio
async def test_project_member_can_request_announcement_socket_ticket(client):
    project, _, member = await setup_project(client)
    await logout(client)
    await login(client, member.email, member.full_name)

    response = await client.get(f"/projects/{project['id']}/announcements/ws-ticket")

    assert response.status_code == 200
    assert response.json()["ticket"]
