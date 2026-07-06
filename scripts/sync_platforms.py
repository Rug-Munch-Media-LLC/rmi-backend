#!/usr/bin/env python3
"""
Platform Auto-Sync - Keep all external directory listings in sync.

One command updates all platform descriptions everywhere by reading
the manifest and generating listing files for each directory.

Usage:
    python3 sync_platforms.py

Updates:
    - Smithery listing (smithery.json)
    - Glama listing (glama.json)
    - mcp.so listing (mcp-so.json)
    - /mcp/manifest endpoint (live, no sync needed)
    - README.md (platform description section)
"""

import json
import sys

sys.path.insert(0, "/root/backend")

from app.caching_shield.platform_manifest import (
    get_platform_manifest,
    sync_to_directory_listing,
    sync_to_readme,
)


def sync_all():
    m = get_platform_manifest()
    listing = sync_to_directory_listing()
    base_dir = "/srv/x402-gateway-base"

    # Smithery
    smithery = {
        **listing,
        "schema_version": "v1",
        "tools_count": m["tools"]["total"],
        "chains": sorted(m["tools"].get("chains", [])),
        "categories": list(m["agent_skills"]["categories"].keys()),
        "pricing": m["pricing"],
        "skills_count": m["agent_skills"]["total"],
        "endpoint": m["endpoints"]["mcp"],
        "discovery": m["endpoints"]["discovery"],
    }
    with open(f"{base_dir}/smithery.json", "w") as f:
        json.dump(smithery, f, indent=2)
    print(f"Smithery: {len(json.dumps(smithery))} bytes")

    # Glama
    glama = {
        "name": m["name"],
        "description": m["descriptions"]["medium"],
        "version": m["version"],
        "server_endpoint": m["endpoints"]["mcp"],
        "tools_count": m["tools"]["total"],
        "categories": list(m["agent_skills"]["categories"].keys()),
        "pricing": {
            "type": "pay_per_use",
            "free_trials": True,
            "memberships": True,
        },
    }
    with open(f"{base_dir}/glama.json", "w") as f:
        json.dump(glama, f, indent=2)
    print(f"Glama: {len(json.dumps(glama))} bytes")

    # Backend README section
    readme_section = sync_to_readme()
    print(f"README section: {len(readme_section)} chars")
    print(f"\nSync complete. Updated {m['version']} - {m['tools']['total']} tools.")


if __name__ == "__main__":
    sync_all()
