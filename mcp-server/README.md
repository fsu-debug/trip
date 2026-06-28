# TRIP MCP Server

MCP (Model Context Protocol) server for TRIP — lets AI assistants (Claude, Hermes, etc.) manage trips via tools.

## Tools (49)

**Trips:** create, list, get, get_trip_overview, get_trip_balance, update, delete, link_places, list_trip_places, add_place_to_trip, list_trip_members

**Days:** list_trip_days, get_day, add, update, delete, duplicate_day

**Items:** add, bulk_add_items, update, set_item_comment, delete

**Bookings:** add, update, delete

**Places:** search_places, import_place_from_google, geocode, search_nearby, get_route, create, list, get, update, delete

**Categories:** list, create

**Packing:** list, add, update, delete

**Checklist:** list, add, update, delete

**Sharing:** share_trip, invite_member

For large trips, prefer `get_trip_overview` + `get_day` over `get_trip`. Use `add_place_to_trip` instead of `link_places` to append places safely.

## Setup

```bash
docker compose up -d
```

Environment variables (set where the MCP process runs, not in the HTTP client config):

- `TRIP_API_URL` — TRIP backend URL (default: http://localhost:8080)
- `TRIP_API_TOKEN` — TRIP API key (recommended when OIDC is enabled; generate in TRIP Settings)
- `TRIP_USERNAME` — Login username (fallback when `TRIP_API_TOKEN` is not set)
- `TRIP_PASSWORD` — Login password (fallback when `TRIP_API_TOKEN` is not set)
- `TRIP_MCP_LOG_LEVEL` — `INFO` (default) or `DEBUG` for verbose tool/API logging

### Debugging

Logs go to stdout — view them with Docker:

```bash
docker logs -f <mcp-container>
```

Set `TRIP_MCP_LOG_LEVEL=DEBUG` to see HTTP details, tool arguments, and API request bodies.

On startup you should see `registered 47 MCP tools`. When Hermes connects, look for `MCP request: tools/list` and `tools/list -> 47 tools` in the logs. An empty `prompts/list` response is normal (this server has no prompts). After MCP updates, run `/reload-mcp` in Hermes.

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

Hermes Agent (`mcp_servers` config):

```yaml
mcp_servers:
  trip:
    url: "http://localhost:3001/mcp"
```