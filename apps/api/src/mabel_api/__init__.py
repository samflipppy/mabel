"""The FastAPI app. The `web` process group in fly.toml.

Serves three things: the portal API, the inbound webhooks, and the MCP server.
They share a process because they share a connection pool and none of them
holds a socket open — unlike `media`, which does, and is therefore its own
process so a portal deploy never drops a live call.
"""
