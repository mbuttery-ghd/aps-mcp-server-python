"""
MCP server using Secure Service Accounts (SSA).

This server demonstrates APS Secure Service Account authentication. SSA lets a
server-side identity obtain 3-legged OAuth tokens without any user interaction
by using a JWT assertion signed with an RSA private key registered against the
service account.

SSA is appropriate when:
- The server needs to act on behalf of a service identity (not an end user)
- The APIs require 3-legged authorization (e.g., Data Management / BIM 360)
- The service account has been pre-authorized to access the target resources
- Fully automated, unattended workflows are required

Token flow:
  1. Build a short-lived JWT assertion signed with the SSA private key.
  2. POST the assertion to the APS token endpoint using the
     urn:ietf:params:oauth:grant-type:jwt-bearer grant type.
  3. Receive a 3-legged access token valid for use with user-context APIs.
  4. Cache the token until it expires, then repeat.

APS docs: https://aps.autodesk.com/en/docs/ssa/v1/developers_guide/overview/

This server exposes two tools against the APS Data Management API:
- list_hubs:     List all hubs accessible to the service account
- list_projects: List projects within a hub

Environment variables:
    APS_CLIENT_ID       - Your APS application client ID
    APS_CLIENT_SECRET   - Your APS application client secret
    APS_SSA_ID          - The Secure Service Account ID
    APS_SSA_KEY_ID      - The key pair ID registered with the service account
    APS_SSA_KEY_BASE64  - Base64-encoded RSA private key (PEM format)

Usage:
    uv run fastmcp run mcp_server_ssa/server.py --transport streamable-http --port 5001
"""

import base64
import os
import time

import httpx
import jwt
from dotenv import load_dotenv
from fastmcp import FastMCP

from shared.aps_api import list_hubs as _list_hubs
from shared.aps_api import list_projects as _list_projects

load_dotenv()

APS_CLIENT_ID = os.environ["APS_CLIENT_ID"]
APS_CLIENT_SECRET = os.environ["APS_CLIENT_SECRET"]
APS_SSA_ID = os.environ["APS_SSA_ID"]
APS_SSA_KEY_ID = os.environ["APS_SSA_KEY_ID"]
APS_SSA_PRIVATE_KEY = base64.b64decode(os.environ["APS_SSA_KEY_BASE64"]).decode("utf-8")
APS_TOKEN_URL = "https://developer.api.autodesk.com/authentication/v2/token"

# In-memory token cache - reused across tool calls until expiry.
_token_cache: dict = {"access_token": None, "expires_at": 0.0}


async def _get_access_token() -> str:
    """Return a valid SSA-issued access token, refreshing when expired.

    Builds a JWT assertion signed with the SSA private key and exchanges it
    for a 3-legged access token at the APS token endpoint.
    """
    if _token_cache["access_token"] and time.time() < _token_cache["expires_at"] - 60:
        return _token_cache["access_token"]

    # The JWT assertion identifies the client application (iss) and the service
    # account (sub) and includes the requested scopes. It is signed with the
    # RSA private key whose public counterpart is registered with the SSA.
    now = int(time.time())
    payload = {
        "iss": APS_CLIENT_ID,
        "sub": APS_SSA_ID,
        "aud": APS_TOKEN_URL,
        "exp": now + 300,
        "iat": now,
        "scope": ["data:read"],
    }
    assertion = jwt.encode(
        payload,
        APS_SSA_PRIVATE_KEY,
        algorithm="RS256",
        headers={"kid": APS_SSA_KEY_ID, "alg": "RS256"},
    )

    credentials = base64.b64encode(
        f"{APS_CLIENT_ID}:{APS_CLIENT_SECRET}".encode()
    ).decode()
    async with httpx.AsyncClient() as client:
        response = await client.post(
            APS_TOKEN_URL,
            headers={
                "Authorization": f"Basic {credentials}",
                "Content-Type": "application/x-www-form-urlencoded",
            },
            data={
                "grant_type": "urn:ietf:params:oauth:grant-type:jwt-bearer",
                "assertion": assertion,
            },
        )
        response.raise_for_status()
        data = response.json()

    _token_cache["access_token"] = data["access_token"]
    _token_cache["expires_at"] = time.time() + data.get("expires_in", 3600)
    return data["access_token"]


mcp = FastMCP(
    "APS SSA Demo",
    instructions=(
        "This server provides access to the APS Data Management API using "
        "Secure Service Account (SSA) authentication. The SSA obtains 3-legged "
        "tokens without user interaction, suitable for automated server-to-server "
        "workflows where the service account has been pre-authorized to access "
        "the required resources."
    ),
)


@mcp.tool()
async def list_hubs() -> list[dict]:
    """List all hubs accessible to the configured Secure Service Account."""
    token = await _get_access_token()
    return await _list_hubs(token)


@mcp.tool()
async def list_projects(hub_id: str) -> list[dict]:
    """List all projects in a hub.

    Args:
        hub_id: The ID of the hub (e.g. "b.xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx").
    """
    token = await _get_access_token()
    return await _list_projects(token, hub_id)


if __name__ == "__main__":
    mcp.run(transport="streamable-http", host="0.0.0.0", port=5001)
