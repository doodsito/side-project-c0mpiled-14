from fastmcp import FastMCP
from fastmcp.server.dependencies import get_http_request

mcp = FastMCP("Wallie")


def connector_key() -> str:
    return get_http_request().headers.get("x-wallie-key", "")


@mcp.tool
def whoami() -> dict:
    """Temporary step-1 spike tool: returns the connector key read from the path."""
    return {"connector_key": connector_key()}


class KeyFromPath:
    """ASGI middleware: rewrites /mcp/<key>[/] -> /mcp, moves <key> into a header."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] == "http":
            parts = [p for p in scope["path"].split("/") if p]
            if len(parts) == 2 and parts[0] == "mcp":
                headers = [(k, v) for k, v in scope["headers"] if k != b"x-wallie-key"]
                headers.append((b"x-wallie-key", parts[1].encode()))
                scope = {**scope, "path": "/mcp", "raw_path": b"/mcp", "headers": headers}
        await self.app(scope, receive, send)


app = KeyFromPath(mcp.http_app(path="/mcp", stateless_http=True))
# run: uv run uvicorn server:app --port 8000
