"""
Test script demonstrating MCP server with LLM tool calling.

This script:
1. Starts the MCP math server as a subprocess
2. Connects to it via stdio
3. Gets available tools and sends them to an LLM
4. The LLM decides to call a tool, we execute it, and return the result
"""

import argparse
import asyncio
import json
import os
from datetime import datetime
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

# litellm provides a unified API for calling various LLM providers (OpenAI, Gemini, Anthropic, etc.)
from litellm import acompletion

# MCP (Model Context Protocol) client for connecting to MCP servers
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

# Load API keys from .env file
load_dotenv()

# litellm expects the API key in specific env vars, so we map ours
os.environ["GEMINI_API_KEY"] = os.environ.get("GOOGLE_API_KEY", "")


# Model aliases - litellm uses "provider/model" format
MODELS = {
    "gemini": "gemini/gemini-2.5-flash",
    "openai": "gpt-4o-mini",
}

OUTPUT_FILE = Path("llm_context_windows_llm.md")


def format_context_window(
    messages: list, tools: list, response, turn: int, is_final: bool = False
) -> str:
    title = f"Turn {turn}"
    if is_final:
        title += " (final)"
    lines = []
    lines.append(f"## {title}")
    lines.append("")

    history_label = "Full Conversation History" if turn > 1 else "Conversation History"
    lines.append(f"### {history_label}")
    lines.append("### Messages")
    for msg in messages:
        role = msg.get("role", "unknown")
        content = msg.get("content", "")
        lines.append(f"**{role.upper()}**")
        if content:
            lines.append(f"```\n{content}\n```")
        if "tool_calls" in msg and msg["tool_calls"]:
            for tc in msg["tool_calls"]:
                lines.append(
                    f"```\ntool_call: {tc['function']['name']}({tc['function']['arguments']})\n```"
                )
        if "tool_call_id" in msg:
            lines.append(f"```\ntool_response: {msg.get('content')}\n```")
        lines.append("")

    lines.append("### Available Tools")
    for tool in tools:
        func = tool.get("function", {})
        lines.append(f"**{func.get('name')}**: {func.get('description')}")
        lines.append("Parameters:")
        lines.append(f"```json\n{json.dumps(func.get('parameters'), indent=2)}\n```")
        lines.append("")

    return "\n".join(lines)


