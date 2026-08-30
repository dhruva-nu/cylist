"""Authentication: credentials, scopes and the request principal."""

from app.auth.dependencies import SESSION_COOKIE, current_principal, require
from app.auth.principal import Principal
from app.auth.scopes import ALL_SCOPES, Scope

__all__ = ["ALL_SCOPES", "SESSION_COOKIE", "Principal", "Scope", "current_principal", "require"]
