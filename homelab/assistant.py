#!/usr/bin/env python3
"""Page assistant (ADR-020) — MCP client + Ollama tool-calling agent, streamed.

`run(user_msg)` is an async generator of Server-Sent-Event strings:
  {type:"token", text}        — assistant text delta
  {type:"tool", name, args}   — a tool call is starting
  {type:"tool_result", name, result}
  {type:"done"} / {type:"error", message}

Connects to the MCP server (127.0.0.1:8765) as a client to get + call tools, so
the assistant is genuinely MCP-driven. Small local model, fully local.
"""
import json

import httpx
from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client

MCP_URL = "http://127.0.0.1:8765/mcp"
OLLAMA_URL = "http://127.0.0.1:11434/api/chat"
MODEL = "qwen3:8b"   # reliable tool-calling. (qwen3:4b tested: loops + leaks
# reasoning on multi-tool flows — revisit with a tool-tuned small model later.)
MAX_STEPS = 6
SYSTEM = (
    "You are the assistant inside 'Listener', a private personal audio-journal "
    "dashboard. You help manage speakers, tasks, and transcripts. Use the tools to "
    "read and modify data — never invent ids; look them up first (list_speakers / "
    "list_tasks / list_unknown_speakers) before acting. Be concise and friendly. "
    "After an action, confirm what you did in one short sentence."
)


def _sse(type_, **data):
    return "data: " + json.dumps({"type": type_, **data}) + "\n\n"


def _tool_schemas(mcp_tools):
    return [{"type": "function", "function": {
        "name": t.name, "description": (t.description or "")[:1024],
        "parameters": t.inputSchema or {"type": "object", "properties": {}},
    }} for t in mcp_tools]


def _result_text(res):
    return "".join(getattr(c, "text", "") for c in (res.content or []))


async def run(user_msg: str):
    try:
        async with streamablehttp_client(MCP_URL) as (read, write, _):
            async with ClientSession(read, write) as session:
                await session.initialize()
                tools = _tool_schemas((await session.list_tools()).tools)
                messages = [{"role": "system", "content": SYSTEM},
                            {"role": "user", "content": user_msg}]
                async with httpx.AsyncClient(timeout=180) as http:
                    for _step in range(MAX_STEPS):
                        content, tool_calls = "", []
                        payload = {"model": MODEL, "messages": messages, "tools": tools,
                                   "stream": True, "think": False,
                                   "options": {"temperature": 0}}
                        async with http.stream("POST", OLLAMA_URL, json=payload) as r:
                            async for line in r.aiter_lines():
                                if not line.strip():
                                    continue
                                m = json.loads(line).get("message", {})
                                if m.get("content"):
                                    content += m["content"]
                                    yield _sse("token", text=m["content"])
                                if m.get("tool_calls"):
                                    tool_calls.extend(m["tool_calls"])
                        amsg = {"role": "assistant", "content": content}
                        if tool_calls:
                            amsg["tool_calls"] = tool_calls
                        messages.append(amsg)
                        if not tool_calls:
                            break
                        for tc in tool_calls:
                            fn = tc.get("function", {}).get("name", "")
                            args = tc.get("function", {}).get("arguments", {})
                            if isinstance(args, str):
                                try:
                                    args = json.loads(args)
                                except json.JSONDecodeError:
                                    args = {}
                            yield _sse("tool", name=fn, args=args)
                            try:
                                text = _result_text(await session.call_tool(fn, args))
                            except Exception as e:  # noqa: BLE001
                                text = json.dumps({"error": str(e)})
                            yield _sse("tool_result", name=fn, result=text[:600])
                            messages.append({"role": "tool", "tool_name": fn, "content": text})
        yield _sse("done")
    except Exception as e:  # noqa: BLE001
        yield _sse("error", message=str(e))
