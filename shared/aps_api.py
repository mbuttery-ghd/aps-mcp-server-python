"""
APS API client functions shared across all MCP servers.

Makes direct HTTP calls to APS REST APIs using httpx. Each function
accepts an access token and returns a list of plain dicts, keeping
the MCP tools decoupled from any particular authentication mechanism.
"""

import httpx

APS_BASE_URL = "https://developer.api.autodesk.com"


async def list_oss_buckets(access_token: str) -> list[dict]:
    """List OSS buckets accessible with the given token.

    See: https://aps.autodesk.com/en/docs/data/v2/reference/http/buckets-GET/
    """
    async with httpx.AsyncClient() as client:
        response = await client.get(
            f"{APS_BASE_URL}/oss/v2/buckets",
            headers={"Authorization": f"Bearer {access_token}"},
        )
        response.raise_for_status()
        data = response.json()
    return [
        {
            "bucket_key": item["bucketKey"],
            "created_date": item["createdDate"],
            "policy_key": item["policyKey"],
        }
        for item in data.get("items", [])
    ]


async def list_oss_objects(access_token: str, bucket_key: str) -> list[dict]:
    """List objects stored in an OSS bucket.

    See: https://aps.autodesk.com/en/docs/data/v2/reference/http/buckets-:bucketKey-objects-GET/
    """
    async with httpx.AsyncClient() as client:
        response = await client.get(
            f"{APS_BASE_URL}/oss/v2/buckets/{bucket_key}/objects",
            headers={"Authorization": f"Bearer {access_token}"},
        )
        response.raise_for_status()
        data = response.json()
    return [
        {
            "object_key": item["objectKey"],
            "object_id": item["objectId"],
            "size": item["size"],
        }
        for item in data.get("items", [])
    ]


async def list_hubs(access_token: str) -> list[dict]:
    """List accessible hubs.

    See: https://aps.autodesk.com/en/docs/data/v2/reference/http/hubs-GET/
    """
    async with httpx.AsyncClient() as client:
        response = await client.get(
            f"{APS_BASE_URL}/project/v1/hubs",
            headers={"Authorization": f"Bearer {access_token}"},
        )
        response.raise_for_status()
        data = response.json()
    return [
        {
            "id": hub["id"],
            "name": hub["attributes"]["name"],
            "region": hub["attributes"].get("region", ""),
        }
        for hub in data.get("data", [])
    ]


async def list_projects(access_token: str, hub_id: str) -> list[dict]:
    """List projects in a hub.

    See: https://aps.autodesk.com/en/docs/data/v2/reference/http/hubs-hub_id-projects-GET/
    """
    async with httpx.AsyncClient() as client:
        response = await client.get(
            f"{APS_BASE_URL}/project/v1/hubs/{hub_id}/projects",
            headers={"Authorization": f"Bearer {access_token}"},
        )
        response.raise_for_status()
        data = response.json()
    return [
        {
            "id": project["id"],
            "name": project["attributes"]["name"],
        }
        for project in data.get("data", [])
    ]
