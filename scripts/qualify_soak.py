#!/usr/bin/env python3
"""Bounded two-device capture soak. Keeps metrics only; never stores screen pixels."""
import argparse
import base64
import io
import json
import os
from pathlib import Path
import shutil
import statistics
import subprocess
import time

import httpx
from PIL import Image
from qualify_local_runtime import receive, services_stopped


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--bundle',type=Path,required=True)
    parser.add_argument('--device-id',action='append',required=True)
    parser.add_argument('--duration-seconds',type=int,default=3600)
    parser.add_argument('--interval-seconds',type=int,default=30)
    parser.add_argument('--report',type=Path,required=True)
    parser.add_argument('--workspace',type=Path,default=Path.home()/'Library/Caches/TapMesh/soak-qualification')
    parser.add_argument('--device-kind',choices=('physical','simulator'),required=True,
                        help='Explicitly label the qualification scope; simulator results never qualify physical devices')
    args=parser.parse_args()
    if len(set(args.device_id))!=2 or args.duration_seconds<1 or args.interval_seconds<1:
        parser.error('Choose two distinct explicit devices and positive time bounds.')
    root=args.workspace.expanduser().absolute()
    if root.is_symlink(): raise SystemExit('The owned qualification workspace must not be a symlink.')
    root.mkdir(parents=True,exist_ok=True,mode=0o700)
    bundle=root/'TapMesh.app'
    if bundle.exists(): raise SystemExit('The previous qualification bundle must be reviewed before reusing this test workspace.')
    shutil.copytree(args.bundle,bundle,symlinks=True)
    resources=bundle/'Contents/Resources';profile=root/'Local'
    env={k:os.environ[k] for k in ('HOME','TMPDIR','LANG') if k in os.environ};env['PATH']='/usr/bin:/bin:/usr/sbin:/sbin'
    process=subprocess.Popen([str(resources/'runtime/bin/python3'),'-I','-B','-m','tapmesh_runtime','supervise','--state-dir',str(profile),
        '--resources',str(resources),'--parent-pid',str(os.getpid())],stdin=subprocess.DEVNULL,stdout=subprocess.PIPE,stderr=subprocess.DEVNULL,env=env,cwd=root)
    report={'product':'TapMesh','scope':'two-device read-only capture; no input or stored screen content',
            'device_kind':args.device_kind,'status':'running',
            'memory_scope':'runtime supervisor and all descendants; excludes CoreSimulator host services',
            'required_duration_seconds':args.duration_seconds,'interval_seconds':args.interval_seconds,'samples':[],'errors':[],'memory_samples_kib':[]}
    configuration=None
    started=time.monotonic();next_round=started;last_notice=started
    try:
        receive(process)
        configuration=json.loads((profile/'connection.json').read_text())
        headers={'Authorization':'Bearer '+(profile/'server.token').read_text().strip()}
        with httpx.Client(base_url=configuration['api_url'],headers=headers,timeout=50,trust_env=False) as client:
            sessions=['soak-a','soak-b']
            discovered=client.post('/api/call',json={'session_id':'soak-readiness','tool':'list_devices','arguments':{}}).json()
            ready={item['device_id']:item.get('ready') is True for item in discovered.get('data',{}).get('devices',[])}
            if any(not ready.get(device) for device in args.device_id): raise RuntimeError('Both explicit qualification devices must be ready before the soak starts.')
            for session,device in zip(sessions,args.device_id):
                result=client.post('/api/call',json={'session_id':session,'tool':'select_device','arguments':{'device_id':device}}).json()
                if result.get('status')!='success': raise RuntimeError('An explicit qualification device is unavailable.')
            started=time.monotonic();next_round=started;last_notice=started
            while time.monotonic()-started<args.duration_seconds:
                for index,session in enumerate(sessions):
                    before=time.monotonic()
                    result=client.post('/api/call',json={'session_id':session,'tool':'take_screenshot','arguments':{
                        'format':'jpeg','max_width':1280,'quality':0.7,'require_fresh':True}}).json()
                    elapsed=time.monotonic()-before
                    if result.get('status')!='success':
                        report['errors'].append({'device':index,'at_seconds':round(time.monotonic()-started,2),'code':result.get('error',{}).get('code','capture_failed')})
                        continue
                    data=result['data']; raw=base64.b64decode(data['image']['data'],validate=True)
                    with Image.open(io.BytesIO(raw)) as image:
                        image.load()
                        if image.convert('RGB').getbbox() is None: raise RuntimeError('A qualification capture was blank.')
                        size=[image.width,image.height]
                    report['samples'].append({'device':index,'at_seconds':round(time.monotonic()-started,2),'capture_seconds':round(elapsed,3),'image_size':size})
                metadata=subprocess.check_output(['/bin/ps','-axo','pid=,ppid=,rss='],text=True)
                rows=[tuple(map(int,line.split())) for line in metadata.splitlines() if len(line.split())==3]
                owned={process.pid}
                while True:
                    descendants=owned|{pid for pid,parent,rss in rows if parent in owned}
                    if descendants==owned: break
                    owned=descendants
                report['memory_samples_kib'].append(sum(rss for pid,parent,rss in rows if pid in owned))
                report['elapsed_seconds']=round(time.monotonic()-started,2)
                args.report.write_text(json.dumps(report,indent=2)+'\n')
                if time.monotonic()-last_notice>=60:
                    print(f"Soak: {int(report['elapsed_seconds'])} seconds, {len(report['samples'])} captures, {len(report['errors'])} errors",flush=True)
                    last_notice=time.monotonic()
                next_round+=args.interval_seconds
                while time.monotonic()<min(next_round,started+args.duration_seconds):
                    if process.poll() is not None: raise RuntimeError('Owned runtime stopped during qualification.')
                    time.sleep(min(.5,max(0,min(next_round,started+args.duration_seconds)-time.monotonic())))
        report['elapsed_seconds']=round(time.monotonic()-started,2)
        report['status']='passed' if not report['errors'] and report['elapsed_seconds']>=args.duration_seconds else 'failed'
        latencies=sorted(sample['capture_seconds'] for sample in report['samples'])
        if latencies:
            report['capture_seconds_median']=statistics.median(latencies)
            report['capture_seconds_p95']=latencies[min(len(latencies)-1,int(len(latencies)*.95))]
        if report['memory_samples_kib']:
            report['runtime_rss_peak_kib']=max(report['memory_samples_kib'])
            report['runtime_rss_first_kib']=report['memory_samples_kib'][0]
            report['runtime_rss_last_kib']=report['memory_samples_kib'][-1]
    except BaseException:
        report['status']='failed';report['errors'].append({'code':'qualification_interrupted_or_unavailable'})
        raise
    finally:
        if process.poll() is None: process.terminate();process.wait(timeout=50)
        if configuration: report['owned_listeners_stopped']=services_stopped(configuration)
        report['bundle_signature_preserved']=subprocess.run(['/usr/bin/codesign','--verify','--deep','--strict',str(bundle)],capture_output=True).returncode==0
        if report['status']=='passed' and (not report.get('owned_listeners_stopped') or not report['bundle_signature_preserved']):
            report['status']='failed'
        args.report.write_text(json.dumps(report,indent=2)+'\n')
    print('Soak final status:',report['status'])
    if report['status']!='passed': raise SystemExit(1)


if __name__=='__main__': main()
