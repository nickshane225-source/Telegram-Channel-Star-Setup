import importlib
import inspect
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional
from fastapi import APIRouter, FastAPI
from loguru import logger
from app.repositories.feature_flags import feature_flags_repo

PLUGINS_DIR = Path(__file__).resolve().parent


class PluginContext:
    """Context object provided to plugins when registering with the application."""

    def __init__(self, app: FastAPI):
        self.app = app
        self.nav_items: List[Dict[str, str]] = []
        self.dashboard_widgets: List[Dict[str, Any]] = []
        self.hooks: Dict[str, List[Callable]] = {}

    def add_nav_item(self, label: str, url: str, icon: str = "sparkles") -> None:
        """Add a link to the primary navigation sidebar."""
        self.nav_items.append({"label": label, "url": url, "icon": icon})
        logger.info(f"Plugin registered nav item: {label} -> {url}")

    def add_dashboard_widget(self, template_str: str, context_provider: Optional[Callable] = None) -> None:
        """Add a custom widget card to the dashboard."""
        self.dashboard_widgets.append({
            "template": template_str,
            "provider": context_provider
        })

    def add_hook(self, hook_name: str, callback: Callable) -> None:
        """Register a callback for an application lifecycle or processing hook."""
        if hook_name not in self.hooks:
            self.hooks[hook_name] = []
        self.hooks[hook_name].append(callback)
        logger.info(f"Registered plugin hook for '{hook_name}'")

    def register_router(self, router: APIRouter, prefix: str = "", tags: Optional[List[str]] = None) -> None:
        """Mount a new FastAPI router from the plugin."""
        self.app.include_router(router, prefix=prefix, tags=tags or ["plugins"])
        logger.info(f"Mounted plugin router at prefix: {prefix}")

    def run_hook(self, hook_name: str, value: Any, **kwargs) -> Any:
        """Run all registered callbacks for a hook sequentially."""
        current = value
        for cb in self.hooks.get(hook_name, []):
            try:
                current = cb(current, **kwargs)
            except Exception as e:
                logger.error(f"Error running hook '{hook_name}': {e}")
        return current


plugin_context: Optional[PluginContext] = None


async def load_plugins(app: FastAPI) -> PluginContext:
    """Discover, check feature flags, and auto-load all plugins in app/plugins/."""
    global plugin_context
    plugin_context = PluginContext(app)

    if not PLUGINS_DIR.exists():
        return plugin_context

    for item in PLUGINS_DIR.iterdir():
        if item.is_dir() and not item.name.startswith("__"):
            plugin_file = item / "plugin.py"
            if plugin_file.exists():
                plugin_name = item.name
                flag_key = f"plugin_{plugin_name}"

                # Ensure feature flag exists in database
                display_name = plugin_name.replace("_", " ").title()
                flag = await feature_flags_repo.register_flag(
                    key=flag_key,
                    name=f"Plugin: {display_name}",
                    description=f"Enables/disables the {display_name} plugin extension.",
                    default_enabled=True,
                )

                if not flag.enabled:
                    logger.info(f"Skipping plugin '{plugin_name}' (disabled in feature flags)")
                    continue

                try:
                    module_path = f"app.plugins.{plugin_name}.plugin"
                    module = importlib.import_module(module_path)

                    if hasattr(module, "register"):
                        register_func = getattr(module, "register")
                        if inspect.iscoroutinefunction(register_func):
                            await register_func(app, plugin_context)
                        else:
                            register_func(app, plugin_context)
                        logger.info(f"Successfully loaded plugin: {plugin_name}")
                    else:
                        logger.warning(f"Plugin {plugin_name} missing register(app, ctx) entrypoint")
                except Exception as exc:
                    logger.error(f"Failed to load plugin {plugin_name}: {exc}")

    return plugin_context


def get_plugin_context() -> Optional[PluginContext]:
    return plugin_context
