#!/usr/bin/env python3
"""Check HTTP, MCP negotiation, schemas and basic pack lifecycle using stdlib."""
import argparse
import json
from pathlib import Path
import tempfile
import time
import urllib.error
import urllib.request


def decode_response(raw):
    text = raw.decode("utf-8")
    if text.lstrip().startswith("{"):
        return json.loads(text)
    # Streamable HTTP may return JSON-RPC in Server-Sent Events.
    for event in text.replace("\r\n", "\n").split("\n\n"):
        data = "\n".join(line[5:].lstrip() for line in event.splitlines()
                         if line.startswith("data:"))
        if data:
            value = json.loads(data)
            if "result" in value or "error" in value:
                return value
    raise AssertionError("No JSON-RPC result in response")


class Client:
    def __init__(self, base):
        self.base = base.rstrip("/")
        self.session = None
        self.protocol = None
        self.counter = 0

    def request(self, method, params=None, notification=False):
        message = {"jsonrpc": "2.0", "method": method}
        if params is not None:
            message["params"] = params
        if not notification:
            self.counter += 1
            message["id"] = self.counter
        headers = {"Content-Type": "application/json",
                   "Accept": "application/json, text/event-stream"}
        if self.session:
            headers["Mcp-Session-Id"] = self.session
        if self.protocol:
            headers["MCP-Protocol-Version"] = self.protocol
        request = urllib.request.Request(self.base + "/mcp",
                                         data=json.dumps(message).encode(),
                                         headers=headers, method="POST")
        with urllib.request.urlopen(request, timeout=90) as response:
            self.session = response.headers.get("Mcp-Session-Id", self.session)
            raw = response.read()
        if notification:
            return None
        result = decode_response(raw)
        if "error" in result:
            raise AssertionError(result["error"])
        return result["result"]

    def tool(self, name, arguments=None):
        result = self.request("tools/call", {"name": name,
                                             "arguments": arguments or {}})
        if result.get("isError"):
            raise AssertionError(f"{name}: {result}")
        # RPFM can wrap an IPC Error inside an otherwise successful MCP result.
        for block in result.get("content", []):
            if block.get("type") == "text":
                try:
                    value = json.loads(block["text"])
                except json.JSONDecodeError:
                    continue
                if isinstance(value, dict) and ("Error" in value or "error" in value):
                    raise AssertionError(f"{name}: {value}")
        if "structuredContent" in result:
            return result["structuredContent"]
        for block in result.get("content", []):
            if block.get("type") == "text":
                try:
                    return json.loads(block["text"])
                except json.JSONDecodeError:
                    continue
        raise AssertionError(f"{name}: No structured tool result: {result}")

    def job(self, name, arguments=None, timeout=180):
        """API v2 slow tools return job states, even when already finished."""
        deadline = time.monotonic() + timeout
        status = self.tool(name, arguments)
        while status["state"] in {"queued", "running"}:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise AssertionError(f"{name}: Job timed out: {status}")
            status = self.tool("wait_for_job", {
                "job": status["job"], "timeout_secs": min(30, max(1, int(remaining)))})
        if status["state"] != "finished":
            raise AssertionError(f"{name}: Job did not finish successfully: {status}")
        return status["result"]


def check_pack_lifecycle(client, roundtrip=False):
    client.job("set_game", {"game": "warhammer_3", "rebuild_dependencies": False})
    status = client.tool("session_status")
    assert status["game"] == "warhammer_3" and status["schema_loaded"], status
    tables = client.tool("schema_tables", {"prefix": "twad_key_deletes"})
    assert tables["tables"].get("twad_key_deletes_tables"), tables
    before = {pack["key"] for pack in status["packs"]}
    created = client.tool("new_pack")
    pack_key = created["key"]
    try:
        after = {pack["key"] for pack in client.tool("session_status")["packs"]}
        assert after == before | {pack_key} and pack_key not in before, after
        if roundtrip:
            with tempfile.TemporaryDirectory(prefix=".rpfm-smoke-", dir=Path.cwd()) as folder:
                path = Path(folder) / "smoke.pack"
                container_path = "/work/" + Path(folder).name + "/smoke.pack"
                saved = client.tool("save_pack", {"pack": pack_key, "path": container_path})
                assert saved["key"] == pack_key and saved["path"] == container_path, saved
                assert path.read_bytes().startswith(b"PFH"), "Missing pack header"
                client.tool("close_pack", {"pack": pack_key})
                reopened = client.tool("open_pack", {"paths": [container_path]})
                pack_key = reopened["key"]
                assert reopened["path"] == container_path and reopened["file_count"] == 0, reopened
                # Close before removing the file, including on Windows hosts.
                client.tool("close_pack", {"pack": pack_key})
                pack_key = None
    finally:
        if pack_key is not None:
            client.tool("close_pack", {"pack": pack_key})
    remaining = {pack["key"] for pack in client.tool("session_status")["packs"]}
    assert remaining == before, remaining


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", default="http://127.0.0.1:45127")
    parser.add_argument("--expected-version", default="5.3.0")
    parser.add_argument("--roundtrip", action="store_true",
                        help="Save/reopen a temporary pack under the mounted job workspace")
    args = parser.parse_args()
    base = args.url.rstrip("/")
    for attempt in range(60):
        try:
            with urllib.request.urlopen(base + "/version", timeout=2) as response:
                version = json.load(response)
            break
        except (urllib.error.URLError, TimeoutError):
            if attempt == 59:
                raise
            time.sleep(1)
    assert version["version"] == args.expected_version, version
    with urllib.request.urlopen(base + "/sessions", timeout=5) as response:
        assert isinstance(json.load(response), list)
    client = Client(base)
    initialized = client.request("initialize", {
        "protocolVersion": "2025-03-26", "capabilities": {},
        "clientInfo": {"name": "rpfm-docker-smoke", "version": "1.0"}})
    client.protocol = initialized["protocolVersion"]
    client.request("notifications/initialized", notification=True)
    tools = []
    cursor = None
    while True:
        page = client.request("tools/list", {"cursor": cursor} if cursor else {})
        tools.extend(page["tools"])
        cursor = page.get("nextCursor")
        if not cursor:
            break
    names = {tool["name"] for tool in tools}
    required = {"set_game", "new_pack", "session_status", "schema_tables",
                "wait_for_job", "save_pack", "open_pack", "close_pack"}
    assert required <= names, f"Missing API v2 tools: {required - names}"
    check_pack_lifecycle(client, args.roundtrip)
    print(f"PASS: RPFM {version['version']}, HTTP, MCP ({len(tools)} tools), "
          "WH3 schema and pack lifecycle" + (", disk roundtrip" if args.roundtrip else ""))


if __name__ == "__main__":
    main()
