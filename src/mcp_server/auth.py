"""Bearer-token ASGI middleware for MCP endpoints.

The /mcp path is excluded from the upstream Entra gate (path-scoped
router bypass, D-29), so authentication is handled here via a static
bearer token.

Fail-closed: if no token is configured the middleware answers 503 for
every request instead of letting traffic through — a misconfigured env
must never silently expose the endpoint (containers on the shared
docker network can reach the app directly, bypassing Traefik).
"""

import hmac
import os

MCP_BEARER_TOKEN = os.getenv("MCP_BEARER_TOKEN", "")


class BearerTokenMiddleware:
    """ASGI middleware that validates Authorization: Bearer <token>."""

    def __init__(self, app, token: str):
        self.app = app
        self.token = token

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        if not self.token:
            await self._respond(send, 503, b'{"detail":"MCP bearer token not configured"}')
            return

        headers = dict(scope.get("headers", []))
        auth_header = headers.get(b"authorization", b"").decode("utf-8", "replace")
        expected = f"Bearer {self.token}"

        if not hmac.compare_digest(auth_header.encode(), expected.encode()):
            await self._respond(send, 401, b'{"detail":"Unauthorized"}')
            return

        await self.app(scope, receive, send)

    @staticmethod
    async def _respond(send, status: int, body: bytes) -> None:
        await send(
            {
                "type": "http.response.start",
                "status": status,
                "headers": [[b"content-type", b"application/json"]],
            }
        )
        await send({"type": "http.response.body", "body": body})
