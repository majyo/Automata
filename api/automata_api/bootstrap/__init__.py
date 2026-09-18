"""Bootstrap: dependency assembly, configuration and lifecycle.

The bootstrap package is the only place that knows how to choose concrete
implementations. Business modules receive what they need through explicit
construction instead of reaching for module level globals.
"""

from automata_api.bootstrap.container import AppContainer, create_container
from automata_api.bootstrap.lifecycle import app_lifespan
from automata_api.bootstrap.settings import AppSettings, load_settings

__all__ = [
    "AppContainer",
    "AppSettings",
    "app_lifespan",
    "create_container",
    "load_settings",
]
