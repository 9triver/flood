"""End-to-end MCP check: a real client over stdio against the full flood
business daemon (scripts/dos_mcp_server.py).

The client plays a 值班 agent: opens a scoped session, reads the world,
sees the level-monitor process's approval request and resolves it, watches
a CNN forecast land (produced by the stateless trigger process), follows
the automatic impact sweep, and queries the mirror — all over the MCP
protocol, across process boundaries.

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
LEVEL_PATH = f"{BASE}/level_m"
STATUS_PATH = "/hydro/shanhu/views/level_status"
FORECAST_LATEST = "/hydro/shanhu/forecasts/latest"
IMPACT_LATEST = "/hydro/shanhu/impacts/latest"


def banner(text: str) -> None:
    print(f"\n== {text} ==")


class Client:
    def __init__(self, session: ClientSession):
        self.session = session

    async def call(self, tool: str, **args):
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
            return payload["result"]
        return payload


async def run() -> int:
    server_params = StdioServerParameters(
        command=sys.executable,
        args=[str(Path(__file__).resolve().parent / "dos_mcp_server.py")],
    )
    async with stdio_client(server_params) as (read, write):
        async with ClientSession(read, write) as raw:
            await raw.initialize()
            client = Client(raw)

            banner("0. 工具发现")
            tools = await raw.list_tools()
            names = {t.name for t in tools.tools}
            print(f"  tools: {sorted(names)}")
            assert {"open_session", "read_path", "history", "act", "approve", "txn_status", "wait_for_change", "pending_approvals", "list_paths"} <= names

            banner("1. 会话与读域")
            session = (await client.call(
                "open_session",
                principal="duty-agent",
                read_scopes=["/hydro/shanhu"],
                act_prefix="/hydro/shanhu/stations",
                act_actions=["set_sampling_interval"],
            ))["session_id"]
            visible = await client.call("list_paths", session=session, under="/")
            print(f"  可见路径 {len(visible)} 条，如 {visible[:3]}")
            try:
                await client.call("read_path", session=session, path="/etc/hostname")
                raise AssertionError("out-of-scope read must fail")
            except RuntimeError as exc:
                print(f"  越域读取被拒: {exc}")

            banner("2. 观察世界：水位越警，监视进程已提出加密采样申请")
            status = await client.call("read_path", session=session, path=STATUS_PATH)
            print(f"  level_status={status['value']} (generation={status['generation']})")
            pending = await client.call("pending_approvals")
            assert pending, "level-monitor 的申请应已在审批清单里"
            txn_id = pending[0]["txn_id"]
            print(f"  审批清单: {pending[0]['action']} {pending[0]['args']} → {txn_id}")

            banner("3. 值班 agent 代人放行（进程的待办，agent 处理）")
            approved = await client.call("approve", txn_id=txn_id, approved_by="值班员-陈", decision=True, reason="汛情加密")
            print(f"  approve → {approved['state']}")
            for _ in range(100):
                state = (await client.call("txn_status", txn_id=txn_id))["state"]
                if state == "committed":
                    break
                await asyncio.sleep(0.2)
            print(f"  txn {txn_id} → {state}（下一帧遥测证实）")
            assert state == "committed"

            banner("4. 补课：agent 不在场时，触发进程已把预测做完")
            latest = await client.call("read_path", session=session, path=FORECAST_LATEST)
            assert latest is not None and latest["value"].get("id"), "启动窗口越阈，预测应已落地"
            meta = await client.call("read_path", session=session, path=f"/hydro/shanhu/forecasts/{latest['value']['id']}")
            print(f"  forecast {latest['value']['id']} committed（journal 序号即身份）")
            print(f"  输入: {meta['value']['input']}")
            print(f"  统计: {meta['value']['stats']}")

            banner("5. 自动研判：影响评估进程已跟进标准目标集")
            for _ in range(100):
                impact = await client.call("read_path", session=session, path=IMPACT_LATEST)
                if impact is not None and impact["value"].get("forecast_id") == latest["value"]["id"]:
                    break
                await asyncio.sleep(0.2)
            detail = await client.call(
                "read_path", session=session,
                path=f"/hydro/shanhu/impacts/{impact['value']['id']}",
            )
            print(f"  impact {impact['value']['id']}: {detail['value']['summary']}")

            banner("5b. 常驻值班 agent 已在 T0 立下态势研判（进程是条件反射，它是第一个理解的人）")
            for _ in range(100):
                situation = await client.call(
                    "read_path", session=session,
                    path="/hydro/shanhu/assessments/by-kind/situation/latest",
                )
                if situation is not None and situation["value"].get("refs", {}).get("forecast_id") == latest["value"]["id"]:
                    break
                await asyncio.sleep(0.2)
            filed = await client.call(
                "read_path", session=session,
                path=f"/hydro/shanhu/assessments/{situation['value']['id']}",
            )
            print(f"  assessment {filed['value']['id']} by {filed['value']['author']}: {filed['value']['title']}")
            print(f"  {filed['value']['content']['summary']}")

            banner("6. 镜像与 watch：查历史、等世界变化")
            rows = await client.call(
                "history", session=session,
                path="/hydro/shanhu/stations/interval1/flow_m3s", limit=3,
            )
            for row in rows:
                print(f"  {datetime_str(row['observed_at'])}  flow={row['value']} m3/s")
            level = await client.call("read_path", session=session, path=LEVEL_PATH)
            changed = await client.call(
                "wait_for_change", session=session, paths=[LEVEL_PATH],
                since={LEVEL_PATH: level["generation"]}, timeout=15,
            )
            assert changed["changed"], "水位每 2 秒更新，watch 应很快返回"
            print(f"  watch 观测到水位 generation 前进: {changed['changed']}")

    print("\nOK: dos flood daemon verified over MCP (观察→审批→预测→研判→镜像)")
    return 0


def datetime_str(ts: float) -> str:
    from datetime import datetime, timezone

    return datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%m-%d %H:%M")


if __name__ == "__main__":
    raise SystemExit(asyncio.run(run()))
