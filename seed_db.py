import asyncio
from datetime import date, datetime, timedelta, UTC
import os
import sys
from uuid import uuid4

# Detect if we are running on the host or inside docker
is_on_host = os.path.exists("backend-teamy")

if is_on_host:
    # Add backend directory to path so we can import app modules
    backend_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "backend-teamy")
    sys.path.append(backend_path)
    
    # Load .env file from the root directory
    if os.path.exists(".env"):
        with open(".env") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                if "=" in line:
                    k, v = line.split("=", 1)
                    key = k.strip()
                    val = v.strip().strip('"').strip("'")
                    if key not in os.environ:
                        os.environ[key] = val

    # Adapt DATABASE_URL for running on host
    db_url = os.environ.get("DATABASE_URL")
    if db_url and "@db:3306" in db_url:
        mysql_port = os.environ.get("MYSQL_PORT", "3307")
        db_url = db_url.replace("@db:3306", f"@localhost:{mysql_port}")
        os.environ["DATABASE_URL"] = db_url
else:
    # We are running inside docker or in the backend directory directly
    backend_path = os.path.dirname(os.path.abspath(__file__))
    sys.path.append(backend_path)

# Now import the SQLAlchemy session, select, and models from backend app
from sqlalchemy import select
from app.database import SessionLocal, engine
from app.models import (
    User, Project, ProjectMember, Task, TaskAssignee,
    FileResource, TaskFileLink, Announcement, Notification
)

