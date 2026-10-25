from datetime import datetime, timedelta
import pytest
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from app.models.post import PostStatus
from app.repositories.posts import posts_repo
from app.services.posts import PostService
from app.services.scheduler import ScheduleService


@pytest.mark.asyncio
async def test_scheduler_lifecycle(sample_photos):
    """Test scheduling, pausing, resuming, and canceling jobs."""
    sched = AsyncIOScheduler()
    service = ScheduleService(sched=sched)

    # Add job
    run_date = datetime.utcnow() + timedelta(hours=2)
    service.schedule_post(post_id="post_test_123", scheduled_at=run_date)

    job = sched.get_job("post_publish_post_test_123")
    assert job is not None
    assert job.id == "post_publish_post_test_123"

    # Pause job
    paused = service.pause_post_schedule("post_test_123")
    assert paused is True

    # Resume job
    resumed = service.resume_post_schedule("post_test_123")
    assert resumed is True

    # Cancel job
    canceled = service.cancel_post_schedule("post_test_123")
    assert canceled is True
    assert sched.get_job("post_publish_post_test_123") is None


@pytest.mark.asyncio
async def test_reload_jobs_from_database(sample_photos):
    """Test that all pending scheduled posts are reloaded into scheduler on startup."""
    sched = AsyncIOScheduler()
    service = ScheduleService(sched=sched)
    post_svc = PostService(repo=posts_repo)

    # Create 2 scheduled posts in DB
    run_1 = datetime.utcnow() + timedelta(hours=1)
    run_2 = datetime.utcnow() + timedelta(hours=5)

    p1 = await post_svc.create_post(
        title="Reload Post 1",
        caption="",
        photos=sample_photos,
        star_count=50,
        status=PostStatus.SCHEDULED,
        scheduled_at=run_1
    )
    p2 = await post_svc.create_post(
        title="Reload Post 2",
        caption="",
        photos=sample_photos,
        star_count=100,
        status=PostStatus.SCHEDULED,
        scheduled_at=run_2
    )

    count = await service.reload_jobs_from_db()
    assert count >= 2
    assert sched.get_job(f"post_publish_{p1.id}") is not None
    assert sched.get_job(f"post_publish_{p2.id}") is not None
