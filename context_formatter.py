import json
from typing import Any


def _format_arg(arg) -> str:
    if isinstance(arg, str):
        try:
            return json.dumps(json.loads(arg), indent=2)
        except json.JSONDecodeError:
            return arg
    return json.dumps(arg, indent=2)


def convert_llm_request_to_messages(llm_request: Any) -> list[dict[str, Any]]:
    messages = []
    if not hasattr(llm_request, "contents"):
        return messages
    for content in llm_request.contents:
        if not hasattr(content, "parts") or not content.parts:
            continue
        for part in content.parts:
            if hasattr(part, "text") and part.text:
                messages.append({"role": content.role or "user", "content": part.text})
            if hasattr(part, "function_call") and part.function_call:
                messages.append(
                    {
                        "role": "assistant",
                        "tool_calls": [
                            {
                                "function": {
                                    "name": part.function_call.name,
                                    "arguments": str(part.function_call.args),
                                }
                            }
                        ],
                    }
                )
            if hasattr(part, "function_response") and part.function_response:
                messages.append(
                    {
                        "role": "tool",
                        "content": str(part.function_response.response),
                    }
                )
    return messages


def convert_llm_request_to_tools(llm_request: Any) -> list[dict[str, Any]]:
    tools = []
    if not hasattr(llm_request, "tools_dict") or not llm_request.tools_dict:
        return tools
    for name, tool in llm_request.tools_dict.items():
        raw = getattr(tool, "raw_mcp_tool", None)
        if raw:
            tools.append(
                {
                    "function": {
                        "name": name,
                        "description": getattr(raw, "description", ""),
                        "parameters": getattr(raw, "inputSchema", {}),
                    }
                }
            )
    return tools


def get_system_instruction(llm_request: Any) -> str | None:
    if not hasattr(llm_request, "config") or not llm_request.config:
        return None
    if not hasattr(llm_request.config, "system_instruction"):
        return None
    sys_inst = llm_request.config.system_instruction
    if isinstance(sys_inst, str):
        return sys_inst
    if hasattr(sys_inst, "parts") and sys_inst.parts:
        for part in sys_inst.parts:
            if hasattr(part, "text") and part.text:
                return part.text
    return None


def format_context_window(
    turn: int,
    is_final: bool,
    messages: list,
    tools: list,
    system_instruction: str | None = None,
) -> str:
    title = f"Turn {turn}"
    if is_final:
        title += " (final)"

    lines = []
    lines.append(f"## {title}")
    lines.append("")

    if system_instruction:
        lines.append("### System Instruction")
        lines.append(f"```\n{system_instruction}\n```")
        lines.append("")

    history_label = "Full Conversation History" if turn > 1 else "Conversation History"
    lines.append(f"### {history_label}")

    for msg in messages:
        role = msg.get("role", "unknown")
        content = msg.get("content", "")
        lines.append(f"**{role.upper()}**")
        if content:
            lines.append(f"```\n{content}\n```")
        if "tool_calls" in msg and msg["tool_calls"]:
            for tc in msg["tool_calls"]:
                func = tc.get("function", {})
                name = func.get("name", "")
                args = func.get("arguments", "")
                lines.append(f"```\n{name}({_format_arg(args)})\n```")
        if "tool_call_id" in msg:
            lines.append(f"```\ntool_response: {msg.get('content')}\n```")
        lines.append("")

    lines.append("### Available Tools")
    for tool in tools:
        func = tool.get("function", {})
        name = func.get("name", "unknown")
        desc = func.get("description", "")
        params = func.get("parameters", {})

        lines.append(f"**{name}**: {desc}")
        lines.append("Parameters:")
        lines.append(f"```json\n{json.dumps(params, indent=2)}\n```")
        lines.append("")

    return "\n".join(lines)
