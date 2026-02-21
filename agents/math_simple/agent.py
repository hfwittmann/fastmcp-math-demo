"""
ADK Agent for Web UI - Uses simple functions (no MCP).

This agent can be run with: adk web
Then access http://127.0.0.1:8000 in your browser.
"""

from google.adk.agents import LlmAgent
from google.adk.models.lite_llm import LiteLlm


# Simple math functions (no MCP)
def add(a: float, b: float) -> float:
    """Add two numbers together."""
    return a + b


def subtract(a: float, b: float) -> float:
    """Subtract b from a."""
    return a - b


def multiply(a: float, b: float) -> float:
    """Multiply two numbers together."""
    return a * b


def divide(a: float, b: float) -> float:
    """Divide a by b."""
    if b == 0:
        raise ValueError("Cannot divide by zero")
    return a / b


def create_math_agent_simple():
    """Create the math agent with simple functions."""

    # Create the agent with simple function tools
    agent = LlmAgent(
        name="math_simple_agent",
        model=LiteLlm(model="gemini/gemini-2.5-flash"),
        instruction="You are a helpful math assistant. Use the available tools to calculate answers.",
        tools=[add, subtract, multiply, divide],
    )

    return agent


# For ADK web to discover the agent
root_agent = create_math_agent_simple()
