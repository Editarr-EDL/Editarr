"""The Users, permissions, and media-access routers (MU-15).

Every handler here decides through the shared evaluator.
"""

from fastapi import APIRouter

from . import (
    account_deletion_api,
    account_link_api,
    media_access_api,
    ownership_api,
    permissions_api,
    users_admin_api,
    users_api,
)

ROUTERS: tuple[APIRouter, ...] = (
    users_admin_api.router,
    users_api.router,
    ownership_api.router,
    permissions_api.router,
    media_access_api.router,
    account_link_api.router,
    account_deletion_api.router,
)
