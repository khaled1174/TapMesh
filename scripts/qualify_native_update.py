#!/usr/bin/env python3
"""Exercise notarized disposable Sparkle fixtures on a fresh macOS user profile.

Requires pre-authorized UI automation; never requests or grants that access.
Refuses an existing default TapMesh profile and never operates an Apple device.
"""
import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import platform
import plistlib
import subprocess
import threading
import time

import httpx
from prepare_update_qualification import FixtureServer, FixtureState
from qualify_local_runtime import services_stopped


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--fixtures',type=Path,required=True)
    parser.add_argument('--report',type=Path,required=True)
    args=parser.parse_args();os.umask(0o077)
    fixtures=args.fixtures.resolve(strict=True)
    profile=Path.home()/'Library/Application Support/TapMesh/Local'
    if profile.exists():raise SystemExit('Use a fresh test user; an existing default profile is never replaced.')
    metadata=json.loads((fixtures/'qualification.json').read_text())
    archive=fixtures/'update.zip'
    assert hashlib.sha256(archive.read_bytes()).hexdigest()==metadata['archive_sha256']
    app=fixtures/'Installed/TapMesh.app';binary=app/'Contents/MacOS/TapMesh'
    report={'product':'TapMesh','checked_at':datetime.now(timezone.utc).isoformat(),
        'macos':platform.mac_ver()[0],'architecture':platform.machine(),'status':'running',
        'scope':'notarized disposable Sparkle fixtures on fresh default user profile; ephemeral update key; no device operations',
        'installed_build':metadata['installed_build'],'candidate_build':metadata['candidate_build'],'checks':[]}
    def passed(name):
        report['checks'].append({'name':name,'status':'passed'});print(name+': passed',flush=True)
    def command(values,timeout=45):
        result=subprocess.run([str(v) for v in values],capture_output=True,timeout=timeout)
        if result.returncode:
            report['command_error']={'executable':Path(str(values[0])).name,'exit_code':result.returncode,
                'stderr_excerpt':result.stderr.decode('utf-8','replace')[:8192]}
            raise RuntimeError('Qualification command failed')
        return result.stdout
    server=None;process=None;configuration=None;probe=fixtures/'sparkle-ui-probe'
    try:
        command(['/usr/bin/codesign','--verify','--deep','--strict',app])
        command(['/usr/bin/xcrun','stapler','validate',app])
        command(['/usr/sbin/spctl','--assess','--type','execute',app])
        passed('Notarized installed fixture is accepted by Gatekeeper')
        probe_source=fixtures/'sparkle-ui-probe.swift'
        probe_source.write_bytes(Path(__file__).with_name('qualify_sparkle_ui.swift').read_bytes())
        command(['/usr/bin/swiftc',probe_source,'-o',probe],timeout=90)
        state=FixtureState(fixtures)
        server=FixtureServer(('127.0.0.1',metadata['port']),state)
        thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
        env={k:os.environ[k] for k in ('HOME','TMPDIR','LANG') if k in os.environ}
        env['PATH']='/usr/bin:/bin:/usr/sbin:/sbin'
        process=subprocess.Popen([str(binary)],cwd=fixtures,env=env,start_new_session=True,
            stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
        def healthy(previous=None):
            try:
                config=json.loads((profile/'connection.json').read_text())
                if previous is not None and config['instance_id']==previous:return None
                token=(profile/'server.token').read_text().strip()
                with httpx.Client(timeout=1,trust_env=False) as client:
                    response=client.get(config['api_url']+'/api/tools',headers={'Authorization':'Bearer '+token})
                    if response.status_code==200 and len(response.json()['tools'])==50:return config
            except (OSError,ValueError,httpx.HTTPError):pass
            return None
        deadline=time.monotonic()+75
        while time.monotonic()<deadline:
            configuration=healthy()
            if configuration:break
            if process.poll() is not None:raise RuntimeError('Installed fixture exited before readiness')
            time.sleep(.2)
        assert configuration is not None
        credentials={name:hashlib.sha256((profile/name).read_bytes()).hexdigest()
            for name in ('server.token','agent.token','approvals.token')}
        passed('Installed fixture starts normal default-profile services')
        def press(mode):
            report['pending_ui_action']=mode
            deadline=time.monotonic()+90
            while time.monotonic()<deadline:
                value=json.loads(command([probe,mode,binary]))
                if value.get('trusted') is False:raise RuntimeError('UI automation prerequisite unavailable')
                if 'action_error' in value:raise RuntimeError('UI action outcome uncertain; action not replayed')
                if value.get('action_performed'):
                    report.pop('pending_ui_action',None);return
                time.sleep(.25)
            raise TimeoutError('Expected Sparkle control did not become available')
        press('check-update');press('install');press('relaunch')
        passed('Native Sparkle UI downloads, verifies and accepts installation')
        previous=configuration['instance_id'];deadline=time.monotonic()+90
        while time.monotonic()<deadline:
            current=healthy(previous)
            build=plistlib.loads((app/'Contents/Info.plist').read_bytes())['CFBundleVersion']
            if current and str(build)==str(metadata['candidate_build']) and not (profile/'update-fence.json').exists():
                configuration=current;break
            time.sleep(.2)
        else:raise TimeoutError('Updated app did not resume its default-profile services')
        assert all(hashlib.sha256((profile/name).read_bytes()).hexdigest()==value for name,value in credentials.items())
        passed('New build relaunches with unchanged credentials and cleared update fence')
        command(['/usr/bin/codesign','--verify','--deep','--strict',app])
        command(['/usr/bin/xcrun','stapler','validate',app])
        command(['/usr/sbin/spctl','--assess','--type','execute',app])
        assert all(not path.stat().st_mode&0o022 for path in [app,*app.rglob('*')] if not path.is_symlink())
        passed('Updated notarized app retains valid signatures, ticket and safe public modes')
        closed=json.loads(command([probe,'quit',binary]));assert closed['termination_requested'] is True
        deadline=time.monotonic()+55
        while not services_stopped(configuration) and time.monotonic()<deadline:time.sleep(.2)
        assert services_stopped(configuration)
        passed('Updated native app quits and stops owned listeners')
        report['status']='passed'
    except BaseException as error:
        report.update(status='failed',error_type=type(error).__name__);raise
    finally:
        if server:server.shutdown();server.server_close()
        if probe.exists():
            try:command([probe,'quit',binary])
            except (RuntimeError,subprocess.SubprocessError):pass
        if process is not None and process.poll() is None:
            process.terminate();process.wait(timeout=55)
        if configuration:report['owned_listeners_stopped']=services_stopped(configuration)
        args.report.parent.mkdir(parents=True,exist_ok=True)
        args.report.write_text(json.dumps(report,indent=2)+'\n')


if __name__=='__main__':main()
