"""Health Engine MCP server — exposes coaching tools to Claude Desktop."""

from mcp.server.fastmcp import FastMCP
from mcp_server.tools import register_tools, register_resources

mcp = FastMCP(
    "Health Engine",
    instructions=(
        "Health Engine is a local-first health intelligence system. "
        "When the user asks about their health, wants a check-in, or mentions health data, "
        "call `checkin` first — it returns a full coaching snapshot. "
        "Coach from the data: lead with what matters, connect metrics, give 1-2 nudges. "
        "Never dump raw JSON. When the user mentions a number (weight, BP), log it conversationally. "
        "For new users or 'set me up' or 'what should I measure?', call `onboard` first — "
        "it shows their full 20-metric coverage map, what's tracked vs missing, and ranked next steps. "
        "Walk through tiers, ask what they care about, then use `setup_profile` to collect basics. "
        "For setup, use `setup_profile` to create their config. "
        "The `health-engine://methodology` resource contains the full scoring methodology — "
        "reference it when users ask 'why do you measure this?' or 'how does scoring work?'."
    ),
)

register_tools(mcp)
register_resources(mcp)

def main():
    """Entry point for `health-engine` CLI command (PyPI)."""
    mcp.run()


if __name__ == "__main__":
    main()
