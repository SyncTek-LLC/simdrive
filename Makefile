.PHONY: llms

## Regenerate llms.txt from the MCP server's registered tool registry.
## Run after adding or modifying any @mcp.tool() decorator in server.py.
llms:
	python3 scripts/generate_llms_txt.py --write
