---
sidebar_position: 1
description: Creating a place using the TRIP API
---

# Generating API Key

A TRIP API Key allows you to impersonate you for some tasks, such as creating places.  
This can be precious for scripting, allowing you to authenticate yourself through a header and not the Authentication flow with the `/login`.

To access the full API (e.g. trips, MCP server), exchange your key for a JWT:

```bash
curl -X POST http://localhost:8080/api/by_token/login \
  -H "X-Api-Token: YOUR_API_KEY"
```

Generating a key is done through the settings.

<img src="/trip/img/trip_api_key.png" alt="Generate your TRIP API Key" />
<div style={{textAlign: 'center'}}><sup>Generate your TRIP API Key</sup></div>
