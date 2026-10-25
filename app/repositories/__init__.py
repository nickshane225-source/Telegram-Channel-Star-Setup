from app.repositories.posts import posts_repo, PostRepository
from app.repositories.templates import templates_repo, TemplateRepository
from app.repositories.sales import sales_repo, SalesRepository
from app.repositories.purchases import purchases_repo, PurchaseRepository
from app.repositories.settings import settings_repo, SettingsRepository
from app.repositories.activity import activity_repo, ActivityRepository
from app.repositories.feature_flags import feature_flags_repo, FeatureFlagsRepository
from app.repositories.users import users_repo, UserRepository

__all__ = [
    "posts_repo",
    "PostRepository",
    "templates_repo",
    "TemplateRepository",
    "sales_repo",
    "SalesRepository",
    "purchases_repo",
    "PurchaseRepository",
    "settings_repo",
    "SettingsRepository",
    "activity_repo",
    "ActivityRepository",
    "feature_flags_repo",
    "FeatureFlagsRepository",
    "users_repo",
    "UserRepository",
]
