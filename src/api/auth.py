"""IAP header trust auth dependency, and the admin/reader role split.

Reads X-Auth-Request-Email forwarded by an upstream Identity-Aware Proxy.
No in-app OIDC client — the proxy terminates Entra OIDC and forwards the email.

Two roles, decided by ONE env var:

* **admin** — the address in ALLOWED_ENTRA_EMAIL. May do everything.
* **reader** — anybody else the upstream proxy let through. May read everything,
  may change nothing.

Membership is therefore delegated to Entra: oauth2-proxy already enforces the
tenant and OAUTH2_PROXY_EMAIL_DOMAINS, so "passed the gate" is a real boundary
and this app does not keep a second list of people. Before this split the app
403'd every non-admin — and because the ingress `entra-auth-errors` middleware
rewrites 401-403 into the oauth2-proxy sign-in response body while KEEPING the
403 status, a legitimate household member saw a white page with the word
"Found" on it (the body of a redirect the browser never follows).
"""

import os
from dataclasses import dataclass

from fastapi import Depends, Header, HTTPException, Request, status

# Methods that cannot change state. Everything else is admin-only — see
# require_admin_for_writes. Deny-by-default: the rule is keyed on the METHOD, so a
# new endpoint is gated the day it is written and nobody has to remember a decorator.
SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})


@dataclass(frozen=True)
class Principal:
    """Who is asking, and whether they may change anything."""

    email: str
    is_admin: bool


def _configured_admin_email() -> str:
    """The single admin address, read per request rather than at import.

    Import-time reads make the value untestable without reloading the module, and a
    container that gained the variable after boot would keep denying everyone.
    """
    return os.getenv("ALLOWED_ENTRA_EMAIL", "")


def get_principal(
    x_auth_request_email: str | None = Header(None, alias="X-Auth-Request-Email"),
) -> Principal:
    """Resolve the caller from the upstream IAP header.

    Returns:
        The caller's Principal (admin if the email matches ALLOWED_ENTRA_EMAIL).

    Raises:
        HTTPException 403: no admin configured (fail closed), or no header at all —
            the latter means the request did not come through the ingress.
    """
    admin_email = _configured_admin_email()
    if not admin_email:
        # Fail closed. Without an admin there is nobody to grant write access to, and
        # granting read access to an unconfigured instance would be a surprise.
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="ALLOWED_ENTRA_EMAIL not configured",
        )

    if not x_auth_request_email:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Missing X-Auth-Request-Email header",
        )

    return Principal(
        email=x_auth_request_email,
        is_admin=x_auth_request_email.lower() == admin_email.lower(),
    )


def require_auth(principal: Principal = Depends(get_principal)) -> str:
    """Validated caller email, for handlers that only want to log or display it."""
    return principal.email


def require_admin_for_writes(
    request: Request,
    principal: Principal = Depends(get_principal),
) -> Principal:
    """THE authorization gate: authenticate everyone, let only the admin write.

    Mounted once as a router-level dependency so it covers every route without being
    repeated per handler — a per-route decorator is a hole waiting for the next
    endpoint someone forgets to mark. tests/test_static_gates.py fails the build if
    this stops being the router's gate.

    Raises:
        HTTPException 403: a reader attempted a state-changing method.
    """
    if request.method in SAFE_METHODS or principal.is_admin:
        return principal

    raise HTTPException(
        status_code=status.HTTP_403_FORBIDDEN,
        detail="Du har läsbehörighet och kan inte ändra något här.",
    )
