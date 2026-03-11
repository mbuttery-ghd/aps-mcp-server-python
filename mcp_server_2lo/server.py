"""
MCP server using 2-legged (Client Credentials) OAuth.

This server demonstrates the simplest APS authentication approach: using a
client ID and secret to obtain a 2-legged access token via the OAuth 2.0
client credentials flow. The token is tied to the application identity, not
to any individual user.

2-legged OAuth is appropriate when:
- No user interaction is needed or possible
- The APIs only require application-level authorization (not user consent)
- The application owns the resources it accesses (e.g., its own OSS buckets)

This server exposes two tools against the APS Object Storage Service (OSS):
- list_buckets: List all buckets owned by the application
- list_objects: List objects stored in a specific bucket

APS docs: https://aps.autodesk.com/en/docs/oauth/v2/tutorials/get-2-legged-token/

Environment variables:
    APS_CLIENT_ID     - Your APS application client ID
    APS_CLIENT_SECRET - Your APS application client secret

Usage:
    uv run fastmcp run mcp_server_2lo/server.py --transport streamable-http --port 5000
"""

import os
import time

import httpx
from dotenv import load_dotenv
from fastmcp import FastMCP

from shared.aps_api import list_oss_buckets as _list_oss_buckets
from shared.aps_api import list_oss_objects as _list_oss_objects

load_dotenv()

APS_CLIENT_ID = os.environ["APS_CLIENT_ID"]
APS_CLIENT_SECRET = os.environ["APS_CLIENT_SECRET"]
APS_TOKEN_URL = "https://developer.api.autodesk.com/authentication/v2/token"

# In-memory token cache – reused across tool calls until expiry.
_token_cache: dict = {"access_token": None, "expires_at": 0.0}


async def _get_access_token() -> str:
    """Return a valid 2-legged access token, fetching a fresh one when expired."""
    if _token_cache["access_token"] and time.time() < _token_cache["expires_at"] - 60:
        return _token_cache["access_token"]

    async with httpx.AsyncClient() as client:
        response = await client.post(
            APS_TOKEN_URL,
            data={
                "grant_type": "client_credentials",
                "scope": "bucket:read data:read",
            },
            auth=(APS_CLIENT_ID, APS_CLIENT_SECRET),
        )
        response.raise_for_status()
        data = response.json()

    _token_cache["access_token"] = data["access_token"]
    _token_cache["expires_at"] = time.time() + data["expires_in"]
    return data["access_token"]


mcp = FastMCP(
    "APS 2LO Demo",
    instructions=(
        "This server provides access to the APS Object Storage Service (OSS) "
        "using 2-legged (client credentials) OAuth. It can list buckets and "
        "objects owned by the configured APS application."
    ),
)


@mcp.tool()
async def list_buckets() -> list[dict]:
    """List all OSS buckets owned by the configured APS application."""
    token = await _get_access_token()
    return await _list_oss_buckets(token)


@mcp.tool()
async def list_objects(bucket_key: str) -> list[dict]:
    """List objects stored in a specific OSS bucket.

    Args:
        bucket_key: The unique key identifying the OSS bucket.
    """
    token = await _get_access_token()
    return await _list_oss_objects(token, bucket_key)


if __name__ == "__main__":
    mcp.run(transport="streamable-http", host="0.0.0.0", port=5000)
