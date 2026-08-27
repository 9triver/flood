"""End-to-end MCP check: a real client over stdio against the dos server.

The client plays an agent (as OAG or a TS pi agent would): opens a
scoped session, reads the world, acts (privileged → approval), approves,
and follows the transaction to `committed` — crossing process boundaries
over the MCP protocol the whole way.

Run: uv run python scripts/check_dos_mcp.py
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, ".")

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

STATION = "808J1510"
BASE = f"/hydro/shanhu/stations/{STATION}"
INTERVAL_PATH = f"{BASE}/sampling_interval_seconds"
STATUS_PATH = "/hydro/shanhu/views/level_status"


def banner(text: str) -> None:
    print(f"\n== {text} ==")


class Client:
    def __init__(self, session: ClientSession):
        self.session = session

    async def call(self, tool: str, **args) -> dict:
        result = await self.session.call_tool(tool, args)
        if getattr(result, "is_error", False):
            raise RuntimeError(f"tool {tool} failed: {[getattr(c, 'text', c) for c in result.content]}")
        payload = getattr(result, "structured_content", None)
        if payload is None:
            text = getattr(result.content[0], "text", None)
            payload = json.loads(text) if text is not None else {}
        if isinstance(payload, dict) and "gateway_error" in payload:
            raise RuntimeError(f"tool {tool} failed: {payload['gateway_error']}")
        if isinstance(payload, dict) and set(payload) == {"result"}:
            return payload["result"]  # unwrap list-returning tools
        return payload


async def wait_for(predicate, label: str, timeout: float = 15.0) -> None:  # kept for callers
    deadline = asyncio.get_event_loop().time() + timeout
    while not predicate():
        if asyncio.get_event_loop().time() >= deadline:
            raise TimeoutError(f"timed out waiting for {label}")
        await asyncio.sleep(0.1)


async def run() -> int:
    server_params = StdioServerParameters(
        command=sys.executable,
        args=[str(Path(__file__).resolve().parent / "dos_mcp_server.py")],
    )
    async with stdio_client(server_params) as (read, write):
        async with ClientSession(read, write) as raw:
            await raw.initialize()
            client = Client(raw)

            banner("0. 工具发现：syscall 面即工具面")
            tools = await raw.list_tools()
            print(f"  tools: {[t.name for t in tools.tools]}")
            names = {t.name for t in tools.tools}
            assert {"open_session", "read_path", "act", "approve", "txn_status", "wait_for_change", "pending_approvals"} <= names

            banner("1. 会话：以 agent 身份开读域 /hydro/shanhu + act 能力")
            session = (await client.call(
                "open_session",
                principal="oag-agent",
                read_scopes=["/hydro/shanhu"],
                act_prefix=f"/hydro/shanhu/stations/{STATION}",
                act_actions=["set_sampling_interval"],
            ))["session_id"]
            print(f"  session={session}")

            banner("2. 感知：读世界（越警判定为派生视图）")
            status = await client.call("read_path", session=session, path=STATUS_PATH)
            print(f"  level_status={status['value']} (generation={status['generation']})")

            banner("3. 读域治理：越域读取被拒绝")
            try:
                await client.call("read_path", session=session, path="/proc/secret")
                raise AssertionError("out-of-scope read must fail")
            except RuntimeError as exc:
                print(f"  denied as expected: {exc}")
                assert "may not read" in str(exc)

            banner("4. 控制：act 申请加密采样（特权 → 挂起待审批）")
            result = await client.call(
                "act", session=session, path=INTERVAL_PATH,
                action="set_sampling_interval", args={"seconds": 60},
            )
            print(f"  act -> {result}")
            assert result["state"] == "awaiting_approval"

            banner("5. 审批：值班员经审批工作清单放行")
            pending = await client.call("pending_approvals")
            print(f"  pending: {pending}")
            assert pending[0]["txn_id"] == result["txn_id"]
            approved = await client.call(
                "approve", txn_id=result["txn_id"], approved_by="值班员-陈",
                decision=True, reason="MCP 冒烟加密采样",
            )
            print(f"  approve -> {approved}")
            assert approved["state"] == "dispatched"

            banner("6. 反馈：等遥测证据，事务提交")
            status = await client.call("txn_status", txn_id=result["txn_id"])
            while status["state"] not in ("committed", "failed", "unknown"):
                await asyncio.sleep(0.2)
                status = await client.call("txn_status", txn_id=result["txn_id"])
            print(f"  txn {status['txn_id']} -> {status['state']}")
            assert status["state"] == "committed"

            banner("7. watch 桥接：长轮询等 generation 前进")
            level_path = f"{BASE}/level_m"
            current = await client.call("read_path", session=session, path=level_path)
            changed = await client.call(
                "wait_for_change", session=session, paths=[level_path],
                since={level_path: current["generation"]}, timeout=15,
            )
            print(f"  changed: {changed['changed']}")
            assert changed["changed"], "watch must observe a later frame"

    print("\nOK: dos MCP gateway verified over stdio (agent session → scoped read → privileged act → approval → committed)")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(run()))
