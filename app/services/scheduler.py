import asyncio
from datetime import datetime, timedelta
from typing import Optional
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.date import DateTrigger
from apscheduler.triggers.interval import IntervalTrigger
from loguru import logger
from app.config import settings
from app.models.post import PostStatus
from app.repositories.posts import posts_repo
from app.repositories.settings import settings_repo

scheduler = AsyncIOScheduler()


async def execute_scheduled_post(post_id: str):
    """Job executed by APScheduler when a scheduled post is due."""
    from app.services.posts import post_service

    logger.info(f"Triggering scheduled publish for post {post_id}...")
    try:
        post = await posts_repo.get_by_id(post_id)
        if not post or post.status != PostStatus.SCHEDULED or post.deleted_at:
            logger.info(f"Skipping scheduled post {post_id} (status: {post.status if post else 'not found'})")
            return

        await post_service.publish_post(post_id)
        logger.info(f"Scheduled post {post_id} successfully published.")

    except Exception as exc:
        logger.error(f"Execution failed for scheduled post {post_id}: {exc}")
        post = await posts_repo.get_by_id(post_id)
        if post and post.retry_count < 3:
            # Exponential backoff: 2 min, 5 min, 15 min
            backoff_minutes = [2, 5, 15][min(post.retry_count, 2)]
            next_attempt_at = datetime.utcnow() + timedelta(minutes=backoff_minutes)
            logger.warning(
                f"Scheduling retry {post.retry_count + 1}/3 for post {post_id} "
                f"at {next_attempt_at} (backoff: {backoff_minutes}m)"
            )
            schedule_service.schedule_post(post_id, next_attempt_at)


async def check_due_posts_safety_poll():
    """Safety background job that runs every 60s to check for overdue scheduled posts."""
    try:
        now = datetime.utcnow()
        due_posts = await posts_repo.get_due_scheduled_posts(now)
        for post in due_posts:
            job_id = f"post_publish_{post.id}"
            if not scheduler.get_job(job_id):
                logger.info(f"Safety poll found overdue scheduled post {post.id}, triggering...")
                asyncio.create_task(execute_scheduled_post(post.id))
    except Exception as exc:
        logger.warning(f"Error in safety poll check: {exc}")


async def sync_stars_interval_job():
    """Periodic job to sync Telegram Stars earnings."""
    try:
        from app.services.stars import stars_service
        logger.debug("Running periodic Telegram Stars transaction sync...")
        await stars_service.sync_stars_transactions()
    except Exception as exc:
        logger.debug(f"Periodic stars sync: {exc}")


class ScheduleService:
    """Manager for scheduled jobs and APScheduler integration."""

    def __init__(self, sched: AsyncIOScheduler = scheduler):
        self.scheduler = sched

    def schedule_post(self, post_id: str, scheduled_at: datetime) -> bool:
        """Schedule a post to be published at scheduled_at."""
        job_id = f"post_publish_{post_id}"
        # Remove existing job if present
        if self.scheduler.get_job(job_id):
            self.scheduler.remove_job(job_id)

        # Ensure scheduled_at is in future, or run immediately if in past
        run_date = max(scheduled_at, datetime.utcnow())

        self.scheduler.add_job(
            execute_scheduled_post,
            trigger=DateTrigger(run_date=run_date),
            args=[post_id],
            id=job_id,
            replace_existing=True,
            misfire_grace_time=3600
        )
        logger.info(f"Scheduled post {post_id} for run at {run_date}")
        return True

    def cancel_post_schedule(self, post_id: str) -> bool:
        """Cancel an existing scheduled job for a post."""
        job_id = f"post_publish_{post_id}"
        if self.scheduler.get_job(job_id):
            self.scheduler.remove_job(job_id)
            logger.info(f"Removed scheduled job for post {post_id}")
            return True
        return False

    def pause_post_schedule(self, post_id: str) -> bool:
        """Pause a scheduled job."""
        job_id = f"post_publish_{post_id}"
        job = self.scheduler.get_job(job_id)
        if job:
            job.pause()
            logger.info(f"Paused scheduled job for post {post_id}")
            return True
        return False

    def resume_post_schedule(self, post_id: str) -> bool:
        """Resume a paused scheduled job."""
        job_id = f"post_publish_{post_id}"
        job = self.scheduler.get_job(job_id)
        if job:
            job.resume()
            logger.info(f"Resumed scheduled job for post {post_id}")
            return True
        return False

    async def trigger_now(self, post_id: str) -> None:
        """Manually trigger immediate execution of a scheduled post."""
        self.cancel_post_schedule(post_id)
        asyncio.create_task(execute_scheduled_post(post_id))

    async def reload_jobs_from_db(self) -> int:
        """Reload all pending scheduled jobs from MongoDB on server startup."""
        count = 0
        try:
            scheduled_posts = await posts_repo.get_all_scheduled()
            logger.info(f"Reloading {len(scheduled_posts)} scheduled posts from database...")
            for post in scheduled_posts:
                if post.scheduled_at:
                    self.schedule_post(post.id, post.scheduled_at)
                    count += 1
            logger.info(f"Successfully reloaded {count} scheduled jobs into APScheduler.")
        except Exception as exc:
            logger.error(f"Error reloading scheduled jobs from DB: {exc}")
        return count

    async def start(self) -> None:
        """Start scheduler and periodic maintenance jobs."""
        if not self.scheduler.running:
            self.scheduler.start()
            logger.info("APScheduler started.")

            # Periodic safety check every 60 seconds
            self.scheduler.add_job(
                check_due_posts_safety_poll,
                trigger=IntervalTrigger(seconds=60),
                id="safety_poll_job",
                replace_existing=True,
                misfire_grace_time=120
            )

            # Periodic stars sync
            sync_minutes = settings.STAR_SYNC_INTERVAL_MINUTES
            try:
                app_settings = await settings_repo.get_settings()
                sync_minutes = app_settings.sync_interval_minutes
            except Exception:
                pass

            self.scheduler.add_job(
                sync_stars_interval_job,
                trigger=IntervalTrigger(minutes=max(1, sync_minutes)),
                id="stars_sync_job",
                replace_existing=True,
                misfire_grace_time=300
            )

            # Reload jobs from DB
            await self.reload_jobs_from_db()

    def shutdown(self) -> None:
        """Shutdown scheduler cleanly."""
        if self.scheduler.running:
            self.scheduler.shutdown(wait=False)
            logger.info("APScheduler stopped.")


schedule_service = ScheduleService()
