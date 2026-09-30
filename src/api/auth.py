"""Who the caller is, from the ingress in front of the app, and the admin/reader split.

Two ingresses, chosen by AUTH_SOURCE, and exactly one is trusted at a time:

* ``iap-header`` (default) — Dokploy: oauth2-proxy forwards X-Auth-Request-Email.
* ``cf-access`` — the Kubernetes cluster behind Cloudflare Access: the email comes
  from a VERIFIED Cf-Access-Jwt-Assertion (api/cf_access.py). X-Auth-Request-Email is
  then ignored, since nothing in front of the app sets it and anyone could.

No in-app OIDC client either way: the ingress terminates Entra and the app reads what
it asserts.

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

import hmac
import logging
import os
from dataclasses import dataclass

from fastapi import Depends, Header, HTTPException, Request, status

from api.cf_access import AccessKeyCache, AccessTokenError, verify_access_jwt

logger = logging.getLogger(__name__)

AUTH_SOURCES = frozenset({"iap-header", "cf-access"})

# One cache per process: the keys are the team's, not the request's.
_access_keys = AccessKeyCache()

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


def _configured_ingress_secret() -> str:
    """Optional defense in depth against direct container access.

    The email header is trusted VERBATIM, and the only thing standing between "can
    open a TCP connection to the app port" and "is admin" is the assumption that
    every request transited the ingress — an assumption a neighbour container on the
    same docker network does not have to honor (mcp_server/auth.py documents that
    reachability). When INGRESS_SHARED_SECRET is set, every request must also carry
    it in X-Ingress-Auth, which only the Traefik ingress is configured to inject —
    so a spoofed email header without the secret is refused before it names anyone.
    Unset = current behavior, so enabling it is an explicit two-sided move (env here,
    customRequestHeaders in the home-server repo's Traefik config).
    """
    return os.getenv("INGRESS_SHARED_SECRET", "")


def _configured_auth_source() -> str:
    """Which ingress to believe, read per request like the other settings."""
    return os.getenv("AUTH_SOURCE", "iap-header")


def _forbidden(detail: str) -> HTTPException:
    return HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=detail)


def _email_from_access(assertion: str | None) -> str:
    """The caller's email from a verified Access assertion, or 403.

    A service-token assertion verifies and carries no email (measured, home-server
    STATEFUL-APPS U2): that caller is a machine, and neither the admin nor a reader.
    """
    team = os.getenv("CF_ACCESS_TEAM_DOMAIN", "")
    audience = os.getenv("CF_ACCESS_AUD", "")
    if not team or not audience:
        # Fail closed: without both there is nothing to verify against.
        raise _forbidden("CF_ACCESS_TEAM_DOMAIN and CF_ACCESS_AUD must be configured")
    if not assertion:
        raise _forbidden("Missing Cf-Access-Jwt-Assertion header")
    try:
        claims = verify_access_jwt(
            assertion, team_domain=team, audience=audience, keys=_access_keys
        )
    except AccessTokenError as exc:
        # The reason is logged and never returned: it would tell a forger which check
        # to aim at. It never contains the token.
        logger.warning("Access assertion refused: %s", exc)
        raise _forbidden("Invalid Cf-Access-Jwt-Assertion") from None
    email = claims.get("email")
    if not isinstance(email, str) or not email:
        logger.warning("Access assertion without an email refused (service token?)")
        raise _forbidden("Access assertion carries no user identity")
    return email


def get_principal(
    x_auth_request_email: str | None = Header(None, alias="X-Auth-Request-Email"),
    x_ingress_auth: str | None = Header(None, alias="X-Ingress-Auth"),
    cf_access_jwt_assertion: str | None = Header(None, alias="Cf-Access-Jwt-Assertion"),
) -> Principal:
    """Resolve the caller from the upstream IAP header.

    Returns:
        The caller's Principal (admin if the email matches ALLOWED_ENTRA_EMAIL).

    Raises:
        HTTPException 403: no admin configured (fail closed), an unknown
            AUTH_SOURCE, no identity from the configured ingress (no header, or an
            Access assertion that does not verify or names no user), or a configured
            ingress secret that the request does not carry.
    """
    admin_email = _configured_admin_email()
    if not admin_email:
        # Fail closed. Without an admin there is nobody to grant write access to, and
        # granting read access to an unconfigured instance would be a surprise.
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="ALLOWED_ENTRA_EMAIL not configured",
        )

    ingress_secret = _configured_ingress_secret()
    if ingress_secret and not hmac.compare_digest(x_ingress_auth or "", ingress_secret):
        # Constant-time, and BEFORE the email is even read: a request without the
        # ingress-injected secret did not come through the ingress, so its email
        # header is an unverified claim.
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Missing or invalid ingress credential",
        )

    source = _configured_auth_source()
    if source not in AUTH_SOURCES:
        # A typo must not fall back to the header anyone can send.
        raise _forbidden("AUTH_SOURCE not recognised")

    if source == "cf-access":
        email = _email_from_access(cf_access_jwt_assertion)
    else:
        if not x_auth_request_email:
            raise _forbidden("Missing X-Auth-Request-Email header")
        email = x_auth_request_email

    return Principal(email=email, is_admin=email.lower() == admin_email.lower())


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
