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
MAX_STEPS = 10
SYSTEM = (
    "You are the assistant inside 'Listener', a private personal audio-journal "
    "dashboard. You help manage speakers, tasks, transcripts, and what's been learned "
    "about people. "
    "Be PROACTIVE: when answering needs data — a person's profile/preferences, their "
    "tasks, recent activity — CALL THE TOOLS to fetch it yourself; do NOT say you don't "
    "know or ask the user for permission first. For ANY question about a person (gift "
    "ideas, what they like/dislike, their birthday, etc.), call get_speaker to load "
    "their profile, then answer from it. Never invent ids; look them up first. "
    "Use the earlier turns of THIS conversation for context (e.g. if the user says "
    "'yes', act on what you just offered). Be concise and friendly; after an action, "
    "confirm in one short sentence."
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


async def run(messages):
    """Stream the agent loop over `messages` (a live conversation list that already
    holds the system prompt, prior turns, and the new user message). Appends the
    assistant/tool turns back onto `messages` so the caller keeps the history."""
    try:
        async with streamablehttp_client(MCP_URL) as (read, write, _):
            async with ClientSession(read, write) as session:
                await session.initialize()
                tools = _tool_schemas((await session.list_tools()).tools)
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
                    else:
                        # Loop exhausted while still calling tools — force ONE final answer
                        # (no tools) so the user always gets a response (ADR-054).
                        if tool_calls:
                            final = messages + [{"role": "user", "content":
                                "Stop calling tools. Answer my question now using what you've "
                                "already gathered above."}]
                            payload = {"model": MODEL, "messages": final, "stream": True,
                                       "think": False, "options": {"temperature": 0}}
                            async with http.stream("POST", OLLAMA_URL, json=payload) as r:
                                async for line in r.aiter_lines():
                                    if not line.strip():
                                        continue
                                    m = json.loads(line).get("message", {})
                                    if m.get("content"):
                                        yield _sse("token", text=m["content"])
        yield _sse("done")
    except Exception as e:  # noqa: BLE001
        yield _sse("error", message=str(e))
