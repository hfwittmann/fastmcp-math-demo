"""
Test script demonstrating MCP server with Google ADK.

This script uses Google's Agent Development Kit (ADK) to connect to the MCP
math server and run the same test as test_with_llm.py but with a different approach.

Key differences from litellm approach:
- ADK handles the tool calling loop automatically (no manual execution needed)
- You define tools as part of the agent, ADK manages the interaction
- More agentic - the agent can make multiple tool calls in sequence
"""

import argparse
import asyncio
import json
import os
from datetime import datetime
from typing import Optional
from pathlib import Path

from dotenv import load_dotenv
from google.adk.agents import LlmAgent
from google.adk.runners import Runner
from google.adk.agents.callback_context import CallbackContext
from google.adk.models.llm_request import LlmRequest
from google.adk.models.llm_response import LlmResponse

# Google ADK components
from google.adk.sessions import InMemorySessionService
from google.adk.tools.mcp_tool import McpToolset
from google.adk.tools.mcp_tool.mcp_session_manager import StdioConnectionParams
from google.genai import types
from mcp import StdioServerParameters

load_dotenv()

# Output file for LLM context windows
OUTPUT_FILE = Path("llm_context_windows_adk.md")


def format_context_window(
    llm_request: LlmRequest, turn_number: int, is_final: bool = False
) -> str:
    """Format a single LLM context window as markdown."""
    title = f"Turn {turn_number}"
    if is_final:
        title += " (final)"
    lines = []
    lines.append(f"## {title}")
    lines.append("")
    if llm_request.config and llm_request.config.system_instruction:
        lines.append("### System Instruction")
        sys_inst = llm_request.config.system_instruction
        if isinstance(sys_inst, str):
            lines.append(f"```\n{sys_inst}\n```")
        elif hasattr(sys_inst, "parts") and sys_inst.parts:  # type: ignore[union-attr]
            for part in sys_inst.parts:  # type: ignore[union-attr]
                if hasattr(part, "text") and part.text:
                    lines.append(f"```\n{part.text}\n```")
        lines.append("")

    # Contents (conversation history)
    history_label = (
        "Full Conversation History" if turn_number > 1 else "Conversation History"
    )
    lines.append(f"### {history_label}")
    for i, content in enumerate(llm_request.contents):
        if not content.parts:  # type: ignore[union-attr]
            continue
        role = content.role or "unknown"
        lines.append(f"**{role.upper()}**")
        for part in content.parts:  # type: ignore[union-attr]
            if hasattr(part, "text") and part.text:
                lines.append(f"```\n{part.text}\n```")
            if hasattr(part, "function_call") and part.function_call:
                fc = part.function_call
                lines.append(
                    f"```\nfunction_call: {fc.name}({json.dumps(fc.args)})\n```"
                )
            if hasattr(part, "function_response") and part.function_response:
                fr = part.function_response
                lines.append(f"```\nfunction_response: {fr.response}\n```")
        lines.append("")

    # Tools
    if llm_request.tools_dict:
        lines.append("### Available Tools")
        for name, tool in llm_request.tools_dict.items():
            raw = getattr(tool, "raw_mcp_tool", None)
            if raw and hasattr(raw, "inputSchema"):
                lines.append(f"**{name}**")
                lines.append(f"Description: {getattr(raw, 'description', 'N/A')}")
                lines.append("")
                lines.append(
                    f"Parameters:\n```json\n{json.dumps(raw.inputSchema, indent=2)}\n```"
                )
            else:
                desc = getattr(tool, "description", "N/A")
                lines.append(f"**{name}**: {desc}")
            lines.append("")
        lines.append("")

    return "\n".join(lines)


# Global state to accumulate context windows
_context_windows: list = []
_llm_requests: list = []


async def inspect_llm_request(
    callback_context: CallbackContext,
    llm_request: LlmRequest,
) -> Optional[LlmResponse]:
    """Callback to capture what's sent to the LLM."""
    global _context_windows, _llm_requests

    turn_number = len(_llm_requests) + 1
    _llm_requests.append(llm_request)
    formatted = format_context_window(llm_request, turn_number)
    _context_windows.append(formatted)

    print(f"\n[Captured context window for turn {turn_number}]")
    return None


async def test_math_with_adk(model_name: str):
    """
    Run the math test using Google ADK.

    Flow:
    1. Create MCPToolset that connects to our math MCP server
    2. Create an LlmAgent with those tools
    3. Create a session and runner
    4. Send the user query and let ADK handle tool calling automatically
    """

    # Configure how to connect to the MCP server (same as before - stdio)
    server_params = StdioServerParameters(command=".venv/bin/python", args=["main.py"])

    # MCPToolset wraps the MCP server and exposes its tools to ADK
    # StdioConnectionParams tells ADK how to connect (via stdio in this case)
    mcp_toolset = McpToolset(
        connection_params=StdioConnectionParams(server_params=server_params)
    )

    # Define the model to use
    model = "gemini-2.5-flash" if model_name == "gemini" else "gpt-4o-mini"
    print(f"Using model: {model}")

    # Create the agent with the MCP tools
    # ADK will automatically decide when to call tools based on the prompt
    agent = LlmAgent(
        name="math_agent",
        model=model,
        instruction="You are a helpful math assistant. Use the available tools to calculate answers.",
        tools=[mcp_toolset],  # Add MCP tools here
        before_model_callback=inspect_llm_request,
    )

    # Create services needed to run the agent
    session_service = InMemorySessionService()
    runner = Runner(
        app_name="math_agent",
        agent=agent,
        session_service=session_service,
    )

    # Create a session (conversation context)
    user_id = "test_user"
    session_id = "test_session"
    session = await session_service.create_session(
        app_name=agent.name,
        user_id=user_id,
        session_id=session_id,
    )

    # The query to send
    query = "Calculate 15 + 27, then multiply the result by 3, then subtract 50, then divide by 2"
    print(f"\nUser: {query}")

    # Run the agent - ADK handles the tool calling loop automatically!
    # It will call tools as needed and return when done
    # Note: run_async returns an async generator, so we iterate over it
    final_content = None
    async for event in runner.run_async(
        user_id=user_id,
        session_id=session_id,
        new_message=types.Content(role="user", parts=[types.Part(text=query)]),
    ):
        if event.content and event.content.parts:
            for part in event.content.parts:
                if part.text:
                    final_content = part.text

    # Print the final response
    print(f"\nAssistant: {final_content}")

    # Write final context window to file
    if _llm_requests:
        final_turn = len(_llm_requests)
        final_window = format_context_window(
            _llm_requests[-1], final_turn, is_final=True
        )
        output = []
        output.append(f"# LLM Context Windows\n")
        output.append(f"Generated: {datetime.now().isoformat()}\n")
        output.append(f"Query: {query}\n")
        output.append(f"Model: {model}\n")
        output.append("")
        output.append(final_window)

        OUTPUT_FILE.write_text("\n".join(output))
        print(f"\n[Context window written to {OUTPUT_FILE}]")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Test MCP math server with Google ADK")
    parser.add_argument(
        "--model",
        choices=["gemini", "openai"],
        default="gemini",
        help="LLM provider to use",
    )
    args = parser.parse_args()
    asyncio.run(test_math_with_adk(args.model))
