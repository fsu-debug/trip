# TRIP MCP Server

MCP (Model Context Protocol) server for TRIP — lets AI assistants (Claude, OpenClaw, etc.) manage trips via tools.

## Tools (22)

Trips: create, list, get, update, delete, link_places
Days: add, update, delete
Items: add, update, delete
Places: create, list, update, delete
Categories: list, create
Packing: add_packing_item
Checklist: add_checklist_item
Sharing: share_trip, invite_member

## Setup

```bash
docker compose up -d
```

Environment variables:

- `TRIP_API_URL` — TRIP backend URL (default: http://localhost:8080)
- `TRIP_API_TOKEN` — TRIP API key (recommended when OIDC is enabled; generate in TRIP Settings)
- `TRIP_USERNAME` — Login username (fallback when `TRIP_API_TOKEN` is not set)
- `TRIP_PASSWORD` — Login password (fallback when `TRIP_API_TOKEN` is not set)

When OIDC is configured, password login is disabled on the TRIP backend. Use `TRIP_API_TOKEN` instead:

1. Log in to TRIP via the browser (OIDC)
2. Open Settings and generate an API key
3. Configure the MCP server with `TRIP_API_TOKEN`

## Connect

Claude Code (`~/.claude/settings.json`):

```json
{
  "mcpServers": {
    "trip": {
      "type": "http",
      "url": "http://localhost:3001/mcp"
    }
  }
}
```
