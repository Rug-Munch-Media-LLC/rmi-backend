"""RMI Auth — Shared authentication library for all RMI services.

This package provides:
- ``jwt_validation.validate_token`` — Validate a JWT and return claims
- ``jwt_validation.create_token`` — Create a signed JWT (rmi-backend only)
- ``jwt_validation.extract_token_from_request`` — Extract JWT from request
- ``jwt_validation.get_current_user`` — FastAPI dependency (optional auth)
- ``jwt_validation.require_auth`` — FastAPI dependency (required auth)
- ``jwt_validation.get_jwks`` — JWKS endpoint data

Import from rmi-backend:
    from app.auth import validate_token, require_auth

Import from other services (walletpress, pryscraper):
    # Add rmi-backend to sys.path, then:
    from rmi_auth import validate_token, extract_token_from_request
    # Or copy this package and import locally.
"""

from app.auth.jwt_validation import (  # noqa: F401
    create_token,
    extract_token_from_request,
    get_current_user,
    get_jwks,
    require_auth,
    validate_token,
)