async def test_math(model_name: str):
    """
    Run the math test with the specified LLM model.

    Flow:
    1. Start MCP server as subprocess
    2. Connect to it and initialize session
    3. List available tools from the server
    4. Send user message + tool definitions to LLM
    5. If LLM calls a tool, execute it via MCP and return result
    """
    # Configure how to run the MCP server - here it's our own main.py as a stdio process
    server_params = StdioServerParameters(command=".venv/bin/python", args=["main.py"])

    # stdio_client spawns the server process and gives us read/write streams
    # The MCP server reads JSON-RPC messages from stdin and writes to stdout
    async with stdio_client(server_params) as (read, write):
        # ClientSession handles the MCP protocol (handshake, method calls, etc.)
        async with ClientSession(read, write) as session:
            # Initialize the MCP connection (sends initialize request)
            await session.initialize()

            # Ask the server what tools it provides
            tools = await session.list_tools()
            print("Available tools:", [t.name for t in tools.tools])

            # Convert MCP tool definitions to the format LLM APIs expect (OpenAI function calling style)
            mcp_tools = [
                {
                    "type": "function",
                    "function": {
                        "name": t.name,
                        "description": t.description or "",
                        "parameters": t.inputSchema,
                    },
                }
                for t in tools.tools
            ]

            # The conversation so far - just the user's question
            messages: list[dict[str, Any]] = [
                {
                    "role": "user",
                    "content": "Calculate 15 + 27, then multiply the result by 3, then subtract 50, then divide by 2",
                }
            ]

            # Resolve the model alias to actual model name
            model = MODELS.get(model_name, model_name)
            print(f"\nUsing model: {model}")

            # Call the LLM with the user message and available tools
            # tool_choice="auto" lets the model decide whether to use a tool
            response = await acompletion(
                model=model,
                messages=messages,
                tools=mcp_tools,
                tool_choice="auto",
            )

            query = messages[0]["content"]
            turn = 1
            context_window = format_context_window(messages, mcp_tools, response, turn)
            print(f"\n[Captured context window for turn {turn}]")

            print(f"\nUser: {query}")

            # Extract the LLM's response message
            assistant_message = response.choices[0].message  # type: ignore[union-attr]

            # If the LLM decided to call tools, execute them
            if assistant_message.tool_calls:
                # Add assistant message to conversation
                messages.append(
                    {
                        "role": "assistant",
                        "content": assistant_message.content,
                        "tool_calls": [
                            {
                                "id": tc.id,
                                "function": {
                                    "name": tc.function.name,
                                    "arguments": tc.function.arguments,
                                },
                            }
                            for tc in assistant_message.tool_calls
                        ],
                    }
                )

                # Execute each tool call
                for call in assistant_message.tool_calls:
                    tool_name = call.function.name  # type: ignore[union-attr]
                    tool_args = json.loads(call.function.arguments)
                    print(f"\nCalling tool: {tool_name}({tool_args})")

                    # Execute the tool via MCP and get result
                    result = await session.call_tool(tool_name, tool_args)  # type: ignore[arg-type]
                    tool_result_text = result.content[0].text  # type: ignore[union-attr]
                    print(f"Result: {tool_result_text}")

                    # Add tool result to conversation
                    messages.append(  # type: ignore[arg-type]
                        {
                            "role": "tool",
                            "tool_call_id": call.id,
                            "content": tool_result_text,
                        }
                    )

                # Continue calling LLM with tool results until no more tool calls
                while True:
                    turn += 1
                    response = await acompletion(
                        model=model,
                        messages=messages,
                        tools=mcp_tools,
                        tool_choice="auto",
                    )

                    context_window = format_context_window(
                        messages, mcp_tools, response, turn
                    )
                    print(f"\n[Captured context window for turn {turn}]")

                    assistant_message = response.choices[0].message  # type: ignore[union-attr]

                    if assistant_message.tool_calls:
                        # Add assistant message to conversation
                        messages.append(  # type: ignore[arg-type]
                            {
                                "role": "assistant",
                                "content": assistant_message.content,
                                "tool_calls": [
                                    {
                                        "id": tc.id,
                                        "function": {
                                            "name": tc.function.name,
                                            "arguments": tc.function.arguments,
                                        },
                                    }
                                    for tc in assistant_message.tool_calls
                                ],
                            }
                        )

                        # Execute each tool call
                        for call in assistant_message.tool_calls:
                            tool_name = call.function.name  # type: ignore[union-attr]
                            tool_args = json.loads(call.function.arguments)
                            print(f"\nCalling tool: {tool_name}({tool_args})")

                            result = await session.call_tool(tool_name, tool_args)  # type: ignore[arg-type]
                            tool_result_text = result.content[0].text  # type: ignore[union-attr]
                            print(f"Result: {tool_result_text}")

                            messages.append(  # type: ignore[arg-type]
                                {
                                    "role": "tool",
                                    "tool_call_id": call.id,
                                    "content": tool_result_text,
                                }
                            )
                    else:
                        # LLM answered without more tool calls
                        print(f"\nAssistant: {assistant_message.content}")
                        break

                # Write final context window to file
                final_context_window = format_context_window(
                    messages, mcp_tools, response, turn, is_final=True
                )
                output = []
                output.append(f"# LLM Context Windows (test_with_llm.py)\n")
                output.append(f"Generated: {datetime.now().isoformat()}\n")
                output.append(f"Query: {query}\n")
                output.append(f"Model: {model}\n")
                output.append("")
                output.append(final_context_window)
                OUTPUT_FILE.write_text("\n".join(output))
                print(f"\n[Context window written to {OUTPUT_FILE}]")
            else:
                # LLM answered directly without using tools
                print(f"\nAssistant: {assistant_message.content}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Test MCP math server with LLM")
    parser.add_argument(
        "--model",
        choices=["gemini", "openai"],
        default="gemini",
        help="LLM provider to use",
    )
    args = parser.parse_args()
    asyncio.run(test_math(args.model))
