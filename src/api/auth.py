"""IAP header trust auth dependency.

Reads X-Auth-Request-Email forwarded by an upstream Identity-Aware Proxy.
No in-app OIDC client — the proxy terminates Entra OIDC and forwards the email.
"""

import os

from fastapi import Header, HTTPException, status

ALLOWED_ENTRA_EMAIL = os.getenv("ALLOWED_ENTRA_EMAIL", "")


def require_auth(
    x_auth_request_email: str | None = Header(None, alias="X-Auth-Request-Email"),
) -> str:
    """Validate the upstream IAP email header.

    Returns the validated email address.

    Raises:
        HTTPException 403: If header is missing or email doesn't match ALLOWED_ENTRA_EMAIL.
    """
    if not ALLOWED_ENTRA_EMAIL:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="ALLOWED_ENTRA_EMAIL not configured",
        )

    if not x_auth_request_email:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Missing X-Auth-Request-Email header",
        )

    if x_auth_request_email.lower() != ALLOWED_ENTRA_EMAIL.lower():
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Access denied",
        )

    return x_auth_request_email
