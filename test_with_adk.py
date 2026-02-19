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
from typing import Any, Optional
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
from google.adk.models.lite_llm import LiteLlm
from google.genai import types
from mcp import StdioServerParameters

from context_formatter import (
    format_context_window,
    convert_llm_request_to_messages,
    convert_llm_request_to_tools,
    get_system_instruction,
)

load_dotenv()

# Output file for LLM context windows
OUTPUT_FILE = Path("llm_context_windows_adk.md")


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

    messages = convert_llm_request_to_messages(llm_request)
    tools = convert_llm_request_to_tools(llm_request)
    system_instruction = get_system_instruction(llm_request)

    formatted = format_context_window(
        turn_number, False, messages, tools, system_instruction
    )
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
    if model_name == "gemini":
        model = LiteLlm(model="gemini/gemini-2.5-flash")
        display_model = "gemini-2.5-flash"
    else:
        model = LiteLlm(model="openai/gpt-4o-mini")
        display_model = "gpt-4o-mini"

    print(f"Using model: {display_model}")

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
        last_request = _llm_requests[-1]

        messages = convert_llm_request_to_messages(last_request)
        tools = convert_llm_request_to_tools(last_request)
        system_instruction = get_system_instruction(last_request)

        final_window = format_context_window(
            final_turn, True, messages, tools, system_instruction
        )
        output = []
        output.append(f"# LLM Context Windows\n")
        output.append(f"Generated: {datetime.now().isoformat()}\n")
        output.append(f"Query: {query}\n")
        output.append(f"Model: {display_model}\n")
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
