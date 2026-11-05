#!/usr/bin/env python3
"""Qualify the deployed NAS/Mac route using only an explicitly owned simulator."""
from __future__ import annotations

import argparse
import asyncio
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import sys
import time
import uuid

import httpx
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

ROOT = Path(__file__).resolve().parents[1]
STATE = Path.home() / "Library/Application Support/TapMesh"


async def verify(device_id: str, report: dict) -> dict:
    config = json.loads((STATE / "connection.json").read_text())
    token = Path(config["server_token_file"]).read_text().strip()
    owner = "Bearer " + Path(config["approval_token_file"]).read_text().strip()
    session = "integration-" + uuid.uuid4().hex

    def checked(name, **metadata):
        report["checks"].append({"name": name, "status": "passed", **metadata})
        print(name + ": passed", flush=True)

    async with httpx.AsyncClient(base_url="http://127.0.0.1:48793", timeout=90) as client:
        assert (await client.get("/health")).json()["status"] == "ok"
        assert (await client.get("/api/tools")).status_code == 401
        assert (await client.post("/mcp", json={})).status_code == 401
        client.headers["Authorization"] = "Bearer " + token
        assert (await client.get("/internal/ocr/health")).status_code == 401
        assert (await client.get("/api/approvals")).status_code == 403
        assert (await client.post("/api/takeover", json={"session_id": session, "device_id": device_id})).status_code == 403
        checked("public and private authorization separation")
        tools = (await client.get("/api/tools")).json()["tools"]
        assert len(tools) == len({t["name"] for t in tools}) == 50
        checked("50 deployed tool schemas")

        async def call(tool, arguments=None, *, selected=session, authorize=False):
            payload = {"session_id": selected, "tool": tool, "arguments": arguments or {}}
            response = await client.post("/api/call", json=payload)
            response.raise_for_status()
            value = response.json()
            if authorize and value.get("error", {}).get("code") == "approval_required":
                approval = value["error"]["details"]["approval_id"]
                decision = await client.post("/api/approvals/" + approval, headers={"X-DeviceBridge-Local-Authorization": owner}, json={"decision": "allow"})
                decision.raise_for_status()
                response = await client.post("/api/call", json=payload)
                response.raise_for_status()
                value = response.json()
            return value

        async def ok(tool, arguments=None, **kwargs):
            result = await call(tool, arguments, **kwargs)
            assert result["status"] == "success", (tool, result.get("error"))
            return result["data"]

        devices = await ok("list_devices", {"include_all_simulators": True})
        device = next(d for d in devices["devices"] if d["device_id"] == device_id)
        assert device["backend"] == "simulator" and "Qualification" in device["name"] and device["ready"]
        await ok("select_device", {"device_id": device_id})
        await ok("get_device_info")
        isolated = await call("get_device_info", selected=session + "-other")
        assert isolated["error"]["code"] == "device_not_selected"
        checked("explicit device selection and session isolation")
        await ok("launch_app", {"bundle_id": "io.tapmesh.fixture"}, authorize=True)
        await asyncio.sleep(1)
        capture = await ok("take_screenshot", {"format": "png", "max_width": 640, "require_fresh": True})
        assert capture["image_size"]["width"] == 640 and capture["observation_id"]
        checked("live simulator screenshot through NAS", image_width=640)
        saved = await ok("save_screenshot", authorize=True)
        artifact = saved["artifact"]
        downloaded = await client.get("/api/artifacts/" + artifact["artifact_id"])
        downloaded.raise_for_status()
        assert len(downloaded.content) == artifact["size_bytes"]
        assert hashlib.sha256(downloaded.content).hexdigest() == artifact["sha256"]
        checked("NAS artifact transfer and download checksum", size_bytes=len(downloaded.content))
        elements = await ok("list_elements", {"source": "ocr", "max_elements": 200})
        assert elements["elements"] and all(e["source"] == "ocr" for e in elements["elements"])
        assert any("TapMesh" in e.get("label", "") for e in elements["elements"])
        checked("live screenshot OCR processed on NAS", element_count=len(elements["elements"]))

        # This changes only the disposable simulator, then restores its appearance.
        await ok("set_appearance", {"appearance": "dark"}, authorize=True)
        stale = await call("tap", {"target": {"x": 10, "y": 10, "coordinate_space": "device_points", "observation_id": capture["observation_id"]}}, authorize=True)
        assert stale["error"]["code"] == "stale_observation" and stale["error"]["side_effect_state"] == "not_sent"
        await ok("set_appearance", {"appearance": "light"}, authorize=True)
        checked("stale observation rejected before input")

        recording = await ok("start_recording", {"max_duration_seconds": 90}, authorize=True)
        try:
            # Hold the device mutation lease with a bounded, input-free batch.
            batch = {"commands": [{"tool": "wait", "arguments": {"duration_ms": 25000}}, {"tool": "set_appearance", "arguments": {"appearance": "light"}}], "timeout_ms": 60000}
            pending = await call("batch_commands", batch)
            assert pending["error"]["code"] == "approval_required"
            granted = await client.post("/api/approvals/" + pending["error"]["details"]["approval_id"], headers={"X-DeviceBridge-Local-Authorization": owner}, json={"decision": "allow"})
            granted.raise_for_status()
            wait_task = asyncio.create_task(call("batch_commands", batch))
            await asyncio.sleep(8)
            started = time.monotonic()
            stopped = await ok("stop_recording", {"recording_id": recording["recording_id"]}, authorize=True)
            assert stopped["segments"] and stopped["segments"][0]["duration_seconds"] > 0
            elapsed = time.monotonic() - started
            assert elapsed < 15 and not wait_task.done()
            checked("recording stop while another request is active", elapsed_seconds=round(elapsed, 2))
            receipt = await client.post("/api/takeover", headers={"X-DeviceBridge-Local-Authorization": owner}, json={"session_id": session, "device_id": device_id})
            receipt.raise_for_status()
            assert receipt.json()["status"] == "success"
            assert receipt.json()["data"]["cancelled_requests"] >= 1
            waited = await wait_task
            assert waited["status"] == "partial" and waited["data"]["results"][0]["error"]["code"] == "cancelled"
            checked("owner takeover cancels active leased device work")
        finally:
            # Stop is idempotently queried only for this owned recording.
            await call("stop_recording", {"recording_id": recording["recording_id"]}, authorize=True)

        # Initialize a real Streamable HTTP MCP session and exercise its tools.
        headers = {"Accept": "application/json, text/event-stream"}
        init = await client.post("/mcp", headers=headers, json={"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2025-11-25", "capabilities": {}, "clientInfo": {"name": "TapMesh qualification", "version": "0.1"}}})
        init.raise_for_status()
        headers.update({"Mcp-Session-Id": init.headers["mcp-session-id"], "MCP-Protocol-Version": "2025-11-25"})
        assert (await client.post("/mcp", headers=headers, json={"jsonrpc": "2.0", "method": "notifications/initialized"})).status_code == 202
        listing = await client.post("/mcp", headers=headers, json={"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}})
        assert len(listing.json()["result"]["tools"]) == 50
        called = await client.post("/mcp", headers=headers, json={"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": {"name": "list_devices", "arguments": {}}})
        assert called.json()["result"]["structuredContent"]["status"] == "success"
        assert (await client.delete("/mcp", headers=headers)).status_code == 200
        checked("initialized Streamable HTTP MCP session and tool call")

    env = {"TAPMESH_SERVER_URL": "http://127.0.0.1:48793", "TAPMESH_SERVER_TOKEN_FILE": config["server_token_file"]}
    parameters = StdioServerParameters(command=config["python"], args=["-m", "server.stdio"], env=env, cwd=str(STATE / "runtime-src"))
    async with stdio_client(parameters) as (read, write):
        async with ClientSession(read, write) as mcp:
            initialized = await mcp.initialize()
            assert initialized.server_info.name == "TapMesh"
            assert len((await mcp.list_tools()).tools) == 50
            result = await mcp.call_tool("list_devices", {})
            assert result.structured_content["status"] == "success"
    checked("official MCP SDK stdio bridge over deployed NAS")
    report['status'] = 'passed'
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--device-id", required=True, help="Exact device_id of a booted project-owned Qualification simulator")
    parser.add_argument("--report", type=Path, default=ROOT / "docs/INTEGRATION_QUALIFICATION.json")
    args = parser.parse_args()
    report = {"checked_at": datetime.now(timezone.utc).isoformat(), "checks": []}
    try:
        asyncio.run(verify(args.device_id, report))
    except Exception as error:
        report.update(status='failed', failure=str(error))
        raise
    finally:
        args.report.write_text(json.dumps(report, indent=2) + "\n")
        print("Report:", args.report)


if __name__ == "__main__":
    main()
