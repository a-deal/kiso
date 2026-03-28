---
paths:
  - "engine/gateway/**"
  - "scripts/run_v1_api.py"
---

# API Rules

- The API runs as a native launchd service (com.kiso.v1-api), NOT Docker. See hub/decisions/2026-03-27-docker-to-launchd.md.
- After changing any API code, restart: launchctl unload/load ~/Library/LaunchAgents/com.kiso.v1-api.plist
- The v1 API (engine/gateway/v1_api.py) serves iOS sync. The legacy /api/{tool_name} routes serve Milo's MCP tools. Both must be registered in run_v1_api.py.
- Always test locally before pushing. Deploy rule is a hard rule.
- Auth: admin token in gateway.yaml. Per-user tokens in token_persons map.
