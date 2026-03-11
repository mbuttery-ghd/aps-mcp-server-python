"""
MCP server using standard 3-legged (Authorization Code) OAuth.

This server demonstrates the standard OAuth 2.0 authorization code flow for
APS. The user must explicitly visit an authorization URL, sign in with their
Autodesk account, and grant consent before the server can access their data.

3-legged OAuth is appropriate when:
- The server acts on behalf of real, named users
- Explicit user consent is required by policy or regulation
- There is no pre-established trust relationship (no SSA)

Token flow:
  1. Call list_hubs or list_projects — if not yet authenticated, you get back
     an authorization URL instead of data.
  2. Visit that URL in a browser, sign in, and click "Allow".
  3. Autodesk redirects to /callback on this server with a one-time code.
  4. The callback exchanges the code for access + refresh tokens and stores
     them keyed by the mcp-session-id header value.
  5. Subsequent tool calls use the access token, automatically refreshing it
     via the refresh token when it expires.

APS docs: https://aps.autodesk.com/en/docs/oauth/v2/tutorials/get-3-legged-token/

This server exposes two tools against the APS Data Management API:
- list_hubs:     List all hubs accessible to the authenticated user
- list_projects: List projects within a hub

Environment variables:
    APS_CLIENT_ID       - Your APS application client ID
    APS_CLIENT_SECRET   - Your APS application client secret
    MCP_PORT            - Port this server listens on (default: 5002)

Note: The redirect URI "http://localhost:<MCP_PORT>/callback" must be
registered in your APS application's callback URL list.

Usage:
    uv run fastmcp run mcp_server_3lo/server.py --transport streamable-http --port 5002
"""

import os
import time
import urllib.parse

import httpx
from dotenv import load_dotenv
from fastmcp import FastMCP, Context
from starlette.requests import Request
from starlette.responses import HTMLResponse

from shared.aps_api import list_hubs as _list_hubs
from shared.aps_api import list_projects as _list_projects

load_dotenv()

APS_CLIENT_ID = os.environ["APS_CLIENT_ID"]
APS_CLIENT_SECRET = os.environ["APS_CLIENT_SECRET"]
MCP_PORT = int(os.getenv("MCP_PORT", "5002"))
REDIRECT_URI = f"http://localhost:{MCP_PORT}/callback"
APS_AUTH_URL = "https://developer.api.autodesk.com/authentication/v2/authorize"
APS_TOKEN_URL = "https://developer.api.autodesk.com/authentication/v2/token"
SCOPES = "data:read"

# One-way bridge: the OAuth callback (no MCP context) writes here; the tool
# reads it once, migrates the tokens into ctx session state, then clears it.
_pending_tokens: dict[str, dict] = {}


def _build_auth_url(session_id: str) -> str:
    """Build the Autodesk authorization URL, embedding session_id in state."""
    params = {
        "response_type": "code",
        "client_id": APS_CLIENT_ID,
        "redirect_uri": REDIRECT_URI,
        "scope": SCOPES,
        "state": session_id,
    }
    return f"{APS_AUTH_URL}?{urllib.parse.urlencode(params)}"


async def _exchange_code(code: str) -> dict:
    """Exchange an authorization code for access and refresh tokens."""
    async with httpx.AsyncClient() as client:
        response = await client.post(
            APS_TOKEN_URL,
            data={
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": REDIRECT_URI,
            },
            auth=(APS_CLIENT_ID, APS_CLIENT_SECRET),
        )
        response.raise_for_status()
        return response.json()


