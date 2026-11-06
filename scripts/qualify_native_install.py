#!/usr/bin/env python3
"""Assess and launch an exact signed release on a fresh, isolated local profile.

Never selects or operates an Apple device. Reports native-entrypoint launch;
Finder's first-open confirmation and native update installation remain separate.
"""
import argparse
import asyncio
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import platform
import plistlib
import subprocess
import time

import httpx
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from qualify_local_runtime import services_stopped


async def check_mcp(app, profile, workspace, env):
    parameters=StdioServerParameters(command=str(app/'Contents/MacOS/tapmesh-mcp'),
        args=['--state-dir',str(profile)],env=env,cwd=str(workspace))
    async with stdio_client(parameters) as (read,write):
        async with ClientSession(read,write) as session:
            assert (await session.initialize()).server_info.name=='TapMesh'
            assert len((await session.list_tools()).tools)==50


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--bundle',type=Path,required=True)
    parser.add_argument('--workspace',type=Path,required=True)
    parser.add_argument('--report',type=Path,required=True)
    args=parser.parse_args()
    os.umask(0o077)
    app=args.bundle.resolve(strict=True);workspace=args.workspace.absolute()
    workspace.mkdir(mode=0o700)
    profile=workspace/'Local'
    info=plistlib.loads((app/'Contents/Info.plist').read_bytes())
    assert info['CFBundleIdentifier']=='io.tapmesh.mac'
    binary=app/'Contents/MacOS/TapMesh'
    report={'product':'TapMesh','checked_at':datetime.now(timezone.utc).isoformat(),
        'build':info['CFBundleVersion'],'macos':platform.mac_ver()[0],'architecture':platform.machine(),
        'environment':'github_actions_runner' if os.environ.get('GITHUB_ACTIONS')=='true' else 'development_mac',
        'scope':'notarized native entrypoint, visible workspace, local HTTP/MCP and cooperative quit; no device operations',
        'finder_first_open_confirmation':'not_exercised','native_update_installation':'not_exercised',
        'status':'running','checks':[]}
    def passed(name):
        report['checks'].append({'name':name,'status':'passed'})
        print(name+': passed',flush=True)
    def command(argv,timeout=45):
        result=subprocess.run([str(v) for v in argv],capture_output=True,timeout=timeout)
        if result.returncode:
            name=Path(str(argv[0])).name
            (workspace/(name+'-stderr.txt')).write_bytes(result.stderr[:16384])
            report['command_error']={'executable':name,'exit_code':result.returncode,
                'stderr_excerpt':result.stderr.decode('utf-8','replace')[:8192]}
            raise RuntimeError(name+' failed with exit code '+str(result.returncode))
        return result.stdout
    process=None;configuration=None
    try:
        command(['/usr/bin/codesign','--verify','--deep','--strict',app])
        command(['/usr/bin/xcrun','stapler','validate',app])
        command(['/usr/sbin/spctl','--assess','--type','execute',app])
        passed('Developer ID signature, stapled ticket and Gatekeeper assessment')
        probe=workspace/'native-window-probe'
        # Compile an immutable local snapshot; SMB can update source mtimes after
        # a new file is closed and make swiftc reject it as modified mid-build.
        probe_source=workspace/'native-window-probe.swift'
        probe_source.write_bytes(Path(__file__).with_name('qualify_native_window.swift').read_bytes())
        command(['/usr/bin/swiftc',probe_source,'-o',probe],timeout=90)
        env={k:os.environ[k] for k in ('HOME','TMPDIR','LANG') if k in os.environ}
        env.update(PATH='/usr/bin:/bin:/usr/sbin:/sbin',TAPMESH_PROFILE_DIR=str(profile))
        process=subprocess.Popen([str(binary)],env=env,cwd=workspace,start_new_session=True,
            stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
        with httpx.Client(timeout=2,trust_env=False) as client:
            deadline=time.monotonic()+75
            while time.monotonic()<deadline:
                if process.poll() is not None:raise RuntimeError('Native app exited before readiness')
                try:
                    configuration=json.loads((profile/'connection.json').read_text())
                    token=(profile/'server.token').read_text().strip()
                    response=client.get(configuration['api_url']+'/api/tools',headers={'Authorization':'Bearer '+token})
                    if response.status_code==200 and len(response.json()['tools'])==50:break
                except (OSError,ValueError,httpx.HTTPError):pass
                time.sleep(.2)
            else:raise TimeoutError('Native application local services did not become ready')
        passed('Native app starts its private local HTTP services')
        visible=json.loads(command([probe,'window',process.pid,binary]))
        assert visible['workspace_window_visible'] is True
        report['ui_automation_trusted']=visible.get('ui_automation_trusted')
        passed('Native workspace window is visible')
        credentials={name:hashlib.sha256((profile/name).read_bytes()).hexdigest()
            for name in ('server.token','agent.token','approvals.token')}
        asyncio.run(check_mcp(app,profile,workspace,env))
        assert all(hashlib.sha256((profile/name).read_bytes()).hexdigest()==value for name,value in credentials.items())
        passed('Native MCP bridge attaches and preserves profile credentials')
        stopped=json.loads(command([probe,'quit',process.pid,binary]))
        assert stopped['termination_requested'] is True
        assert process.wait(timeout=55)==0
        assert services_stopped(configuration)
        passed('Cooperative native quit stops owned local listeners')
        command(['/usr/bin/codesign','--verify','--deep','--strict',app])
        passed('Bundle signature remains valid after native execution')
        report['status']='passed'
    except BaseException as error:
        report.update(status='failed',error_type=type(error).__name__)
        raise
    finally:
        if process is not None and process.poll() is None:
            process.terminate();process.wait(timeout=60)
        if configuration:
            deadline=time.monotonic()+20
            while not services_stopped(configuration) and time.monotonic()<deadline:time.sleep(.2)
            report['owned_listeners_stopped']=services_stopped(configuration)
        args.report.parent.mkdir(parents=True,exist_ok=True)
        args.report.write_text(json.dumps(report,indent=2)+'\n')


if __name__=='__main__':main()