async def seed():
    print("Connecting to the database to seed sample data...")
    
    async with SessionLocal() as db:
        # 1. Check for existing users (e.g. developer logged in via Google)
        result = await db.execute(select(User))
        existing_users = result.scalars().all()
        
        developer_user = None
        if existing_users:
            developer_user = existing_users[0]
            print(f"Detected existing user (developer): {developer_user.full_name} ({developer_user.email})")
        else:
            print("No existing users found. Seeding default developer account...")
            # We will create a local developer account if none exists
            developer_user = User(
                id=uuid4(),
                email="developer@teamy.test",
                full_name="Alex Developer",
                username="alex_dev",
                auth_provider="local",
                avatar_url="https://api.dicebear.com/7.x/adventurer/svg?seed=Alex",
                last_online_at=datetime.now(UTC)
            )
            db.add(developer_user)
            await db.flush()
            
        # 2. Seed realistic sample team members
        print("Seeding sample team members...")
        sample_users_data = [
            ("alice@teamy.test", "Alice Vance", "alice_pm", "Alice"),
            ("bob@teamy.test", "Bob Miller", "bob_dev", "Bob"),
            ("charlie@teamy.test", "Charlie Jenkins", "charlie_design", "Charlie"),
            ("diana@teamy.test", "Diana Prince", "diana_qa", "Diana")
        ]
        
        seeded_users = []
        for email, full_name, username, seed_name in sample_users_data:
            # Check if user already exists
            res = await db.execute(select(User).where(User.email == email))
            u = res.scalar_one_or_none()
            if not u:
                u = User(
                    id=uuid4(),
                    email=email,
                    full_name=full_name,
                    username=username,
                    auth_provider="local",
                    avatar_url=f"https://api.dicebear.com/7.x/adventurer/svg?seed={seed_name}",
                    last_online_at=datetime.now(UTC) - timedelta(minutes=15)
                )
                db.add(u)
                await db.flush()
            seeded_users.append(u)
            
        alice, bob, charlie, diana = seeded_users

        # 3. Seed Projects
        print("Seeding sample projects...")
        
        # Project 1: Apollo Launch Website
        res_p1 = await db.execute(select(Project).where(Project.teamy_code == "APOLLO-WEBSITE"))
        apollo_project = res_p1.scalar_one_or_none()
        if not apollo_project:
            apollo_project = Project(
                id=uuid4(),
                name="Apollo Launch Website",
                description="Marketing site and user dashboard launch for the Apollo space exploration platform. Includes design layout, copywriting, analytics integration, and responsive testing.",
                teamy_code="APOLLO-WEBSITE",
                created_by_user_id=developer_user.id
            )
            db.add(apollo_project)
            await db.flush()
            
        # Project 2: Mobile App Redesign
        res_p2 = await db.execute(select(Project).where(Project.teamy_code == "MOBILE-REDESIGN"))
        mobile_project = res_p2.scalar_one_or_none()
        if not mobile_project:
            mobile_project = Project(
                id=uuid4(),
                name="Mobile App Redesign",
                description="Re-architecting and redesigning the Teamy iOS/Android app. Focus on modernizing visual layout, smooth gestures, biometric login, and performance optimization.",
                teamy_code="MOBILE-REDESIGN",
                created_by_user_id=alice.id
            )
            db.add(mobile_project)
            await db.flush()

        # 4. Seed Project Members
        print("Adding members to projects...")
        # Project 1 Members (Leader: Developer, Members: Alice, Bob, Charlie, Diana)
        p1_members_data = [
            (developer_user.id, "leader", "Lead Dev"),
            (alice.id, "member", "PM"),
            (bob.id, "member", "Fullstack"),
            (charlie.id, "member", "Designer"),
            (diana.id, "member", "QA Lead")
        ]
        for user_id, role, nickname in p1_members_data:
            res_m = await db.execute(select(ProjectMember).where(
                ProjectMember.project_id == apollo_project.id,
                ProjectMember.user_id == user_id
            ))
            if not res_m.scalar_one_or_none():
                pm = ProjectMember(
                    id=uuid4(),
                    project_id=apollo_project.id,
                    user_id=user_id,
                    role=role,
                    nickname=nickname
                )
                db.add(pm)
                
        # Project 2 Members (Leader: Alice, Members: Developer, Bob, Charlie, Diana)
        p2_members_data = [
            (alice.id, "leader", "Product Owner"),
            (developer_user.id, "member", "Mobile Arch"),
            (bob.id, "member", "React Native Dev"),
            (charlie.id, "member", "UI/UX"),
            (diana.id, "member", "QA Engineer")
        ]
        for user_id, role, nickname in p2_members_data:
            res_m = await db.execute(select(ProjectMember).where(
                ProjectMember.project_id == mobile_project.id,
                ProjectMember.user_id == user_id
            ))
            if not res_m.scalar_one_or_none():
                pm = ProjectMember(
                    id=uuid4(),
                    project_id=mobile_project.id,
                    user_id=user_id,
                    role=role,
                    nickname=nickname
                )
                db.add(pm)
                
        await db.flush()

        # 5. Seed File Resources
        print("Seeding file resources...")
        resources_data = [
            (apollo_project.id, "Apollo Figma Layout Brief", "link", "https://www.figma.com/file/apollo-design-brief", None, developer_user.id),
            (apollo_project.id, "Apollo Brand Assets Package", "file", "https://res.cloudinary.com/teamy/raw/upload/apollo_assets.zip", "<p>Includes vector SVGs, font assets (Geist & Inter), and official brand color guidelines.</p>", developer_user.id),
            (mobile_project.id, "Mobile Redesign Figma Mockups", "link", "https://www.figma.com/file/mobile-redesign-proto", None, alice.id)
        ]
        
        seeded_resources = []
        for pid, title, kind, url, html, creator_id in resources_data:
            res_f = await db.execute(select(FileResource).where(
                FileResource.project_id == pid,
                FileResource.title == title
            ))
            f_res = res_f.scalar_one_or_none()
            if not f_res:
                f_res = FileResource(
                    id=uuid4(),
                    project_id=pid,
                    title=title,
                    kind=kind,
                    url=url,
                    content_html=html,
                    created_by_user_id=creator_id
                )
                db.add(f_res)
                await db.flush()
            seeded_resources.append(f_res)
            
        figma_brief, brand_assets, mobile_mockups = seeded_resources

        # 6. Seed Tasks
        print("Seeding tasks and task assignees...")
        today = date.today()
        
        # Project 1 Tasks
        p1_tasks_data = [
            # 1. Brand Guidelines (Done)
            {
                "title": "Define visual brand guidelines",
                "description": "Establish the color palette, typography hierarchy, and spacing rules for the landing page.",
                "priority": "high",
                "start_date": today - timedelta(days=20),
                "due_date": today - timedelta(days=15),
                "status": "done",
                "assignees": [charlie.id],
                "files": [brand_assets.id]
            },
            # 2. Copywriting (Done)
            {
                "title": "Draft website copywriting",
                "description": "Write initial draft copy for hero section, features list, and call-to-actions.",
                "priority": "medium",
                "start_date": today - timedelta(days=15),
                "due_date": today - timedelta(days=10),
                "status": "done",
                "assignees": [alice.id],
                "files": [figma_brief.id]
            },
            # 3. Landing page development (In Progress)
            {
                "title": "Develop landing page layout",
                "description": "Translate Figma designs into responsive React components using Geist font styling.",
                "priority": "high",
                "start_date": today - timedelta(days=5),
                "due_date": today + timedelta(days=4),
                "status": "in_progress",
                "assignees": [bob.id]
            },
            # 4. CI/CD setup (In Progress)
            {
                "title": "Setup continuous integration & staging environment",
                "description": "Configure GitHub Actions and connect AWS Amplify / Vercel for preview deployments.",
                "priority": "high",
                "start_date": today - timedelta(days=3),
                "due_date": today + timedelta(days=2),
                "status": "in_progress",
                "assignees": [developer_user.id]
            },
            # 5. Illustrations (To Do)
            {
                "title": "Create SVG illustrations for hero section",
                "description": "Custom tech-themed vector graphics demonstrating interactive workflows.",
                "priority": "low",
                "start_date": today,
                "due_date": today + timedelta(days=6),
                "status": "todo",
                "assignees": [charlie.id],
                "files": [brand_assets.id]
            },
            # 6. Responsiveness review (For Review)
            {
                "title": "Review responsiveness on tablet & mobile devices",
                "description": "Inspect layout breakpoints at 768px and 375px. Resolve overlapping text columns.",
                "priority": "medium",
                "start_date": today - timedelta(days=2),
                "due_date": today + timedelta(days=1),
                "status": "for_review",
                "assignees": [diana.id]
            },
            # 7. Overdue Security Alerts (Todo - Overdue!)
            {
                "title": "Resolve critical dependency security scanner alerts",
                "description": "Run npm audit/pip audit. Update packages exhibiting vulnerability exploits.",
                "priority": "high",
                "start_date": today - timedelta(days=12),
                "due_date": today - timedelta(days=4),
                "status": "todo",
                "assignees": [bob.id, developer_user.id]
            },
            # 8. Beta Deploy (To Do - Future)
            {
                "title": "Publish beta website to staging router",
                "description": "Deploy to staging, run sanity checks, and distribute URL to testers.",
                "priority": "medium",
                "start_date": today + timedelta(days=8),
                "due_date": today + timedelta(days=12),
                "status": "todo",
                "assignees": [bob.id]
            }
        ]
        
        for t_data in p1_tasks_data:
            res_t = await db.execute(select(Task).where(
                Task.project_id == apollo_project.id,
                Task.title == t_data["title"]
            ))
            task = res_t.scalar_one_or_none()
            if not task:
                task = Task(
                    id=uuid4(),
                    project_id=apollo_project.id,
                    title=t_data["title"],
                    description=t_data["description"],
                    priority=t_data["priority"],
                    start_date=t_data["start_date"],
                    due_date=t_data["due_date"],
                    status=t_data["status"],
                    created_by_user_id=developer_user.id
                )
                db.add(task)
                await db.flush()
                
                # Add Assignees
                for user_id in t_data["assignees"]:
                    ta_status = "todo"
                    if t_data["status"] == "in_progress":
                        ta_status = "in_progress"
                    elif t_data["status"] in ("for_review", "done"):
                        ta_status = "ready_for_review"
                        
                    ta = TaskAssignee(
                        id=uuid4(),
                        task_id=task.id,
                        user_id=user_id,
                        status=ta_status,
                        completed_at=datetime.now(UTC) if t_data["status"] == "done" else None
                    )
                    db.add(ta)
                
                # Add File Links
                if "files" in t_data:
                    for fid in t_data["files"]:
                        flink = TaskFileLink(
                            id=uuid4(),
                            task_id=task.id,
                            file_resource_id=fid
                        )
                        db.add(flink)
                        
        # Project 2 Tasks
        p2_tasks_data = [
            # A. User Interviews (Done)
            {
                "title": "Conduct user interviews on old layout",
                "description": "Gather feedback from 5 active customers regarding dashboard confusion and task navigation pain points.",
                "priority": "high",
                "start_date": today - timedelta(days=25),
                "due_date": today - timedelta(days=18),
                "status": "done",
                "assignees": [diana.id]
            },
            # B. Figma Prototypes (In Progress - Overdue!)
            {
                "title": "Create Figma high-fidelity prototypes",
                "description": "Build high-fidelity wireframes including the interactive timeline view and navigation switcher.",
                "priority": "high",
                "start_date": today - timedelta(days=8),
                "due_date": today - timedelta(days=2),
                "status": "in_progress",
                "assignees": [charlie.id],
                "files": [mobile_mockups.id]
            },
            # C. Performance Rendering (To Do)
            {
                "title": "Optimize list rendering performance",
                "description": "Audit layout recalculations and implement row-virtualization for long feeds.",
                "priority": "high",
                "start_date": today,
                "due_date": today + timedelta(days=10),
                "status": "todo",
                "assignees": [bob.id, developer_user.id]
            },
            # D. Biometric Login (To Do)
            {
                "title": "Implement biometric login (Face ID / Touch ID)",
                "description": "Configure Keychain integration on iOS and Keystore on Android to securely cache OAuth tokens.",
                "priority": "medium",
                "start_date": today + timedelta(days=4),
                "due_date": today + timedelta(days=14),
                "status": "todo",
                "assignees": [bob.id]
            },
            # E. QA Testing (To Do)
            {
                "title": "QA regression testing & bugs log",
                "description": "Execute test cases on staging. Open tickets for layout deviations or script crashes.",
                "priority": "medium",
                "start_date": today + timedelta(days=15),
                "due_date": today + timedelta(days=20),
                "status": "todo",
                "assignees": [diana.id]
            }
        ]
        
        for t_data in p2_tasks_data:
            res_t = await db.execute(select(Task).where(
                Task.project_id == mobile_project.id,
                Task.title == t_data["title"]
            ))
            task = res_t.scalar_one_or_none()
            if not task:
                task = Task(
                    id=uuid4(),
                    project_id=mobile_project.id,
                    title=t_data["title"],
                    description=t_data["description"],
                    priority=t_data["priority"],
                    start_date=t_data["start_date"],
                    due_date=t_data["due_date"],
                    status=t_data["status"],
                    created_by_user_id=alice.id
                )
                db.add(task)
                await db.flush()
                
                # Add Assignees
                for user_id in t_data["assignees"]:
                    ta_status = "todo"
                    if t_data["status"] == "in_progress":
                        ta_status = "in_progress"
                    elif t_data["status"] in ("for_review", "done"):
                        ta_status = "ready_for_review"
                        
                    ta = TaskAssignee(
                        id=uuid4(),
                        task_id=task.id,
                        user_id=user_id,
                        status=ta_status,
                        completed_at=datetime.now(UTC) if t_data["status"] == "done" else None
                    )
                    db.add(ta)
                
                # Add File Links
                if "files" in t_data:
                    for fid in t_data["files"]:
                        flink = TaskFileLink(
                            id=uuid4(),
                            task_id=task.id,
                            file_resource_id=fid
                        )
                        db.add(flink)
                        
        await db.flush()

        # 7. Seed Announcements
        print("Seeding announcements...")
        announcements_data = [
            # Ppinned announcement on Apollo Website
            {
                "project_id": apollo_project.id,
                "title": "🚀 Welcome to the Apollo Launch Project!",
                "body": "<p>Hey team! Welcome to the central command hub for the Apollo Launch website design and development.</p><p>We will use this workspace to coordinate tasks, share links, and publish updates. Check the <b>Resources</b> tab to grab the official brand package and design file.</p>",
                "is_pinned": True,
                "deadline_date": None,
                "creator_id": developer_user.id
            },
            # Deadline announcement on Apollo Website
            {
                "project_id": apollo_project.id,
                "title": "⏳ Landing Page Content Freeze",
                "body": "<p>Quick reminder that all illustrations, animations, and copywriting draft versions must be complete by the freeze date. We will start front-end assembly immediately after.</p>",
                "is_pinned": False,
                "deadline_date": today + timedelta(days=2),
                "creator_id": developer_user.id
            },
            # Announcement on Mobile App
            {
                "project_id": mobile_project.id,
                "title": "📱 Figma Prototypes Ready for Review",
                "body": "<p>Charlie has completed the interactive prototypes for the biometric authorization and timeline flows. Please review and leave comments in Figma before our sync tomorrow morning.</p>",
                "is_pinned": True,
                "deadline_date": today + timedelta(days=1),
                "creator_id": alice.id
            }
        ]
        
        for a_data in announcements_data:
            res_a = await db.execute(select(Announcement).where(
                Announcement.project_id == a_data["project_id"],
                Announcement.title == a_data["title"]
            ))
            if not res_a.scalar_one_or_none():
                ann = Announcement(
                    id=uuid4(),
                    project_id=a_data["project_id"],
                    title=a_data["title"],
                    body=a_data["body"],
                    is_pinned=a_data["is_pinned"],
                    deadline_date=a_data["deadline_date"],
                    created_by_user_id=a_data["creator_id"]
                )
                db.add(ann)

        # 8. Seed Notifications
        print("Seeding notifications...")
        notifications_data = [
            {
                "user_id": developer_user.id,
                "project_id": apollo_project.id,
                "kind": "task_assigned",
                "title": "New Task Assigned",
                "body": f"You have been assigned to: 'Setup continuous integration & staging environment'",
                "target_path": f"/projects/{apollo_project.id}/tasks"
            },
            {
                "user_id": developer_user.id,
                "project_id": apollo_project.id,
                "kind": "announcement_created",
                "title": "New Announcement Pinned",
                "body": "🚀 Welcome to the Apollo Launch Project!",
                "target_path": f"/projects/{apollo_project.id}/announcements"
            },
            {
                "user_id": developer_user.id,
                "project_id": mobile_project.id,
                "kind": "task_assigned",
                "title": "New Task Assigned",
                "body": f"You have been assigned to: 'Optimize list rendering performance'",
                "target_path": f"/projects/{mobile_project.id}/tasks"
            }
        ]
        
        for n_data in notifications_data:
            res_n = await db.execute(select(Notification).where(
                Notification.user_id == n_data["user_id"],
                Notification.title == n_data["title"],
                Notification.body == n_data["body"]
            ))
            if not res_n.scalar_one_or_none():
                notif = Notification(
                    id=uuid4(),
                    user_id=n_data["user_id"],
                    project_id=n_data["project_id"],
                    kind=n_data["kind"],
                    title=n_data["title"],
                    body=n_data["body"],
                    target_path=n_data["target_path"],
                    is_email_backed=False
                )
                db.add(notif)

        await db.commit()
        print("Seeding completed successfully!")
    await engine.dispose()

if __name__ == "__main__":
    asyncio.run(seed())
