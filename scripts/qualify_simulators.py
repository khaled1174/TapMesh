#!/usr/bin/env python3
"""Qualify an unchanged standalone bundle against exact, receipt-owned simulators.

Simulator creation/build/install are separate setup actions. This runner never
boots, resets, erases, or shuts down any simulator and never selects a physical
device. Only io.tapmesh.fixture may receive its approved input or lifecycle work.
"""
from __future__ import annotations

import argparse
import asyncio
import base64
from contextlib import AsyncExitStack
from datetime import datetime, timezone
import hashlib
import io
import json
import os
from pathlib import Path
import plistlib
import re
import shutil
import stat
import subprocess
import tempfile
import time
import uuid

import httpx
from PIL import Image, ImageStat
from qualify_local_runtime import receive, services_stopped

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = "io.tapmesh.fixture"
AUTHORIZED = {"launch_app", "terminate_app", "tap", "type_text", "hide_keyboard",
              "start_recording", "stop_recording", "save_screenshot"}


class QualificationFailure(Exception):
    pass


def require(condition, code):
    if not condition:
        raise QualificationFailure(code)


def owned_devices(receipt, only_iphone=False):
    info = receipt.lstat()
    require(stat.S_ISREG(info.st_mode) and info.st_uid == os.getuid()
            and stat.S_IMODE(info.st_mode) == 0o600 and info.st_size < 16384, "unsafe_ownership_receipt")
    document = json.loads(receipt.read_text())
    require(document.get("schema") == 1 and document.get("product") == "TapMesh", "invalid_ownership_receipt")
    inventory = json.loads(subprocess.check_output(["/usr/bin/xcrun", "simctl", "list", "devices", "available", "-j"], text=True))
    available = {item["udid"]: item for devices in inventory["devices"].values() for item in devices}
    devices = document["devices"]
    require(1 <= len(devices) <= 2 and len({d["udid"] for d in devices}) == len(devices), "invalid_device_count")
    result = []
    for record in devices:
        if only_iphone and record["kind"] != "iphone":
            continue
        require(re.fullmatch(r"[A-Fa-f0-9-]{36}", record["udid"]) is not None, "invalid_owned_identifier")
        device = available.get(record["udid"])
        require(device is not None and device["name"] == record["name"] and device["state"] == "Booted", "owned_simulator_not_ready")
        require(record.get("ownership") and record["kind"] in {"iphone", "ipad"}, "ownership_not_recorded")
        result.append(record)
    require(bool(result), "no_owned_simulators")
    return result


class ProductClient:
    def __init__(self, profile, devices):
        configuration = json.loads((profile / "connection.json").read_text())
        self.http = httpx.Client(base_url=configuration["api_url"], timeout=55, trust_env=False,
                                 headers={"Authorization": "Bearer " + (profile / "server.token").read_text().strip()})
        self.owner = {"X-DeviceBridge-Local-Authorization": "Bearer " + (profile / "approvals.token").read_text().strip()}
        self.devices = {"sim:" + item["udid"] for item in devices}

    def close(self):
        self.http.close()

    def call(self, session, device, tool, arguments=None, *, approve=False):
        require(device in self.devices or device is None and tool == "list_devices", "unowned_target_rejected")
        arguments = dict(arguments or {})
        if device is not None:
            arguments["device_id"] = device
        if tool in {"launch_app", "terminate_app"}:
            require(arguments.get("bundle_id") == FIXTURE, "nonfixture_lifecycle_rejected")
        if approve:
            require(tool in AUTHORIZED and device in self.devices, "unapproved_operation_rejected")
        payload = {"session_id": session, "tool": tool, "arguments": arguments}
        response = self.http.post("/api/call", json=payload)
        response.raise_for_status()
        result = response.json()
        error = result.get("error", {})
        if approve and error.get("code") == "approval_required":
            require(error.get("side_effect_state") == "not_sent", "approval_after_possible_delivery")
            approval_id = error["details"]["approval_id"]
            pending = self.http.get("/api/approvals", headers=self.owner)
            pending.raise_for_status()
            record = next((r for r in pending.json()["approvals"] if r["id"] == approval_id), None)
            require(record is not None and record["session_id"] == session and record["device_id"] == device
                    and record["tool"] == tool and record["policy"] in {"C", "S"}, "approval_binding_mismatch")
            canonical = json.dumps([session, device, record["connection_epoch"], tool, arguments, record["policy"]],
                                   sort_keys=True, separators=(",", ":"), allow_nan=False)
            require(hashlib.sha256(canonical.encode()).hexdigest() == record["digest"], "approval_digest_mismatch")
            decision = self.http.post("/api/approvals/" + approval_id, headers=self.owner, json={"decision": "allow"})
            decision.raise_for_status()
            # The first request only asked for canonical authorization. A sent
            # device action is never retried by this runner.
            response = self.http.post("/api/call", json=payload)
            response.raise_for_status()
            result = response.json()
        return result

    def ok(self, session, device, tool, arguments=None, *, approve=False):
        result = self.call(session, device, tool, arguments, approve=approve)
        require(result.get("status") == "success", tool + ":" + result.get("error", {}).get("code", "not_success"))
        if device is not None and tool != "select_device":
            require(result.get("device_id") == device, "device_binding_mismatch")
        return result["data"]

    def ocr(self, session, device):
        value = self.ok(session, device, "list_elements", {"source": "ocr", "max_elements": 200})
        require(value["source"] == "ocr" and value["elements"]
                and all(row["source"] == "ocr" for row in value["elements"]), "ocr_source_or_content_missing")
        require("TapMesh" in " ".join(row.get("label", "") for row in value["elements"]), "fixture_not_observed")
        return value

    @staticmethod
    def target(snapshot, label):
        matches = [e for e in snapshot["elements"] if e.get("label", "").strip().casefold() == label.casefold()]
        require(len(matches) == 1, "unique_fixture_target_not_observed")
        require(matches[0]["bounds"]["width"] > 0 and matches[0]["bounds"]["height"] > 0, "target_geometry_unusable")
        return {"ref": matches[0]["ref"], "snapshot_id": snapshot["snapshot_id"]}

    def download(self, artifact, destination):
        digest = hashlib.sha256()
        size = 0
        with self.http.stream("GET", "/api/artifacts/" + artifact["artifact_id"]) as response:
            response.raise_for_status()
            with destination.open("xb") as output:
                for block in response.iter_bytes(1024 * 1024):
                    size += len(block)
                    require(size <= artifact["size_bytes"] <= 2 * 1024 ** 3, "artifact_size_exceeded")
                    digest.update(block)
                    output.write(block)
        require(size == artifact["size_bytes"] and digest.hexdigest() == artifact["sha256"], "artifact_checksum_mismatch")
        return size


