"""
ADK Agent for Web UI - Uses MCP tools via McpToolset.

This agent can be run with: adk web
Then access http://127.0.0.1:8000 in your browser.
"""

from google.adk.agents import LlmAgent
from google.adk.tools.mcp_tool import McpToolset
from google.adk.tools.mcp_tool.mcp_session_manager import StdioConnectionParams
from google.adk.models.lite_llm import LiteLlm
from mcp import StdioServerParameters


def create_math_agent():
    """Create the math agent with MCP tools."""

    # Configure MCP server connection
    server_params = StdioServerParameters(command=".venv/bin/python", args=["main.py"])

    # Create MCP toolset
    mcp_toolset = McpToolset(
        connection_params=StdioConnectionParams(server_params=server_params)
    )

    # Create the agent - use LiteLlm
    agent = LlmAgent(
        name="math_mcp_agent",
        model=LiteLlm(model="gemini/gemini-2.5-flash"),
        instruction="You are a helpful math assistant. Use the available tools to calculate answers.",
        tools=[mcp_toolset],
    )

    return agent


# For ADK web to discover the agent
root_agent = create_math_agent()
