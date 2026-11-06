"""Toolbox for the agent: mock Personal-Planer API with every call logged to api_calls.

    from ragbot.tools import TOOLS, call_tool, describe
    call_tool("mitarbeiter_suchen", {"suchbegriff": "Weber"})
"""
from ragbot.tools.registry import TOOLS, Context, Tool, ToolError, call_tool, describe
from ragbot.tools import personal_planer  # noqa: F401  (registers the tools on import)
from ragbot.tools import wissen  # noqa: F401  (knowledge base search as a tool)
