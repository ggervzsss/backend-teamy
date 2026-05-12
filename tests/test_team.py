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
    leader = await signup(client, "team-leader@example.com", "Team Leader")
    created = await client.post("/projects", json={"name": "Team Project"})
    assert created.status_code == 201
    project = created.json()
    await logout(client)

    member = await signup(client, "team-member@example.com", "Team Member")
    joined = await client.post("/projects/join", json={"teamy_code": project["teamy_code"]})
    assert joined.status_code == 200
    await logout(client)

    await login(client, leader["email"])
    return project, member


@pytest.mark.asyncio
async def test_project_member_can_request_team_socket_ticket(client):
    project, member = await setup_project(client)
    await logout(client)
    await login(client, member["email"])

    response = await client.get(f"/projects/{project['id']}/members/ws-ticket")

    assert response.status_code == 200
    assert response.json()["ticket"]


@pytest.mark.asyncio
async def test_project_member_can_list_presence_roster(client):
    project, member = await setup_project(client)
    await logout(client)
    await login(client, member["email"])

    response = await client.get(f"/projects/{project['id']}/members/presence")

    assert response.status_code == 200
    members = response.json()["members"]
    assert len(members) == 2
    assert {roster_member["user"]["email"] for roster_member in members} == {"team-leader@example.com", "team-member@example.com"}
    assert all(roster_member["is_online"] is False for roster_member in members)
    assert all("last_online_at" in roster_member for roster_member in members)


@pytest.mark.asyncio
async def test_project_member_nickname_is_scoped_to_project_membership(client):
    project, member = await setup_project(client)
    response = await client.get(f"/projects/{project['id']}/members")
    member_row = next(row for row in response.json()["members"] if row["user"]["id"] == member["id"])

    updated = await client.patch(f"/projects/{project['id']}/members/{member_row['id']}/nickname", json={"nickname": "Design Lead"})
    me_response = await client.get("/auth/me")
    roster_response = await client.get(f"/projects/{project['id']}/members")

    assert updated.status_code == 200
    assert updated.json()["nickname"] == "Design Lead"
    assert updated.json()["user"]["username"] == "Design Lead"
    assert me_response.json()["user"]["username"] is None
    updated_row = next(row for row in roster_response.json()["members"] if row["user"]["id"] == member["id"])
    assert updated_row["nickname"] == "Design Lead"
    assert updated_row["user"]["username"] == "Design Lead"


@pytest.mark.asyncio
async def test_non_members_cannot_request_team_socket_ticket(client):
    project, _ = await setup_project(client)
    await logout(client)
    await signup(client, "team-outsider@example.com", "Team Outsider")

    response = await client.get(f"/projects/{project['id']}/members/ws-ticket")

    assert response.status_code == 404
