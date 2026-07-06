"""Bearer-token ASGI middleware for MCP endpoints.

The MCP subdomain is excluded from the upstream IAP (D-18),
so authentication is handled here via a static bearer token.
"""

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

        headers = dict(scope.get("headers", []))
        auth_header = headers.get(b"authorization", b"").decode("utf-8", "replace")
        expected = f"Bearer {self.token}"

        if not self.token or auth_header != expected:
            await send(
                {
                    "type": "http.response.start",
                    "status": 401,
                    "headers": [[b"content-type", b"application/json"]],
                }
            )
            await send(
                {
                    "type": "http.response.body",
                    "body": b'{"detail":"Unauthorized"}',
                }
            )
            return

        await self.app(scope, receive, send)
