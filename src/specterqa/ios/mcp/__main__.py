"""Entry point for ``python -m specterqa.ios.mcp``.

Starts the SpecterQA iOS MCP server on stdio transport, enabling use as:
    python -m specterqa.ios.mcp
"""

from specterqa.ios.mcp.server import serve

serve()
