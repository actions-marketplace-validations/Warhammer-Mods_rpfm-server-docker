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
        return result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", default="http://127.0.0.1:45127")
    parser.add_argument("--expected-version", default="5.2.0")
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
    assert {"set_game_selected", "new_pack", "list_open_packs",
            "close_all_packs", "get_custom_table_list"} <= names, names
    client.tool("set_game_selected", {"game_name": "warhammer_3",
                                       "rebuild_dependencies": False})
    tables = client.tool("get_custom_table_list")
    # This API lists only custom start_pos_/twad_ tables, not regular DB tables.
    assert "twad_key_deletes_tables" in json.dumps(tables), tables
    before = client.tool("list_open_packs")
    created = client.tool("new_pack")
    after = client.tool("list_open_packs")
    assert after != before, "New pack did not appear in this session"
    if args.roundtrip:
        response = json.loads(next(block["text"] for block in created["content"]
                                   if block.get("type") == "text"))
        pack_key = response["String"]
        with tempfile.TemporaryDirectory(prefix=".rpfm-smoke-", dir=Path.cwd()) as folder:
            path = Path(folder) / "smoke.pack"
            container_path = "/work/" + Path(folder).name + "/smoke.pack"
            client.tool("save_pack_as", {"pack_key": pack_key, "path": container_path})
            assert path.read_bytes().startswith(b"PFH"), "Missing pack header"
            client.tool("close_all_packs")
            client.tool("open_packfiles", {"paths": [container_path]})
    client.tool("close_all_packs")
    print(f"PASS: RPFM {version['version']}, HTTP, MCP ({len(tools)} tools), "
          "WH3 schema and pack lifecycle" + (", disk roundtrip" if args.roundtrip else ""))


if __name__ == "__main__":
    main()
