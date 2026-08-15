"""Minimal MCP stdio server exposing one tool: `run` — execute a command inside the task's sandbox.

Claude runs on the host (login stays here) with its normal Bash disabled; the ONLY way it can run a
command is this tool, which docker-execs into the credential-free, no-network box. So the agent's
commands are contained even though the agent itself runs on the host. Container id comes in via env.

Pure stdlib (Python 3.9) — the official `mcp` package needs 3.10+. MCP stdio = newline-delimited
JSON-RPC 2.0; we handle the handshake (initialize), tools/list and tools/call.
"""

import json
import os
import subprocess
import sys

CID = os.environ.get("KONTINUUM_SANDBOX_CID", "")

TOOL = {
    "name": "run",
    "description": "Run a shell command inside the sandbox container (the repo is mounted at /work). "
                   "Returns the exit code and combined stdout/stderr. No network is available.",
    "inputSchema": {
        "type": "object",
        "properties": {"command": {"type": "string", "description": "Shell command to run"}},
        "required": ["command"],
    },
}


def _run(command: str):
    """Return (text, is_error). is_error marks infra failures (not a normal non-zero command exit)."""
    if not CID:
        return "error: sandbox container id not set (KONTINUUM_SANDBOX_CID missing)", True
    try:
        p = subprocess.run(["docker", "exec", "-w", "/work", CID, "sh", "-c", command],
                           capture_output=True, text=True, timeout=600)
    except subprocess.TimeoutExpired:
        return "error: command timed out after 600s", True
    return f"exit={p.returncode}\n{p.stdout}{p.stderr}", False


def _handle(msg: dict):
    method = msg.get("method")
    if method == "initialize":
        return {"protocolVersion": msg.get("params", {}).get("protocolVersion", "2024-11-05"),
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "kontinuum-sandbox", "version": "0.1"}}
    if method == "tools/list":
        return {"tools": [TOOL]}
    if method == "tools/call":
        args = msg.get("params", {}).get("arguments", {})
        text, is_error = _run(args.get("command", ""))
        return {"content": [{"type": "text", "text": text}], "isError": is_error}
    return {}   # ping / anything else: empty result


def main() -> None:
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except ValueError:
            continue
        if "id" not in msg:            # notification -> no response
            continue
        try:
            resp = {"jsonrpc": "2.0", "id": msg["id"], "result": _handle(msg)}
        except Exception as e:         # never crash the server; report the error to the client
            resp = {"jsonrpc": "2.0", "id": msg["id"], "error": {"code": -32000, "message": str(e)}}
        sys.stdout.write(json.dumps(resp) + "\n")
        sys.stdout.flush()


if __name__ == "__main__":
    main()
