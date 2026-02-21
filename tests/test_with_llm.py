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
import time
from datetime import datetime
from pathlib import Path
from typing import Any

# Import timing wrapper - measures import time
_import_start = time.perf_counter()

from dotenv import load_dotenv

# litellm - slow import (~20s)
from litellm import acompletion

# MCP (Model Context Protocol) client for connecting to MCP servers
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from context_formatter import format_context_window

_litellm_import_time = time.perf_counter() - _import_start

# Load API keys from .env file
load_dotenv()

# litellm expects the API key in specific env vars, so we map ours
os.environ["GEMINI_API_KEY"] = os.environ.get("GOOGLE_API_KEY", "")


# Model aliases - litellm uses "provider/model" format
MODELS = {
    "gemini": "gemini/gemini-2.5-flash",
    "openai": "openai/gpt-4o-mini",
}

OUTPUT_FILE = Path("llm_context_windows_llm.md")


async def test_math(model_name: str, query: str, query_index: int = 0):
    """
    Run the math test with the specified LLM model.

    Flow:
    1. Start MCP server as subprocess
    2. Connect to it and initialize session
    3. List available tools from the server
    4. Send user message + tool definitions to LLM
    5. If LLM calls a tool, execute it via MCP and return result
    """
    overall_start = time.perf_counter()

    output_file = Path(f"llm_context_windows_llm_q{query_index + 1}.md")

    # Configure how to run the MCP server - here it's our own main.py as a stdio process
    server_params = StdioServerParameters(command=".venv/bin/python", args=["main.py"])

    # Time MCP server startup
    server_start = time.perf_counter()

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

            server_elapsed = time.perf_counter() - server_start
            print(f"[MCP server startup: {server_elapsed:.2f}s]")

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
                    "content": query,
                }
            ]

            # Resolve the model alias to actual model name
            model = MODELS.get(model_name, model_name)
            print(f"\nUsing model: {model}")

            # Capture execution start time (after MCP server is ready)
            exec_start = time.perf_counter()

            # Call the LLM with the user message and available tools
            # tool_choice="auto" lets the model decide whether to use a tool
            llm_start = time.perf_counter()
            response = await acompletion(
                model=model,
                messages=messages,
                tools=mcp_tools,
                tool_choice="auto",
            )
            llm_elapsed = time.perf_counter() - llm_start
            print(f"[LLM call took {llm_elapsed:.2f}s]")

            query = messages[0]["content"]
            turn = 1
            context_window = format_context_window(turn, False, messages, mcp_tools)
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
                                "type": "function",
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
                    tool_start = time.perf_counter()
                    result = await session.call_tool(tool_name, tool_args)  # type: ignore[arg-type]
                    tool_elapsed = time.perf_counter() - tool_start
                    tool_result_text = result.content[0].text  # type: ignore[union-attr]
                    print(f"Result: {tool_result_text} ({tool_elapsed:.2f}s)")

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
                    llm_start = time.perf_counter()
                    response = await acompletion(
                        model=model,
                        messages=messages,
                        tools=mcp_tools,
                        tool_choice="auto",
                    )
                    llm_elapsed = time.perf_counter() - llm_start
                    print(f"[LLM call took {llm_elapsed:.2f}s]")

                    context_window = format_context_window(
                        turn, False, messages, mcp_tools
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
                                        "type": "function",
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

                            tool_start = time.perf_counter()
                            result = await session.call_tool(tool_name, tool_args)  # type: ignore[arg-type]
                            tool_elapsed = time.perf_counter() - tool_start
                            tool_result_text = result.content[0].text  # type: ignore[union-attr]
                            print(f"Result: {tool_result_text} ({tool_elapsed:.2f}s)")

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

                exec_elapsed = time.perf_counter() - exec_start
                total_elapsed = time.perf_counter() - overall_start
                print(f"\n{'=' * 50}")
                print(f"Setup:              {server_elapsed:.2f}s")
                print(f"LLM execution:      {exec_elapsed:.2f}s")
                print(f"Total (from async): {total_elapsed:.2f}s")

                # Write final context window to file
                final_context_window = format_context_window(
                    turn, True, messages, mcp_tools
                )
                output = []
                output.append(f"# LLM Context Windows (test_with_llm.py)\n")
                output.append(f"Generated: {datetime.now().isoformat()}\n")
                output.append(f"Query {query_index + 1}: {query}\n")
                output.append(f"Model: {model}\n")
                output.append(f"Timing:\n")
                output.append(f"- Setup: {server_elapsed:.2f}s\n")
                output.append(f"- LLM execution: {exec_elapsed:.2f}s\n")
                output.append(f"- Total (from async): {total_elapsed:.2f}s\n")
                output.append("")
                output.append(final_context_window)
                output_file.write_text("\n".join(output))
                print(f"\n[Context window written to {output_file}]")
            else:
                # LLM answered directly without using tools
                print(f"\nAssistant: {assistant_message.content}")


if __name__ == "__main__":
    start_time = time.perf_counter()
    parser = argparse.ArgumentParser(description="Test MCP math server with LLM")
    parser.add_argument(
        "--model",
        choices=["gemini", "openai"],
        default="gemini",
        help="LLM provider to use",
    )
    parser.add_argument(
        "--query",
        type=str,
        nargs="+",
        default=[
            "Calculate 15 + 27, then multiply the result by 3, then subtract 50, then divide by 2",
            "Calculate 16 + 28, then multiply the result by 4, then subtract 50, then divide by 2",
        ],
        help="Math query/queries to send to the LLM (can specify multiple)",
    )
    args = parser.parse_args()

    print(f"[litellm import: {_litellm_import_time:.2f}s]")

    for i, query in enumerate(args.query):
        print(f"\n{'=' * 60}")
        print(f"Query {i + 1}/{len(args.query)}: {query}")
        print(f"{'=' * 60}")
        asyncio.run(test_math(args.model, query, i))

    print(f"\n[Total wallclock time: {time.perf_counter() - start_time:.2f}s]")
