# Quillet MCP — agent bootstrap

You are connecting a coding agent (e.g. Cursor) to this Quillet newsletter over **MCP**.

The human gave you a Quillet base URL and an admin password. Do the rest.

## What is already running

This document is served by the **same Quillet process** that hosts the JSON API.
MCP is Streamable HTTP on this instance — not a local process you launch.

MCP URL for this newsletter:

`{base_url}/{newsletter_slug}/mcp/`

(trailing slash preferred; bare `/mcp` redirects)

## Values for this instance

| Field | Value |
|---|---|
| Base URL | `{base_url}` |
| Newsletter slug | `{newsletter_slug}` |
| MCP URL | `{base_url}/{newsletter_slug}/mcp/` |
| Admin username | `admin` (default) |
| Admin password | *(the human already told you — do not invent one)* |
| OpenAPI (REST) | `{base_url}/{newsletter_slug}/api/openapi.yaml` |
| This bootstrap | `{base_url}/{newsletter_slug}/api/mcp.md` |

## Configure Cursor (project MCP)

Create or update `.cursor/mcp.json` in the workspace (ask the human to approve the write).

Use a **URL** connection — not a `command` / `args` Python launcher:

```json
{{
  "mcpServers": {{
    "quillet": {{
      "url": "{base_url}/{newsletter_slug}/mcp/",
      "headers": {{
        "Authorization": "Basic <base64 of admin:PASSWORD>"
      }}
    }}
  }}
}}
```

Replace `PASSWORD` with the password the human gave you, then base64-encode `admin:PASSWORD` for the header (e.g. `echo -n 'admin:secret' | base64`).

After the file is saved, refresh MCP in Cursor (Settings → MCP) or start a new agent session until `quillet` shows as connected.

## Tools

`list_posts` · `get_post` · `create_post` · `update_post` · `publish_post` · `send_test` · `send_post` · `list_subscribers`

`list_posts` and `list_subscribers` return a page (`next_cursor`). Pass `cursor` to fetch older rows; loop until `next_cursor` is null. `send_post` emails the full confirmed list (not a page).

Same operations as the JSON API. Read the OpenAPI document if you need request/response shapes.

## If you only need REST

Skip MCP. Use Basic Auth against the JSON API and read:

`{base_url}/{newsletter_slug}/api/openapi.yaml`