async def _get_valid_token(ctx: Context) -> str | None:
    """Return a valid access token from session state, refreshing when expired.

    On the first call after OAuth completes, migrates tokens from the pending
    bridge dict into ctx session state and removes them from the bridge.
    """
    store = await ctx.get_state("tokens")

    if store is None:
        pending = _pending_tokens.pop(ctx.session_id, None)
        if pending is None:
            return None
        await ctx.set_state("tokens", pending)
        store = pending

    if store["access_token"] and time.time() < store["expires_at"] - 60:
        return store["access_token"]

    if not store["refresh_token"]:
        return None

    try:
        async with httpx.AsyncClient() as client:
            response = await client.post(
                APS_TOKEN_URL,
                data={
                    "grant_type": "refresh_token",
                    "refresh_token": store["refresh_token"],
                    "scope": SCOPES,
                },
                auth=(APS_CLIENT_ID, APS_CLIENT_SECRET),
            )
            response.raise_for_status()
            data = response.json()
        store = {
            "access_token": data["access_token"],
            "refresh_token": data.get("refresh_token", store["refresh_token"]),
            "expires_at": time.time() + data.get("expires_in", 3600),
        }
        await ctx.set_state("tokens", store)
        return store["access_token"]
    except httpx.HTTPStatusError:
        return None


mcp = FastMCP(
    "APS 3LO Demo",
    instructions=(
        "This server provides access to the APS Data Management API using "
        "standard 3-legged OAuth. If you are not yet authenticated, "
        "list_hubs and list_projects will return an authorization URL — "
        "open it in a browser, sign in, and then retry the tool call."
    ),
)


@mcp.custom_route("/callback", methods=["GET"])
async def oauth_callback(request: Request) -> HTMLResponse:
    """Handle the OAuth redirect from Autodesk.

    The session ID is carried through the OAuth 'state' parameter so tokens
    are stored under the correct MCP session.
    """
    params = dict(request.query_params)

    if "error" in params:
        error = params["error"]
        description = params.get("error_description", "Unknown error")
        return HTMLResponse(
            f"<h1>Authentication failed</h1><p>{error}: {description}</p>",
            status_code=400,
        )

    if "code" not in params:
        return HTMLResponse("<h1>Missing authorization code</h1>", status_code=400)

    session_id = params.get("state", "")
    if not session_id:
        return HTMLResponse("<h1>Missing session ID</h1>", status_code=400)

    try:
        tokens = await _exchange_code(params["code"])
        _pending_tokens[session_id] = {
            "access_token": tokens["access_token"],
            "refresh_token": tokens.get("refresh_token"),
            "expires_at": time.time() + tokens.get("expires_in", 3600),
        }
        return HTMLResponse(
            "<h1>Authentication successful!</h1>"
            "<p>You can close this window and return to your MCP client.</p>"
        )
    except Exception as exc:
        return HTMLResponse(
            f"<h1>Token exchange failed</h1><p>{exc}</p>",
            status_code=500,
        )


@mcp.tool()
async def list_hubs(ctx: Context) -> list[dict] | dict:
    """List all hubs accessible to the authenticated user.

    If not yet authenticated, returns a dict with an 'auth_url' key — open
    that URL in a browser to complete the OAuth flow, then call this tool again.
    """
    token = await _get_valid_token(ctx)
    if not token:
        return {
            "auth_required": True,
            "auth_url": _build_auth_url(ctx.session_id),
            "message": (
                "Open auth_url in a browser to authenticate, "
                "then call list_hubs again."
            ),
        }
    return await _list_hubs(token)


@mcp.tool()
async def list_projects(hub_id: str, ctx: Context) -> list[dict] | dict:
    """List all projects in a hub.

    Args:
        hub_id: The ID of the hub (e.g. "b.xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx").

    If not yet authenticated, returns a dict with an 'auth_url' key — open
    that URL in a browser to complete the OAuth flow, then call this tool again.
    """
    token = await _get_valid_token(ctx)
    if not token:
        return {
            "auth_required": True,
            "auth_url": _build_auth_url(ctx.session_id),
            "message": (
                "Open auth_url in a browser to authenticate, "
                "then call list_projects again."
            ),
        }
    return await _list_projects(token, hub_id)


if __name__ == "__main__":
    mcp.run(transport="streamable-http", host="0.0.0.0", port=MCP_PORT)