async def check_mcp(bundle, profile, workspace, devices, env):
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client
    async with AsyncExitStack() as stack:
        sessions = []
        for device in devices:
            parameters = StdioServerParameters(command=str(bundle / "Contents/MacOS/tapmesh-mcp"),
                args=["--state-dir", str(profile)], env=env, cwd=str(workspace))
            read, write = await stack.enter_async_context(stdio_client(parameters))
            session = await stack.enter_async_context(ClientSession(read, write))
            require((await session.initialize()).server_info.name == "TapMesh", "mcp_server_identity")
            require(len((await session.list_tools()).tools) == 50, "mcp_catalog_count")
            selected = await session.call_tool("select_device", {"device_id": "sim:" + device["udid"]})
            require(selected.structured_content["status"] == "success", "mcp_selection_failed")
            sessions.append((session, "sim:" + device["udid"]))
        for session, expected in sessions:
            info = await session.call_tool("get_device_info", {})
            require(info.structured_content["status"] == "success" and info.structured_content["device_id"] == expected,
                    "mcp_independent_session_selection_failed")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bundle", type=Path, required=True)
    parser.add_argument("--ownership-receipt", type=Path, default=Path.home() / "Library/Caches/TapMesh/simulator-qualification/ownership.json")
    parser.add_argument("--only-iphone", action="store_true")
    parser.add_argument("--report", type=Path, default=ROOT / "docs/SIMULATOR_LOCAL_QUALIFICATION.json")
    args = parser.parse_args()
    devices = owned_devices(args.ownership_receipt, args.only_iphone)
    subprocess.run(["/usr/bin/codesign", "--verify", "--deep", "--strict", str(args.bundle)], check=True, capture_output=True)
    with (args.bundle / "Contents/Info.plist").open("rb") as source:
        bundle_info = plistlib.load(source)
    require(bundle_info.get("CFBundleIdentifier") == "io.tapmesh.mac", "unexpected_app_bundle")
    report = {"product": "TapMesh", "checked_at": datetime.now(timezone.utc).isoformat(), "status": "running",
              "app_version": bundle_info["CFBundleShortVersionString"], "app_build": bundle_info["CFBundleVersion"],
              "native_executable_sha256": hashlib.sha256((args.bundle / "Contents/MacOS/TapMesh").read_bytes()).hexdigest(),
              "scope": "receipt-owned iPhone/iPad simulators; io.tapmesh.fixture only; physical tests deferred",
              "devices": [item["kind"] for item in devices], "checks": []}
    cache = Path.home() / "Library/Caches/TapMesh/simulator-qualification"
    workspace = Path(tempfile.mkdtemp(prefix="integration-", dir=cache))
    bundle = workspace / "Relocated TapMesh.app"
    shutil.copytree(args.bundle, bundle, symlinks=True)
    resources, profile = bundle / "Contents/Resources", workspace / "Local"
    env = {key: os.environ[key] for key in ("HOME", "TMPDIR", "LANG") if key in os.environ}
    env["PATH"] = "/usr/bin:/bin:/usr/sbin:/sbin"
    process = subprocess.Popen([str(resources / "runtime/bin/python3"), "-I", "-B", "-m", "tapmesh_runtime", "supervise",
        "--state-dir", str(profile), "--resources", str(resources), "--parent-pid", str(os.getpid())],
        cwd=workspace, env=env, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    configuration = None
    client = None

    def check(name, operation, device=None):
        record = {"name": name, "status": "running"}
        if device:
            record["device"] = device
        report["checks"].append(record)
        try:
            metadata = operation()
            record["status"] = "passed"
            if isinstance(metadata, dict):
                public_keys = {"image_size", "processed_on", "elements", "counter_before", "counter_after",
                               "character_count", "input_method", "size_bytes", "sha256_verified", "storage", "duration_seconds"}
                record.update({key: value for key, value in metadata.items() if key in public_keys})
            return True
        except Exception as error:
            record["status"] = "failed"
            record["error_code"] = str(error)[:160] if isinstance(error, QualificationFailure) else type(error).__name__
            return False
        finally:
            args.report.write_text(json.dumps(report, indent=2) + "\n")
            print((device + ": " if device else "") + name + ": " + record["status"], flush=True)

    try:
        ready = receive(process)
        require(ready["owner"] is True, "fresh_profile_not_owned")
        configuration = json.loads((profile / "connection.json").read_text())
        client = ProductClient(profile, devices)
        def vision_processor():
            require(client.http.get("/internal/ocr/health").status_code == 401, "processor_accepted_public_bearer")
            headers = {"Authorization": "Bearer " + (profile / "agent.token").read_text().strip()}
            response = client.http.get("/internal/ocr/health", headers=headers)
            response.raise_for_status()
            health = response.json()
            require(health.get("status") == "available" and health.get("engine") == "vision", "bundled_vision_unavailable")
        check("bundled Vision processor identity and credential separation", vision_processor)
        sessions = ["sim-qualification-" + uuid.uuid4().hex for _ in devices]
        discovered = client.ok(sessions[0], None, "list_devices", {"include_all_simulators": True})
        available = {d["device_id"]: d for d in discovered["devices"]}
        for session, device in zip(sessions, devices):
            did = "sim:" + device["udid"]
            require(available[did]["backend"] == "simulator" and available[did]["ready"], "owned_device_not_ready")
            client.ok(session, did, "select_device")

        def selections():
            for session, device in zip(sessions, devices):
                client.ok(session, "sim:" + device["udid"], "get_device_info")
            unselected = client.http.post("/api/call", json={"session_id": "unselected-" + uuid.uuid4().hex,
                                                           "tool": "get_device_info", "arguments": {}}).json()
            require(unselected.get("error", {}).get("code") == "device_not_selected", "unselected_session_borrowed_device")
        check("independent HTTP sessions and explicit selection", selections)
        check("independent initialized MCP stdio sessions and 50 schemas",
              lambda: asyncio.run(check_mcp(bundle, profile, workspace, devices, env)))

        for session, device in zip(sessions, devices):
            did, kind = "sim:" + device["udid"], device["kind"]
            if not check("launch owned fixture", lambda: client.ok(session, did, "launch_app", {"bundle_id": FIXTURE}, approve=True), kind):
                continue
            time.sleep(.5)

            def screenshot():
                capture = client.ok(session, did, "take_screenshot", {"format": "png", "max_width": 1280, "require_fresh": True})
                with Image.open(io.BytesIO(base64.b64decode(capture["image"]["data"], validate=True))) as image:
                    image.load()
                    require(max(ImageStat.Stat(image.convert("RGB")).stddev) > 5, "blank_capture")
                    return {"image_size": [image.width, image.height], "processed_on": capture.get("processed_on")}
            check("real nonblank server-processed capture", screenshot, kind)
            if not check("Vision OCR observes owned fixture", lambda: {"elements": len(client.ocr(session, did)["elements"])}, kind):
                continue

            def counter():
                snapshot = client.ocr(session, did)
                before = re.search(r"Tap\s*count\s*:\s*(\d+)", " ".join(e["label"] for e in snapshot["elements"]), re.I)
                require(before is not None, "fixture_counter_not_observed")
                expected = int(before.group(1)) + 1
                client.ok(session, did, "tap", {"target": client.target(snapshot, "Increment")}, approve=True)
                deadline = time.monotonic() + 6
                while time.monotonic() < deadline:
                    time.sleep(.3)
                    observed = client.ocr(session, did)
                    match = re.search(r"Tap\s*count\s*:\s*(\d+)", " ".join(e["label"] for e in observed["elements"]), re.I)
                    if match and int(match.group(1)) == expected:
                        return {"counter_before": expected - 1, "counter_after": expected}
                raise QualificationFailure("tap_delivery_not_verified_by_counter")
            check("approved tap changes observed fixture counter", counter, kind)

            def text_entry():
                snapshot = client.ocr(session, did)
                text = "TMQualification" + ("1" if kind == "iphone" else "2")
                client.ok(session, did, "type_text", {"target": client.target(snapshot, "Enter name"), "text": text,
                                                    "input_method": "keyboard", "keyboard_layout": "us"}, approve=True)
                observed = client.ocr(session, did)
                joined = " ".join(e["label"] for e in observed["elements"])
                require(re.search(r"Entered\s*:\s*" + re.escape(text), joined) is not None, "bounded_fixture_text_not_observed")
                client.ok(session, did, "hide_keyboard", approve=True)
                return {"character_count": len(text), "input_method": "explicit keyboard"}
            check("bounded ASCII fixture text observed through OCR", text_entry, kind)

            def screenshot_transfer():
                saved = client.ok(session, did, "save_screenshot", {"format": "png"}, approve=True)["artifact"]
                size = client.download(saved, workspace / (kind + "-fixture.png"))
                return {"size_bytes": size, "sha256_verified": True, "storage": saved.get("storage")}
            check("finalized screenshot transfer and download checksum", screenshot_transfer, kind)

            def recording():
                started = client.ok(session, did, "start_recording", {"max_duration_seconds": 30}, approve=True)
                try:
                    time.sleep(1.5)
                    stopped = client.ok(session, did, "stop_recording", {"recording_id": started["recording_id"]}, approve=True)
                    require(stopped["state"] == "complete" and len(stopped["segments"]) == 1, "recording_not_complete")
                    segment = stopped["segments"][0]
                    require(segment["duration_seconds"] > 0, "recording_duration_missing")
                    downloaded = workspace / (kind + "-fixture.mp4")
                    size = client.download(segment, downloaded)
                    result = subprocess.check_output([str(resources / "TapMeshTools"), "probe", str(downloaded)], timeout=20)
                    media = json.loads(result)
                    require(media["duration_seconds"] > 0 and media["video_tracks"] > 0, "downloaded_recording_not_finalized")
                    return {"duration_seconds": media["duration_seconds"], "size_bytes": size, "sha256_verified": True}
                finally:
                    client.call(session, did, "stop_recording", {"recording_id": started["recording_id"]})
            check("recording finalized by bundled media helper and transferred", recording, kind)

            def accessibility():
                result = client.ok(session, did, "list_elements", {"source": "accessibility", "max_elements": 200, "timeout_ms": 5000})
                matches = [e for e in result["elements"] if e.get("identifier") == "increment-button"]
                require(result["source"] == "accessibility" and len(matches) == 1
                        and matches[0].get("bounds", {}).get("width", 0) > 0, "accessibility_fixture_identity_or_geometry_missing")
                return {"elements": len(result["elements"])}
            check("explicit accessibility observation", accessibility, kind)
            if check("owned fixture termination and relaunch",
                     lambda: (client.ok(session, did, "terminate_app", {"bundle_id": FIXTURE}, approve=True),
                              client.ok(session, did, "launch_app", {"bundle_id": FIXTURE}, approve=True)), kind):
                time.sleep(.5)
                check("explicit accessibility after relaunch", accessibility, kind)
                check("Vision OCR after relaunch", lambda: {"elements": len(client.ocr(session, did)["elements"])}, kind)
    except Exception as error:
        report["checks"].append({"name": "qualification setup or transport", "status": "failed",
                                 "error_code": str(error)[:160] if isinstance(error, QualificationFailure) else type(error).__name__})
    finally:
        if client:
            client.close()
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=55)
            except subprocess.TimeoutExpired:
                report["checks"].append({"name": "owned runtime shutdown", "status": "failed", "error_code": "shutdown_deadline"})
        if configuration:
            check("owned service listeners stopped", lambda: require(services_stopped(configuration), "owned_listener_remained"))
        check("relocated bundle signature preserved after actual execution",
              lambda: subprocess.run(["/usr/bin/codesign", "--verify", "--deep", "--strict", str(bundle)], check=True, capture_output=True))
        report["status"] = "failed" if any(c["status"] == "failed" for c in report["checks"]) else "passed"
        args.report.write_text(json.dumps(report, indent=2) + "\n")
    print("Simulator qualification: " + report["status"], flush=True)
    raise SystemExit(0 if report["status"] == "passed" else 1)


if __name__ == "__main__":
    main()
