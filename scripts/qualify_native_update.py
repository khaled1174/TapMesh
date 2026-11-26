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
from bundle_integrity import file_digest, require_same_bundle
from prepare_update_qualification import FixtureServer, FixtureState, private_json
from qualify_local_runtime import services_stopped


def ready_fixture(fixtures, app, release_manifest=None, release_bundle=None):
    try:
        metadata=json.loads((fixtures/'qualification.json').read_text())
        installed=plistlib.loads((app/'Contents/Info.plist').read_bytes())
        installed_build=metadata['installed_build']
        target_build=metadata['candidate_build']
        port=metadata['port']
        digest=file_digest(fixtures/'update.zip')
        if (digest!=metadata['archive_sha256']
                or type(installed_build) is not int or type(target_build) is not int
                or not 0<installed_build<target_build
                or type(port) is not int or not 0<port<65536
                or installed['CFBundleIdentifier']!='io.tapmesh.mac'
                or installed['CFBundleVersion']!=str(installed_build)
                or installed.get('TapMeshUpdateStagingMode') is not True
                or installed['SUFeedURL']!=f'http://127.0.0.1:{port}/appcast.xml'):
            raise ValueError()
        if release_manifest is not None:
            release=json.loads(release_manifest.read_text())
            expected=plistlib.loads((release_bundle/'Contents/Info.plist').read_bytes())
            if (release['product']!='TapMesh' or release['notarized'] is not True
                    or release['updater_configured'] is not True or release['source_compliance_verified'] is not True
                    or release['sha256']!=digest
                    or release['build_number']!=target_build
                    or expected['CFBundleIdentifier']!='io.tapmesh.mac'
                    or expected['CFBundleVersion']!=str(target_build)
                    or expected['CFBundleShortVersionString']!=release['version']
                    or expected['SUPublicEDKey']!=installed['SUPublicEDKey']):
                raise ValueError()
        return metadata
    except (OSError, KeyError, TypeError, ValueError, plistlib.InvalidFileException):
        raise SystemExit('The installed fixture and update ZIP must match their qualification inputs.') from None


def valid_fixture_state(fixtures):
    state=FixtureState(fixtures)
    private_json(state.control, {'mode': 'valid'})
    return state


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--fixtures',type=Path,required=True)
    parser.add_argument('--report',type=Path,required=True)
    parser.add_argument('--release-manifest',type=Path,help='Manifest for the exact notarized update ZIP')
    parser.add_argument('--release-bundle',type=Path,help='Unmodified bundle extracted from that release ZIP')
    args=parser.parse_args();os.umask(0o077)
    if (args.release_manifest is None)!=(args.release_bundle is None):
        parser.error('--release-manifest and --release-bundle must be provided together.')
    fixtures=args.fixtures.resolve(strict=True)
    profile=Path.home()/'Library/Application Support/TapMesh/Local'
    if profile.exists():raise SystemExit('Use a fresh test user; an existing default profile is never replaced.')
    app=fixtures/'Installed/TapMesh.app';binary=app/'Contents/MacOS/TapMesh'
    metadata=ready_fixture(fixtures,app,args.release_manifest,args.release_bundle)
    report={'product':'TapMesh','checked_at':datetime.now(timezone.utc).isoformat(),
        'macos':platform.mac_ver()[0],'architecture':platform.machine(),'status':'running',
        'scope':'notarized Sparkle update on fresh default user profile; no device operations',
        'update_target':'exact_release_zip' if args.release_manifest else 'disposable_fixture',
        'update_archive_sha256':metadata['archive_sha256'],
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
        state=valid_fixture_state(fixtures)
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
            for name in ('server.token','agent.token')}
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
        assert 'approval_token_file' not in configuration and not (profile/'approvals.token').exists()
        passed('New build preserves service credentials, keeps approval in memory and clears the update fence')
        command(['/usr/bin/codesign','--verify','--deep','--strict',app])
        command(['/usr/bin/xcrun','stapler','validate',app])
        command(['/usr/sbin/spctl','--assess','--type','execute',app])
        assert all(not path.stat().st_mode&0o022 for path in [app,*app.rglob('*')] if not path.is_symlink())
        passed('Updated notarized app retains valid signatures, ticket and safe public modes')
        if args.release_bundle:
            require_same_bundle(app,args.release_bundle)
            passed('Installed update matches the exact release bundle')
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
