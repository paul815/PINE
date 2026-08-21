"""The /api/projects blueprint, split by domain.

Importing this package imports every route module, and importing a route
module is what attaches its routes to the blueprint. Same URLs as the
single projects.py this replaced.
"""

from .common import projects_bp

# Imported for their side effect: each module registers its routes on
# projects_bp at import time. Order does not matter. Import a specific view
# or helper from its own module, not from here.
from . import attachments, crud, export, recordings, segments, tags  # noqa: E402,F401

__all__ = ['projects_bp']
