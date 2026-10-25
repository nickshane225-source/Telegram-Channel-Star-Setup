from app.routers.auth import router as auth_router
from app.routers.dashboard import router as dashboard_router
from app.routers.posts import router as posts_router
from app.routers.schedule import router as schedule_router
from app.routers.stats import router as stats_router
from app.routers.templates import router as templates_router
from app.routers.settings import router as settings_router
from app.routers.buyers import router as buyers_router

__all__ = [
    "auth_router",
    "dashboard_router",
    "posts_router",
    "schedule_router",
    "stats_router",
    "templates_router",
    "settings_router",
    "buyers_router",
]
