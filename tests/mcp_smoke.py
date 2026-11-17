"""CI-grade MCP smoke: spawn the stdio server, MCP handshake, tools/list,
tools/call round-trip through a temp bus root. Exit 0 = PASS.

Run: python -X utf8 tests/mcp_smoke.py
"""
import json
import os
import subprocess
import sys
import tempfile
import threading
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
SERVER = os.path.join(ROOT, "dshbus_mcp.py")

EXPECTED_TOOLS = {
    "bus_send", "bus_recv", "bus_ack", "bus_wait", "bus_status", "bus_recover"
}


class McpStdioClient:
    def __init__(self, proc: subprocess.Popen):
        self.proc = proc
        self._id = 0
        self._lock = threading.Lock()

    def notify(self, method: str, params: dict | None = None) -> None:
        """Fire-and-forget notification: no id, no response expected."""
        with self._lock:
            msg = {"jsonrpc": "2.0", "method": method, "params": params or {}}
            self.proc.stdin.write(json.dumps(msg) + "\n")
            self.proc.stdin.flush()

    def request(self, method: str, params: dict | None = None, timeout: float = 30.0):
        with self._lock:
            self._id += 1
            rid = self._id
            msg = {"jsonrpc": "2.0", "id": rid, "method": method,
                   "params": params or {}}
            data = json.dumps(msg) + "\n"
            self.proc.stdin.write(data)
            self.proc.stdin.flush()
        deadline = time.time() + timeout
        while time.time() < deadline:
            line = self.proc.stdout.readline()
            if not line:
                raise RuntimeError("server closed stdout")
            resp = json.loads(line)
            if resp.get("id") == rid:
                if "error" in resp:
                    raise RuntimeError(f"rpc error: {resp['error']}")
                return resp["result"]
        raise TimeoutError(f"no response for {method} in {timeout}s")


def _payload(r: dict):
    """FastMCP may answer via structuredContent or text JSON — accept both."""
    sc = r.get("structuredContent")
    if sc is not None:
        return sc
    return json.loads(r["content"][0]["text"])  # noqa: direct text fallback


def main() -> None:
    bus_root = tempfile.mkdtemp(prefix="dshbus_mcp_smoke_")
    env = dict(os.environ)
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONPATH"] = ROOT
    env["DSH_BUS_ROOT"] = bus_root

    proc = subprocess.Popen(
        (["node", os.path.join(ROOT, "dshbus-launcher.js")]
         if os.environ.get("DSHBUS_SMOKE_LAUNCHER")
         else [sys.executable, "-X", "utf8", SERVER]),
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        text=True, encoding="utf-8", env=env,
    )
    try:
        cli = McpStdioClient(proc)
        # 1. initialize handshake
        init = cli.request("initialize", {
            "protocolVersion": "2024-11-05",
            "capabilities": {},
            "clientInfo": {"name": "smoke", "version": "0"},
        })
        assert "dshbus" in json.dumps(init), "server name missing in initialize result"
        cli.notify("notifications/initialized")
        # 2. tools/list
        listed = cli.request("tools/list")
        names = {t["name"] for t in listed["tools"]}
        missing = EXPECTED_TOOLS - names
        assert not missing, f"missing tools: {missing}, got {names}"
        print(f"[tools/list] {len(names)} tools OK: {sorted(names)}")

        # 3. round-trip: A sends -> B recv (peek) -> status
        r = cli.request("tools/call", {"name": "bus_send", "arguments": {
            "to": "smoke-b", "type": "task",
            "payload": {"hello": "世界🚀"}, "corr": "smoke-1", "me": "smoke-a"}})
        sent = _payload(r)
        assert sent["id"], f"send failed: {r}"
        print(f"[bus_send] id={sent['id']}")

        r = cli.request("tools/call", {"name": "bus_recv", "arguments": {
            "me": "smoke-b", "limit": 5}})
        got_recv = _payload(r)
        msgs = got_recv["messages"]
        assert len(msgs) == 1 and msgs[0]["payload"]["hello"] == "世界🚀", msgs
        assert msgs[0]["corr"] == "smoke-1"
        mid = msgs[0]["id"]
        print(f"[bus_recv] got 1 msg, unicode intact, mid={mid}")

        r = cli.request("tools/call", {"name": "bus_ack", "arguments": {
            "mid": mid, "me": "smoke-b",
            "result": {"corr": "smoke-1", "answer": "ok"}}})
        assert _payload(r)["ok"] is True
        print("[bus_ack] ok=True")

        r = cli.request("tools/call", {"name": "bus_wait", "arguments": {
            "corrs": ["smoke-1"], "deadline_s": 10, "me": "smoke-a"}})
        got = _payload(r)
        assert got.get("smoke-1"), f"wait got: {got}"
        print(f"[bus_wait] receipt={got['smoke-1']}")

        r = cli.request("tools/call", {"name": "bus_status", "arguments": {}})
        st = _payload(r)
        assert st["smoke-b"]["acked"] == 1, st
        print(f"[bus_status] {st}")

        print("MCP SMOKE PASS ✅")
    finally:
        proc.kill()
        err = proc.stderr.read()
        if err.strip():
            print("[stderr]", err[:2000])


if __name__ == "__main__":
    main()
