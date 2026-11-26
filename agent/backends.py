"""Public Apple CLI + public idb simulator adapter. No original product code."""
import asyncio
import base64
from copy import deepcopy
from datetime import datetime, timezone
import errno
import io
import json
import logging
import math
import os
from pathlib import Path
import plistlib
import re
import shutil
import signal
import stat
import sys
import tempfile
import time
import uuid
import zipfile
from urllib.parse import urlsplit
from PIL import Image
from Contracts.catalog import CATALOG
from .native_physical import NativePhysical, locked_input_error
from .process import Runner, minimal_environment
from .safety import BridgeError, utcnow

DISCOVERY_TTL_SECONDS=1.0
LOCK_STATE_TTL_SECONDS=1.0
FRESH_GESTURE_TOOLS={'swipe','drag','pinch'}
IOS_SIMULATOR_RUNTIME_PREFIX='com.apple.CoreSimulator.SimRuntime.iOS-'
LANDSCAPE_ORIENTATIONS={'landscapeLeft','landscapeRight','landscape_left','landscape_right'}
PORTRAIT_ORIENTATIONS={'portrait','portraitUpsideDown','portrait_upside_down'}
logger=logging.getLogger(__name__)


def simulator_os_version(runtime_id: str) -> str | None:
    if not runtime_id.startswith(IOS_SIMULATOR_RUNTIME_PREFIX): return None
    version=runtime_id.removeprefix(IOS_SIMULATOR_RUNTIME_PREFIX)
    return version.replace('-','.') if re.fullmatch(r'[0-9]+(?:-[0-9]+){1,2}',version) else None


def prune_derived_build_data(root):
    cutoff=time.time()-7*86400
    try:
        directory=os.open(root,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW)
    except OSError as exc:
        if exc.errno not in {errno.ENOENT,errno.ENOTDIR,errno.ELOOP}:
            logger.warning('Could not open the build cache for cleanup: %s',type(exc).__name__)
        return
    try:
        for name in os.listdir(directory):
            if not re.fullmatch('[0-9a-f]{32}',name): continue
            try:
                entry=os.stat(name,dir_fd=directory,follow_symlinks=False)
                if stat.S_ISDIR(entry.st_mode) and entry.st_mtime<cutoff:
                    shutil.rmtree(name,dir_fd=directory)
            except FileNotFoundError:
                continue
            except OSError as exc:
                logger.warning('Could not remove an old derived build folder: %s',type(exc).__name__)
    except OSError as exc:
        logger.warning('Could not list the build cache for cleanup: %s',type(exc).__name__)
    finally:
        os.close(directory)


def normalize_physical(raw, epoch):
    p=raw.get('properties',{}); hw=p.get('hardware',raw.get('hardwareProperties',{})); state=p.get('state',raw.get('deviceProperties',{})); connection=p.get('connection',raw.get('connectionProperties',{}))
    connection_state=connection.get('state',connection.get('tunnelState','unknown'))
    if connection.get('pairingState')!='paired':
        code,reason='unpaired',"This device isn't paired with this Mac."
    elif state.get('developerModeStatus')=='disabled':
        code,reason='developer_mode_off','Developer Mode is off on this device.'
    elif state.get('bootState','booted')!='booted':
        code,reason='not_booted',"This device hasn't finished starting."
    elif connection_state in {'unavailable','unpaired'}:
        code,reason='unavailable',"This device isn't reachable right now."
    else:
        code,reason=None,None
    return {
        'device_id':'ios:'+raw['identifier'],'native_id':raw['identifier'],'name':state.get('name','iOS device'),
        'backend':'physical','model':hw.get('marketingName',hw.get('productType','unknown')),
        'os_version':p.get('software',{}).get('osVersionNumber',{}).get('stringValue',state.get('osVersionNumber','unknown')),
        'ready':code is None,'readiness_code':code,'readiness_reasons':[reason] if reason else [],
        'connection_epoch':epoch,'connection_state':connection_state,
    }


def normalize_ax(raw, include_invisible=False, logical_size=None):
    result=[]
    def visit(value):
        if isinstance(value,list):
            for item in value: visit(item)
        elif isinstance(value,dict):
            frame=value.get('frame')
            if frame and all(isinstance(frame.get(k),(int,float)) for k in ('x','y','width','height')):
                if include_invisible or frame['width']>0 and frame['height']>0 and (logical_size is None or frame['x']<logical_size['width'] and frame['y']<logical_size['height'] and frame['x']+frame['width']>0 and frame['y']+frame['height']>0):
                    result.append({'label':value.get('AXLabel') or value.get('label') or value.get('title') or '', 'value':value.get('AXValue',value.get('value')),'role':value.get('role') or value.get('type') or 'unknown','identifier':value.get('AXUniqueId',value.get('identifier')),'bounds':frame,'enabled':value.get('enabled'),'source':'accessibility','secure':('secure' in str(value.get('type','')).lower() or 'secure' in str(value.get('role','')).lower()),'_pid':value.get('pid')})
            for key in ('children','elements'): visit(value.get(key,[]))
    visit(raw)
    return result


def display_geometry(raw):
    displays=raw.get('displays',raw.get('displayInfo',[]))
    if isinstance(displays,dict): displays=list(displays.values())
    for display in sorted(displays,key=lambda d:not d.get('primary',False)):
        state=raw.get('orientation',{})
        orientation=display.get('orientation') or state.get('currentDeviceOrientation','unknown')
        if orientation in {'faceUp','faceDown','face_up','face_down','unknown'}:
            orientation=state.get('currentDeviceNonFlatOrientation',orientation)
        landscape=orientation in LANDSCAPE_ORIENTATIONS
        portrait=orientation in PORTRAIT_ORIENTATIONS
        def result(width,height,**metadata):
            # Apple display bounds can retain framebuffer axes after rotation.
            # Align them using explicit orientation, including the non-flat
            # orientation Apple reports when the device lies on a table.
            if landscape and width<height or portrait and width>height:
                width,height=height,width
            return {'logical_size':{'width':width,'height':height},'orientation':orientation,**metadata}
        size=display.get('logicalSize') or display.get('sizeInPoints') or display.get('logicalDimensions')
        if size and size.get('width') and size.get('height'):
            return result(size['width'],size['height'])
        scale=display.get('pointScale'); bounds=display.get('bounds')
        if isinstance(scale,(float,int)) and 0<scale<=8 and isinstance(bounds,list) and len(bounds)==2 and len(bounds[1])==2:
            return result(bounds[1][0]/scale,bounds[1][1]/scale,density=scale)
    raise BridgeError('unsupported_capability','Physical display does not expose explicit logical size or pointScale')


class MacBackend:
    def __init__(self, state_dir, *, runner=None):
        self.state=Path(state_dir).expanduser().resolve(); self.state.mkdir(parents=True,exist_ok=True,mode=0o700)
        prune_derived_build_data(self.state/'builds')
        self.runner=runner or Runner(); self.native_lock=asyncio.Lock(); self.native_serials={}
        self.native_physical=NativePhysical()
        self.companion_path=os.environ.get('TAPMESH_IDB_COMPANION')
        idb=Path(sys.executable).with_name('idb'); self.idb_path=str(idb) if idb.exists() else shutil.which('idb')
        if os.environ.get('TAPMESH_BUNDLED_RUNTIME')=='1':
            import importlib.util
            self.idb_path=sys.executable if importlib.util.find_spec('idb') else None
        self.socket_root=Path(tempfile.mkdtemp(prefix='tapmesh-')); os.chmod(self.socket_root,0o700)
        self.companions={}; self.companion_locks={}; self.identities={}; self.recordings={}; self.crashes={}; self.health={}; self.discovery_lock=asyncio.Lock()
        self.physical_recording_capabilities={}
        # Physical input safety keeps exactly one check: the device lock. A
        # confirmed-unlocked read is reused briefly and refreshed in the
        # background; locked or unknown results are never cached.
        self.lock_states={}; self.lock_refreshes={}; self.native_epochs={}
        # Router calls discover several times per request; one short-lived
        # result serves them. Any non-input mutation invalidates it.
        self.discovery_cache={}; self.discovery_ttl=DISCOVERY_TTL_SECONDS
    async def close(self):
        async def finalize(recording):
            try: await self._stop_recording(recording)
            except Exception: pass
        try:
            async with asyncio.timeout(22):
                await asyncio.gather(*(finalize(r) for r in list(self.recordings.values()) if r.get('process') and not r.get('final')))
        except TimeoutError: pass
        finally:
            self.companions.clear()
            for task in self.lock_refreshes.values(): task.cancel()
            self.lock_refreshes.clear(); self.lock_states.clear(); self.discovery_cache.clear()
            try: await self.native_physical.aclose()
            finally:
                try: await self.runner.close()
                finally: shutil.rmtree(self.socket_root,ignore_errors=True)
    def _epoch(self, did, fingerprint):
        old=self.identities.get(did)
        epoch=1 if old is None else old[1]+(old[0]!=fingerprint)
        self.identities[did]=(fingerprint,epoch)
        return epoch
    def capabilities(self, backend, ready):
        caps={t['capability']:{'status':'unsupported','reason':'This adapter has no qualified public route for this operation'} for t in CATALOG.values()}
        common={'device.discover','device.select','device.info','screen.capture','screen.record','apps.list','apps.launch','apps.terminate','apps.install','apps.uninstall','display.rotate','settings.appearance','settings.accessibility','location.simulate','clipboard.read','clipboard.write','apps.container_export','diagnostics.crash_list','diagnostics.crash_read','build.xcode','workflow.handoff','workflow.observe','workflow.delay','workflow.batch','system.open_settings','apps.open_url'}
        if backend=='simulator':
            common|={'simulator.boot','simulator.shutdown'}
            idb_caps={'apps.foreground','ui.inspect','ui.alert','input.tap','input.double_tap','input.long_press','input.swipe','input.scroll','input.drag','input.pinch','input.text','input.fill','input.key','input.keyboard_dismiss','system.button','system.gesture','navigation.back','display.rotate'}
            for cap in idb_caps:
                caps[cap]={'status':'supported' if self.companion_path and self.idb_path else 'requires_user_action','reason':'Public idb companion simulator route' if self.companion_path and self.idb_path else 'Configure TAPMESH_IDB_COMPANION to the qualified public idb companion and install fb-idb'}
        if backend=='simulator' and self.companion_path and self.idb_path:
            for cap in ('ui.inspect','ui.alert','apps.foreground','navigation.back','input.fill'):
                caps[cap]={'status':'degraded','reason':'Public idb route available; repeated AX reads after app relaunch remain a qualification gate on Xcode 27'}
        for cap in common:
            if cap=='display.rotate' and backend=='simulator': continue
            caps[cap]={'status':'supported' if ready else 'degraded','reason':'Public simctl route' if backend=='simulator' else 'Public Xcode devicectl route; operation-specific device support and trust are checked at execution'}
        if backend=='physical':
            from .native_physical import capability_profile
            caps.update(capability_profile())
        if backend=='simulator':
            caps['input.text']['limits']={'character_set':'idb HID key map; Unicode replacement is available through AX fill','implicit_clipboard_fallback':False}
            caps['input.fill']['limits']={'frame_validation':['exact'],'replacement':'AX set-value'}
        caps['settings.accessibility']['limits']={'fields':['content_size_category'] if backend=='simulator' else ['content_size_category','reduce_motion']}
        caps['settings.appearance']['limits']={'appearance':['light','dark']}
        caps['location.simulate']['limits']={'altitude':False,'persistence':'until cleared'}
        if backend=='simulator':
            caps['system.button']['limits']={'buttons':['home','lock','side_button','siri','volume_up','volume_down']}
            caps['system.gesture']['limits']={'gestures':['home','app_switcher','control_center','notification_center']}
            caps['input.key']['limits']={'keys':['return','delete','escape','tab','space','up','down','left','right','select_all'],'modifiers':['shift','control','alt','command']}
        return caps
    async def _physical_recording_capability(self, device, *, refresh=False):
        if not device['ready']:
            return {'status':'degraded','reason':'Reconnect the device before checking screen recording support.'}
        did=device['device_id']; epoch=device['connection_epoch']; now=time.monotonic()
        cached=self.physical_recording_capabilities.get(did)
        if not refresh and cached and cached[0]==epoch and now<cached[1]:
            return dict(cached[2])
        # `list devices` exposes only a small discovery subset of capabilities.
        # Only the complete, device-bound details response can establish absence.
        capability={'status':'degraded','reason':'Xcode has not confirmed screen recording support for this device.'}
        try:
            details=await self._dev(['device','info','details'],device,timeout=10)
            features=details.get('capabilities')
            if isinstance(features,list) and all(isinstance(item,dict) and isinstance(item.get('featureIdentifier'),str) for item in features):
                supported=any(item['featureIdentifier']=='com.apple.coredevice.feature.screenrecording' for item in features)
                capability={'status':'supported' if supported else 'unsupported',
                            'reason':'Xcode reports screen recording available on this device.' if supported else 'Screen recording through Xcode is not available on this device.'}
        except BridgeError:
            pass
        self.physical_recording_capabilities[did]=(epoch,time.monotonic()+30,capability)
        return dict(capability)
    async def _sim(self, *args, mutation=False, timeout=30, input=None, env=None, check=True):
        return await self.runner.run(['xcrun','simctl',*args],timeout=timeout,input=input,env=env,mutation=mutation,check=check)
    async def _dev(self, args, device=None, *, mutation=False, timeout=30, input=None):
        # Read-only queries (discovery, lock state, displays, screenshots) never
        # queue behind each other or behind a long mutation such as an install.
        if not mutation: return await self._dev_serialized(args,device,timeout=timeout,input=input)
        async with self.native_lock:
            return await self._dev_serialized(args,device,mutation=mutation,timeout=timeout,input=input)
    async def _dev_serialized(self, args, device=None, *, mutation=False, timeout=30, input=None):
        # Launch treats everything after the bundle as app arguments, even
        # flags before '--'. Put every devicectl option before that position.
        boundary=3 if args[:3]==['device','process','launch'] else (args.index('--') if '--' in args else len(args))
        argv=['xcrun','devicectl',*args[:boundary]]
        if device: argv+=['--device',device['native_id']]
        argv+=['--timeout',str(timeout),'--json-output','-','--omit-deprecated-fields-in-json','--quiet']
        argv+=args[boundary:]
        output=await self.runner.run(argv,timeout=timeout+2,input=input,mutation=mutation)
        try: return json.loads(output.stdout).get('result',{})
        except (ValueError,AttributeError) as exc: raise BridgeError('backend_failure','Apple backend returned invalid structured output',side_effect_state='unknown' if mutation else 'not_sent') from exc
    async def refresh_discovery(self, include_all=False):
        # Post-send validation must bypass even a cache populated by a discovery
        # that was already in flight when the input completed.
        return await self.discover(include_all, fresh=True)
    async def discover(self, include_all=False, *, fresh=False):
        cached=self.discovery_cache.get(include_all)
        if not fresh and cached and time.monotonic()<cached[0]: return deepcopy(cached[1])
        async with self.discovery_lock:
            cached=self.discovery_cache.get(include_all)
            if not fresh and cached and time.monotonic()<cached[0]: return deepcopy(cached[1])
            results=await self._discover(include_all)
            if self.discovery_ttl>0: self.discovery_cache[include_all]=(time.monotonic()+self.discovery_ttl,deepcopy(results))
            return results
    async def _discover(self, include_all):
        results=[]; seen=set()
        try:
            raw=json.loads((await self._sim('list','devices','--json')).stdout)
            self.health['simctl']='available'
            for runtime,devices in raw.get('devices',{}).items():
                if not runtime.startswith(IOS_SIMULATOR_RUNTIME_PREFIX): continue
                version=simulator_os_version(runtime)
                for item in devices:
                    did='sim:'+item['udid']; seen.add(did)
                    available=item.get('isAvailable',True)
                    ready=item.get('state')=='Booted' and available
                    epoch=self._epoch(did,(item.get('state'),item.get('lastBootedAt'),available))
                    if not include_all and not ready: continue
                    code=None if ready else 'simulator_shutdown' if available else 'runtime_missing'
                    reasons=[] if ready else ['Simulator is not booted' if available else 'Required simulator runtime is unavailable']
                    device={'device_id':did,'native_id':item['udid'],'name':item['name'],'backend':'simulator','runtime_id':runtime,'ready':ready,'readiness_code':code,'readiness_reasons':reasons,'connection_epoch':epoch,'state':item.get('state'),'os_version':version}
                    device['capabilities']=self.capabilities('simulator',ready); results.append(device)
        except (BridgeError,ValueError): self.health['simctl']='unavailable'
        try:
            raw=await self._dev(['list','devices'],timeout=10)
            self.health['devicectl']='available'
            for item in raw.get('devices',[]):
                properties=item.get('properties',{})
                hw=properties.get('hardware',item.get('hardwareProperties',{}))
                if hw.get('reality')=='simulated' or hw.get('platform') not in {'iOS','iPadOS'}: continue
                did='ios:'+item['identifier']; seen.add(did)
                if hw.get('udid'): self.native_serials[did]=hw['udid']
                connection=properties.get('connection',item.get('connectionProperties',{}))
                state=connection.get('state',connection.get('tunnelState'))
                # A paired network device's transport is connected lazily for
                # each public command. Ordinary tunnel teardown is not a device
                # reconnection; unavailable/paired identity transitions are.
                device_state=properties.get('state',item.get('deviceProperties',{}))
                dev_mode_off=device_state.get('developerModeStatus')=='disabled'
                epoch=self._epoch(did,(hw.get('udid'),connection.get('pairingState'),state in {'unavailable','unpaired'},device_state.get('bootState'),dev_mode_off))
                device=normalize_physical(item,epoch); device['capabilities']=self.capabilities('physical',device['ready'])
                device['capabilities']['screen.record']=await self._physical_recording_capability(device)
                results.append(device)
        except (BridgeError,ValueError): self.health['devicectl']='unavailable'
        for did,(fingerprint,epoch) in list(self.identities.items()):
            if did not in seen and fingerprint!='absent': self.identities[did]=('absent',epoch+1)
        for did in self.physical_recording_capabilities.keys()-seen:
            self.physical_recording_capabilities.pop(did)
        # A disconnected device or a new connection epoch ends its input session.
        for did,epoch in list(self.native_epochs.items()):
            if did not in seen or self.identities.get(did,(None,None))[1]!=epoch:
                await self._close_native(did)
        return results
    async def _companion(self, device):
        if not self.companion_path or not self.idb_path: raise BridgeError('prerequisite_required','Configure the qualified idb client and companion')
        udid=device['native_id']
        async with self.companion_locks.setdefault(udid,asyncio.Lock()):
            old=self.companions.get(udid)
            if old and old[0].returncode is None: return old[1]
            socket=self.socket_root/(uuid.uuid4().hex[:12]+'.sock')
            # Companion startup logs can dump its environment. Both streams are
            # discarded, and only a minimal environment is inherited.
            proc=await asyncio.create_subprocess_exec(self.companion_path,'--udid',udid,'--grpc-domain-sock',str(socket),'--log-level','info','--terminate-offline','1',stdout=asyncio.subprocess.DEVNULL,stderr=asyncio.subprocess.DEVNULL,stdin=asyncio.subprocess.DEVNULL,start_new_session=True,env=minimal_environment())
            self.runner.own(proc)
            try:
                for _ in range(100):
                    if proc.returncode is not None: raise BridgeError('prerequisite_required','idb companion could not attach to this simulator')
                    if socket.exists():
                        socket.chmod(0o600); self.companions[udid]=(proc,socket); return socket
                    await asyncio.sleep(.1)
                raise BridgeError('prerequisite_required','idb companion startup did not become ready')
            except BaseException:
                await self.runner.terminate(proc); socket.unlink(missing_ok=True); raise
    async def _reset_companion(self, device):
        async with self.companion_locks.setdefault(device['native_id'],asyncio.Lock()):
            previous=self.companions.pop(device['native_id'],None)
            if previous:
                await self.runner.terminate(previous[0]); previous[1].unlink(missing_ok=True)
    async def _idb(self, device, *args, mutation=False, timeout=30, json_output=False):
        socket=await self._companion(device)
        if len(args)>=2 and args[0]=='ui' and args[1]=='describe-all':
            if '--client-type' not in args: args=(*args,'--client-type','xctest')
            if '--format' not in args: args=(*args,'--format','complete')
        command=[sys.executable,'-I','-B','-m','idb.cli.main'] if os.environ.get('TAPMESH_BUNDLED_RUNTIME')=='1' else [self.idb_path]
        argv=[*command,'--log','ERROR','--companion',str(socket),*map(str,args)]
        if json_output: argv+=['--json']
        output=await self.runner.run(argv,timeout=timeout,mutation=mutation)
        if not json_output: return output.stdout.decode('utf-8','replace')
        try:
            text=output.stdout.decode('utf-8')
            try: return json.loads(text)
            except ValueError: return [json.loads(line) for line in text.splitlines() if line.strip()]
        except ValueError as exc: raise BridgeError('backend_failure','idb returned invalid structured output') from exc
    async def geometry(self, device):
        if device['backend']=='simulator':
            raw=await self._idb(device,'describe',json_output=True)
            dimensions=raw.get('screen_dimensions',{})
            if not dimensions.get('width_points') or not dimensions.get('height_points'): raise BridgeError('prerequisite_required','idb did not report logical screen geometry')
            width,height=dimensions['width_points'],dimensions['height_points']
            orientation='unknown'
            try: orientation=(await self._idb(device,'rotation',json_output=True)).get('orientation','UNKNOWN').lower()
            except BridgeError: pass
            if orientation.startswith('landscape') and width<height: width,height=height,width
            return {'logical_size':{'width':width,'height':height},'orientation':orientation,'density':dimensions.get('density',1)}
        serial=self.native_serials.get(device['device_id'])
        if serial:
            await self._bind_native_epoch(device,serial)
            raw=await self.native_physical.display_info(serial)
            if raw is not None:
                try: return display_geometry(raw)
                except BridgeError: pass
        raw=await self._dev(['device','info','displays'],device)
        return display_geometry(raw)
    async def modal(self, device):
        if device['backend']!='simulator': raise BridgeError('unsupported_capability','Physical modal inspection requires a qualified runner')
        raw=await self._idb(device,'ui','describe-all','--api','axbridge','--format','complete',json_output=True)
        return raw.get('modal')
    async def release_input(self, device, context):
        if device['backend']!='simulator': return
        import logging
        from idb.grpc.client import Client
        from idb.common.types import DomainSocketAddress, HIDPress, HIDTouch, HIDDirection, HIDKey, Point
        socket=await self._companion(device)
        targets=context.get('targets',{})
        point=next(iter(targets.values()),(0,0))
        async def events():
            yield HIDPress(action=HIDTouch(point=Point(x=point[0],y=point[1])),direction=HIDDirection.UP)
            for key in (224,225,226,227): yield HIDPress(action=HIDKey(keycode=key),direction=HIDDirection.UP)
        async with Client.build(DomainSocketAddress(str(socket)),logger=logging.getLogger('tapmesh.idb.release')) as client:
            await client.hid(events())
    def _native_serial(self, device):
        serial=self.native_serials.get(device['device_id'])
        if not serial: raise BridgeError('prerequisite_required','Public device discovery did not expose an explicit native serial binding')
        return serial
    async def _close_native(self, did):
        self.native_epochs.pop(did,None); self.lock_states.pop(did,None)
        serial=self.native_serials.get(did)
        if serial: await self.native_physical.close_device(serial)
    async def _bind_native_epoch(self, device, serial):
        # Epochs only grow; a request bound to an older epoch never tears down
        # the newer connection's session (the router rejects it separately).
        did=device['device_id']; epoch=device['connection_epoch']; bound=self.native_epochs.get(did)
        if bound is not None and epoch<bound: return
        if bound is not None and epoch>bound: await self._close_native(did)
        self.native_epochs[did]=epoch
    def _check_native_binding(self, device, serial):
        did=device['device_id']; epoch=device['connection_epoch']
        discovered=self.identities.get(did)
        if (self.native_epochs.get(did)!=epoch or self.native_serials.get(did)!=serial
                or discovered is not None and (discovered[0]=='absent' or discovered[1]!=epoch)):
            raise BridgeError('connection_changed','The physical device connection changed before input')
    async def _read_lock_state(self, device, serial):
        did=device['device_id']; epoch=device['connection_epoch']; started=time.monotonic()
        self._check_native_binding(device,serial)
        try:
            required=await self.native_physical.lock_state(serial)
            self._check_native_binding(device,serial)
            if type(required) is not bool:
                lock=await self._dev(['device','info','lockState'],device,timeout=10)
                self._check_native_binding(device,serial)
                required=lock.get('passcodeRequired') if isinstance(lock,dict) else None
        except BaseException:
            if self.native_epochs.get(did)==epoch and self.native_serials.get(did)==serial:
                self.lock_states.pop(did,None)
            raise
        if required is False:
            self.lock_states[did]=(epoch,started,serial); return True
        self.lock_states.pop(did,None)
        # Touches or keys on a locked device can enter passcode digits, and
        # repeated wrong passcodes can disable or erase it.
        if required is True: return False
        raise BridgeError('prerequisite_required','TapMesh could not verify the device lock state')
    async def _require_unlocked(self, device, serial, *, refresh=True):
        self._check_native_binding(device,serial)
        did=device['device_id']; now=time.monotonic(); cached=self.lock_states.get(did)
        if cached and cached[0]==device['connection_epoch'] and cached[2]==serial and now-cached[1]<LOCK_STATE_TTL_SECONDS:
            if refresh and now-cached[1]>=LOCK_STATE_TTL_SECONDS/2:
                running=self.lock_refreshes.get(did)
                if not running or running.done():
                    async def refresh_lock(snapshot=dict(device)):
                        try: await self._read_lock_state(snapshot,serial)
                        except Exception: pass
                    self.lock_refreshes[did]=asyncio.create_task(refresh_lock())
            return
        if not await self._read_lock_state(device,serial): raise locked_input_error()
    async def _native_input(self, tool, device, args, context):
        # The router dispatch flag is refined here to the first actual native
        # input or explicit clipboard write, not transport/media setup.
        context['sent']=False
        serial=self._native_serial(device)
        await self._bind_native_epoch(device,serial)
        fresh_checks=context.get('in_batch',False) or tool in FRESH_GESTURE_TOOLS or tool in {'type_text','fill','double_tap','long_press'}
        self._check_native_binding(device,serial)
        # Motion gestures already check immediately before the first HID report
        # and during a long gesture. A check before geometry adds no protection.
        if tool not in FRESH_GESTURE_TOOLS:
            await self._require_unlocked(device,serial,refresh=not fresh_checks)
        geometry=await self.geometry(device)
        self._check_native_binding(device,serial)
        targets=context.get('targets',{})
        for _,_,record in targets.values():
            if record is None: continue
            public=record['public']
            size=public.get('logical_size'); orientation=public.get('orientation','unknown')
            if ((size is not None and size!=geometry['logical_size'])
                    or (orientation!='unknown' and orientation!=geometry.get('orientation'))):
                raise BridgeError('stale_observation','The display changed before native input. Capture the screen again.')
        async def guard():
            # Runs after transport/media are ready, immediately before the first
            # report. Normally a cached read; it never captures the screen.
            await self._require_unlocked(device,serial)
        async def still_unlocked():
            pending=self.lock_refreshes.get(device['device_id'])
            if pending and not pending.done():
                # Let an earlier background read settle before the fresh read
                # so it cannot restore an outdated unlocked cache entry.
                await asyncio.shield(pending)
            return await self._read_lock_state(device,serial)
        def on_send(): context['sent']=True
        try:
            return await self.native_physical.input(tool,serial,geometry,targets,args,guard=None if fresh_checks else guard,on_send=on_send,still_unlocked=still_unlocked if fresh_checks else None)
        except BridgeError as exc:
            did=device['device_id']
            if (exc.code not in {'protected_action_requires_human','invalid_arguments','unsupported_capability','stale_observation'}
                    and self.native_epochs.get(did)==device['connection_epoch'] and self.native_serials.get(did)==serial):
                self.lock_states.pop(did,None)
            raise
    async def live(self, device):
        """A read-only viewer of a wired physical device's live video stream
        (the stream screenshots and touch already use). Simulators and network
        devices have no persistent stream."""
        serial=self.native_serials.get(device['device_id']) if device['backend']=='physical' else None
        if not serial: raise BridgeError('live_unavailable','Live video needs a USB-connected physical device; use screenshots')
        await self._bind_native_epoch(device,serial)
        subscriber=await self.native_physical.subscribe(serial)
        if subscriber is None: raise BridgeError('live_unavailable','The device video stream is unavailable; use screenshots',retryable=True)
        return subscriber
    async def _capture(self, device):
        if device['backend']=='physical':
            serial=self.native_serials.get(device['device_id'])
            if serial:
                await self._bind_native_epoch(device,serial)
                content=await self.native_physical.screenshot(serial)
                # A live-stream frame arrives decoded; a capture-service frame as PNG.
                if isinstance(content,Image.Image): return content,'coredevice_stream'
                if content is not None and len(content)<=32*1024*1024: return content,'coredevice'
        with tempfile.TemporaryDirectory(prefix='tapmesh-capture-',dir=self.state) as directory:
            path=Path(directory)/'capture.png'
            if device['backend']=='simulator': await self._sim('io',device['native_id'],'screenshot','--type=png',str(path))
            else: await self._dev(['device','capture','screenshot','--destination',str(path)],device)
            if not path.is_file() or path.stat().st_size>32*1024*1024: raise BridgeError('backend_failure','Invalid screenshot artifact')
            return path.read_bytes(),'simctl' if device['backend']=='simulator' else 'devicectl'
    async def _screenshot(self, tool, device, args, context):
        async def current_geometry():
            try: return await self.geometry(device)
            except BridgeError as exc: return exc
        # The frame and its display geometry are read concurrently.
        (raw,source),geometry=await asyncio.gather(self._capture(device),current_geometry())
        physical=device['backend']=='physical'
        if isinstance(geometry,BridgeError) and (tool!='take_screenshot' or not physical):
            raise geometry
        orientation=geometry['orientation'] if not isinstance(geometry,BridgeError) else 'unknown'
        image_format=args.get('format','jpeg' if tool=='take_screenshot' else 'png')
        def encode():
            if isinstance(raw,Image.Image): image=raw.copy()
            else: image=Image.open(io.BytesIO(raw)); image.load()
            source_size=image.size
            # The frame and native metadata can straddle a rotation. A rotated
            # image cannot establish device coordinates until the reads agree.
            orientation_mismatch=physical and (
                orientation in LANDSCAPE_ORIENTATIONS and image.width<image.height
                or orientation in PORTRAIT_ORIENTATIONS and image.width>image.height
            )
            if orientation_mismatch and tool=='take_screenshot':
                raise BridgeError('stale_observation','The screenshot and display orientation do not match. Capture again after rotation settles.',retryable=True)
            if tool=='take_screenshot':
                maximum=args.get('max_width',1280)
                if image.width>maximum: image.thumbnail((maximum,16384))
            elif not args.get('full_resolution',True): image.thumbnail((1280,16384))
            if image_format=='jpeg': image=image.convert('RGB')
            output=io.BytesIO()
            options={'quality':round(args.get('quality',.8)*100)} if image_format=='jpeg' else {}
            image.save(output,format='JPEG' if image_format=='jpeg' else 'PNG',**options)
            encoded=output.getvalue()
            if len(encoded)>8*1024*1024: raise BridgeError('backend_failure','Encoded screenshot exceeds 8 MiB; request a smaller image')
            if tool=='take_screenshot': encoded=base64.b64encode(encoded).decode()
            return source_size,image.size,encoded,orientation_mismatch
        source_size,image_size,encoded,orientation_mismatch=await asyncio.to_thread(encode)
        if isinstance(geometry,BridgeError):
            # A physical screenshot without known display geometry is view-only.
            geometry={'logical_size':{'width':source_size[0],'height':source_size[1]},'orientation':'unknown','geometry_source':'image_pixels_only','input_geometry_qualified':False}
        if not orientation_mismatch and geometry.get('density'):
            expected=(round(geometry['logical_size']['width']*geometry['density']),round(geometry['logical_size']['height']*geometry['density']))
            if source_size!=expected and source_size==tuple(reversed(expected)):
                logical=geometry['logical_size']; logical['width'],logical['height']=logical['height'],logical['width']
        router=context['router']
        if orientation_mismatch:
            # Saving only needs the pixels. Return their dimensions without
            # registering an observation that an input could target.
            observation={'source':source,'image_size':dict(zip(('width','height'),image_size)),
                         'input_geometry_qualified':False}
        else:
            observation=router.observations.issue(context['session_id'],device['device_id'],device['connection_epoch'],geometry['logical_size'],image_size,source,orientation=geometry['orientation'])
            if geometry.get('input_geometry_qualified') is False: observation.update(input_geometry_qualified=False,geometry_source='image_pixels_only')
        if tool=='take_screenshot': return {**observation,'image':{'mime_type':'image/jpeg' if image_format=='jpeg' else 'image/png','data':encoded},'requested_max_width':args.get('max_width',1280),'effective_width':image_size[0]}
        artifact=router.artifacts.write(encoded,'screenshot','.'+image_format,device['device_id'],device['connection_epoch'],destination=args.get('destination'),filename=args.get('filename'))
        return {**observation,'artifact':artifact}
    async def _apps(self, device, args):
        if device['backend']=='simulator':
            output=await self._sim('listapps',device['native_id'])
            try:
                converted=await self.runner.run(['/usr/bin/plutil','-convert','json','-o','-','--','-'],input=output.stdout)
                raw=json.loads(converted.stdout)
            except (ValueError,plistlib.InvalidFileException) as exc: raise BridgeError('backend_failure','simctl did not return app metadata') from exc
            apps=[{'bundle_id':key,'name':value.get('CFBundleDisplayName',value.get('CFBundleName',key)),'version':value.get('CFBundleShortVersionString',value.get('CFBundleVersion')),'system':value.get('ApplicationType')=='System','bundle_path':value.get('Path'),'supported_actions':['launch','terminate','container_export']+([] if value.get('ApplicationType')=='System' else ['uninstall'])} for key,value in raw.items()]
        else:
            flags=['--include-all-apps'] if args.get('include_system') else []
            raw=await self._dev(['device','info','apps',*flags],device)
            apps=[{'bundle_id':value.get('bundleIdentifier',value.get('bundleID')),'name':value.get('name',value.get('bundleName','unknown')),'version':value.get('version'),'system':value.get('isSystemApp',False),'bundle_path':value.get('url'),'supported_actions':['launch','terminate']+(['container_export'] if value.get('containerAccess',False) else [])} for value in raw.get('apps',[])]
        if not args.get('include_system'): apps=[app for app in apps if not app['system']]
        if args.get('query'): apps=[app for app in apps if args['query'].casefold() in (app['bundle_id']+' '+app['name']).casefold()]
        return apps
    async def invoke(self, tool, device, args, context):
        if CATALOG[tool]['mutation'] and not CATALOG[tool]['capability'].startswith('input.'):
            # Lifecycle/settings mutations must observe fresh discovery state.
            self.discovery_cache.clear()
        if tool=='start_simulator':
            created=False
            if 'simulator_id' in args: native=args['simulator_id'].removeprefix('sim:')
            elif args.get('device_type_id') and args.get('runtime_id'):
                native=(await self._sim('create',args.get('name','TapMesh Simulator'),args['device_type_id'],args['runtime_id'],mutation=True)).stdout.decode().strip(); created=True
            else: raise BridgeError('invalid_arguments','Explicit simulator_id or creation fields required')
            found=next((d for d in await self.discover(True) if d['device_id']=='sim:'+native),None)
            if not found: raise BridgeError('device_not_found','The requested simulator ID is not present')
            if context.get('bind_simulator'): await context['bind_simulator'](found)
            if not found['ready']: await self._sim('boot',native,mutation=True)
            await self._sim('bootstatus',native,'-b',timeout=args.get('timeout_ms',180000)/1000)
            return {'device_id':'sim:'+native,'created':created,'ready':True,'completion_condition':'Simulator bootstatus completed'}
        if tool=='stop_recording': return await self._record(tool,device,args,context)
        if not device['ready']: raise BridgeError('device_disconnected','Selected device is not ready')
        native=device['native_id']; sim=device['backend']=='simulator'; router=context['router']
        if tool in {'take_screenshot','save_screenshot'}: return await self._screenshot(tool,device,args,context)
        if tool=='shut_down_simulator':
            if not sim: raise BridgeError('unsupported_capability','Physical shutdown is not this simulator operation')
            active=[r for r in self.recordings.values() if r['device_id']==device['device_id'] and not r.get('final')]
            if active: raise BridgeError('invalid_arguments','Stop active recording before shutting down the simulator')
            await self._sim('shutdown',native,mutation=True)
            found=next((d for d in await self.discover(True) if d['device_id']==device['device_id']),None)
            if found and found['ready']: raise BridgeError('delivery_outcome_unknown','Simulator has not confirmed shutdown',side_effect_state='unknown')
            companion=self.companions.pop(native,None)
            if companion: await self.runner.terminate(companion[0])
            return {'ready':False,'delivery':'confirmed','completion_condition':'Simulator no longer booted'}
        if tool=='list_elements':
            if args.get('source')=='ocr': raise BridgeError('unsupported_capability','OCR is not configured; accessibility results are never relabeled as OCR')
            if not sim:
                # The public AX audit walks focus and can scroll the app. A
                # read-only inspector must not use that traversal. The router
                # can fall back to non-mutating NAS OCR for source=auto.
                raise BridgeError('unsupported_capability','Physical AX traversal can move inspector focus; use source auto or OCR for a non-mutating screen observation')
            raw=await self._idb(device,'ui','describe-all',json_output=True)
            geometry=await self.geometry(device)
            elements=normalize_ax(raw,args.get('include_invisible',False),geometry['logical_size'])
            if not elements or all(e['bounds']['width']==0 for e in elements): raise BridgeError('prerequisite_required','idb accessibility bridge did not expose a valid tree')
            for element in elements: element.pop('_pid',None)
            return {**geometry,'source':'accessibility','elements':elements,'backend_truncated':bool(raw.get('truncated')) if isinstance(raw,dict) else False}
        if tool=='get_foreground_app':
            if not sim: raise BridgeError('unsupported_capability','Physical foreground app inspection requires a qualified runner')
            raw=await self._idb(device,'ui','describe-all',json_output=True); elements=normalize_ax(raw,True)
            root=next((e for e in elements if e['role'] in {'AXApplication','Application'}),{})
            pid=root.get('_pid'); apps=await self._idb(device,'list-apps',json_output=True)
            if isinstance(apps,dict): apps=[apps]
            matches=[a for a in apps if pid and str(a.get('pid'))==str(pid)]
            if len(matches)==1: return {'bundle_id':matches[0]['bundle_id'],'name':matches[0]['name'],'source':'accessibility_pid+idb_app_metadata','observed_at':utcnow(),'freshness':{'state':'fresh'}}
            return {'state':'unknown','bundle_id':None,'reason':'AX foreground PID is not exposed by app metadata','source':'accessibility','observed_at':utcnow(),'freshness':{'state':'fresh'}}
        if tool=='list_apps':
            apps=await self._apps(device,args)
            return {'apps':[{k:v for k,v in app.items() if k!='bundle_path'} for app in apps[:1000]],'truncated':len(apps)>1000,'observed_at':utcnow(),'source':'simctl' if sim else 'devicectl','freshness':{'state':'fresh'}}
        if tool=='launch_app':
            environment=args.get('environment',{})
            if sim:
                result=await self._sim('launch',native,args['bundle_id'],*args.get('arguments',[]),env={'SIMCTL_CHILD_'+k:v for k,v in environment.items()},mutation=True)
                text=result.stdout.decode(); pid=int(text.rsplit(':',1)[-1].strip()) if re.search(r':\s*\d+\s*$',text) else None
            else:
                flags=['--environment-variables',json.dumps(environment)] if environment else []
                result=await self._dev(['device','process','launch',*flags,args['bundle_id'],'--',*args.get('arguments',[])],device,mutation=True)
                pid=result.get('process',{}).get('processIdentifier')
            if sim: await self._reset_companion(device)
            return {'bundle_id':args['bundle_id'],'process_id':pid,'delivery':'confirmed' if pid else 'sent','verified':bool(pid),'completion_condition':'Process identifier returned by backend' if pid else 'Launch request accepted','foreground_verified':False}
        if tool=='terminate_app':
            if sim:
                result=await self._sim('terminate',native,args['bundle_id'],mutation=True,check=False)
                if result.returncode:
                    if b'not running' not in result.stderr.lower() and b'found nothing' not in result.stderr.lower(): raise BridgeError('delivery_outcome_unknown','Termination was not confirmed',side_effect_state='unknown')
                    return {'bundle_id':args['bundle_id'],'state':'already_stopped','delivery':'not_sent','completion_condition':'Backend reports app is not running'}
            else:
                apps=await self._apps(device,{'include_system':True}); app=next((a for a in apps if a['bundle_id']==args['bundle_id']),None)
                if not app: raise BridgeError('invalid_arguments','Bundle is not installed')
                processes=(await self._dev(['device','info','processes'],device)).get('runningProcesses',[])
                matches=[p for p in processes if p.get('executable') and app.get('bundle_path') and str(p['executable']).startswith(str(app['bundle_path']).rstrip('/')+'/')]
                if not matches: return {'state':'already_stopped','delivery':'not_sent','completion_condition':'No running process in the named bundle'}
                if len(matches)!=1: raise BridgeError('unsupported_capability','Bundle exposes multiple processes; a safe main-process mapping is unavailable')
                await self._dev(['device','process','terminate','--pid',str(matches[0]['processIdentifier'])],device,mutation=True)
            if sim: await self._reset_companion(device)
            return {'bundle_id':args['bundle_id'],'delivery':'confirmed','completion_condition':'Backend confirmed termination','verified':True}
        if tool=='install_app': return await self._install(device,args,context)
        if tool=='uninstall_app':
            apps=await self._apps(device,{'include_system':True}); app=next((a for a in apps if a['bundle_id']==args['bundle_id']),None)
            if not app: return {'state':'already_absent','bundle_id':args['bundle_id'],'delivery':'not_sent','completion_condition':'Bundle is absent'}
            if app['system']: raise BridgeError('unsupported_capability','System app removal is not supported by this adapter')
            if sim: await self._sim('uninstall',native,args['bundle_id'],mutation=True)
            else: await self._dev(['device','uninstall','app',args['bundle_id']],device,mutation=True)
            if any(a['bundle_id']==args['bundle_id'] for a in await self._apps(device,{'include_system':True})): raise BridgeError('delivery_outcome_unknown','App absence was not confirmed',side_effect_state='unknown')
            return {'bundle_id':args['bundle_id'],'delivery':'confirmed','data_removed':True,'completion_condition':'App absence observed after uninstall','verified':True}
        if tool in {'set_appearance','set_accessibility'}:
            if tool=='set_appearance':
                if args['appearance']=='system': raise BridgeError('unsupported_capability','Public backend supports explicit light or dark; system mode is unavailable')
                if sim:
                    await self._sim('ui',native,'appearance',args['appearance'],mutation=True)
                    effective=(await self._sim('ui',native,'appearance')).stdout.decode().strip()
                    if effective!=args['appearance']: raise BridgeError('delivery_outcome_unknown','Requested appearance was not observed',side_effect_state='unknown')
                    return {'appearance':effective,'delivery':'confirmed','verified':True,'completion_condition':'Effective appearance read back'}
                await self._dev(['device','settings','appearance','--mode',args['appearance']],device,mutation=True)
                effective=await self._dev(['device','info','appearance'],device)
                return {'requested':args['appearance'],'effective':effective,'delivery':'sent','verified':False,'completion_condition':'Appearance request accepted; effective raw public settings included'}
            fields=set(args)-{'device_id','timeout_ms'}; supported={'content_size_category'} if sim else {'content_size_category','reduce_motion'}
            if not fields<=supported: raise BridgeError('unsupported_capability','Accessibility patch includes unsupported fields; no fields were changed',details={'supported_fields':sorted(supported)})
            if sim:
                await self._sim('ui',native,'content_size',args['content_size_category'],mutation=True)
                effective=(await self._sim('ui',native,'content_size')).stdout.decode().strip()
                if effective!=args['content_size_category']: raise BridgeError('delivery_outcome_unknown','Content size did not read back as requested',side_effect_state='unknown')
                return {'effective':{'content_size_category':effective},'delivery':'confirmed','verified':True,'completion_condition':'Content size read back'}
            flags=[]
            if 'content_size_category' in args:
                if args['content_size_category'].startswith('accessibility-'): flags+=['--larger-accessibility-sizes','on']
                flags+=['--text-size',args['content_size_category']]
            if 'reduce_motion' in args: flags+=['--reduce-motion','on' if args['reduce_motion'] else 'off']
            await self._dev(['device','settings','appearance',*flags],device,mutation=True)
            return {'requested':{k:args[k] for k in fields},'effective':await self._dev(['device','info','appearance'],device),'delivery':'sent','verified':False,'completion_condition':'Public settings patch accepted'}
        if tool in {'set_location','clear_location'}:
            if 'altitude' in args: raise BridgeError('unsupported_capability','Altitude is not supported by this public location route')
            if sim:
                await self._sim('location',native,*(['set',str(args['latitude'])+','+str(args['longitude'])] if tool=='set_location' else ['clear']),mutation=True)
            else:
                await self._dev(['device','simulate','location',*(['coordinate','--latitude',str(args['latitude']),'--longitude',str(args['longitude'])] if tool=='set_location' else ['clear'])],device,mutation=True)
            return {'delivery':'sent','verified':False,'completion_condition':'Location override request accepted','scope':'selected device','persistence':'until clear_location or backend reset' if tool=='set_location' else 'override clear requested'}
        if tool in {'get_clipboard','set_clipboard'}:
            if tool=='get_clipboard':
                if sim: data=(await self._sim('pbpaste',native)).stdout
                else:
                    output=await self.runner.run(['xcrun','devicectl','device','pasteboard','paste','--device',native,'--timeout','20','--quiet'],timeout=22)
                    data=output.stdout
                if len(data)>65536: raise BridgeError('backend_failure','Clipboard exceeds the 64 KiB text bound')
                return {'text':data.decode('utf-8'),'source':'device_clipboard','observed_at':utcnow(),'freshness':{'state':'fresh'},'propagation_warning':'Device clipboard may synchronize through Universal Clipboard'}
            if sim: await self._sim('pbcopy',native,input=args['text'].encode(),mutation=True)
            else: await self._dev(['device','pasteboard','copy'],device,input=args['text'].encode(),mutation=True)
            return {'delivery':'confirmed','completion_condition':'Backend accepted clipboard replacement','verified':False,'propagation_warning':'Device clipboard may synchronize through Universal Clipboard'}
        if tool in {'open_url','open_settings'}:
            if tool=='open_settings':
                if sim: await self._sim('launch',native,'com.apple.Preferences',mutation=True)
                else: await self._dev(['device','process','launch','com.apple.Preferences'],device,mutation=True)
                return {'delivery':'sent','section':'root','fallback':False,'verified':False,'completion_condition':'Settings launch requested'}
            if sim: await self._sim('openurl',native,args['url'],mutation=True)
            else:
                scheme=urlsplit(args['url']).scheme.lower()
                if scheme not in {'http','https'}: raise BridgeError('unsupported_capability','Physical deep-link handling is not qualified; only web URLs supported')
                await self._dev(['device','process','launch','--payload-url',args['url'],'com.apple.mobilesafari'],device,mutation=True)
            return {'delivery':'sent','verified':False,'completion_condition':'Device URL open request accepted'}
        if tool=='rotate':
            if sim:
                await self._idb(device,'rotation',args['orientation'].upper(),mutation=True)
                effective=(await self._idb(device,'rotation',json_output=True)).get('orientation','UNKNOWN').lower()
            else:
                value={'portrait':'portrait','portrait_upside_down':'portraitUpsideDown','landscape_left':'landscapeLeft','landscape_right':'landscapeRight'}[args['orientation']]
                await self._dev(['device','orientation','set',value],device,mutation=True)
                effective=await self._dev(['device','orientation','get'],device)
            return {'requested':args['orientation'],'effective':effective,'delivery':'confirmed' if effective==args['orientation'] else 'sent','verified':effective==args['orientation'],'completion_condition':'Physical orientation requested; application may enforce orientation lock'}
        if tool in {'start_recording','stop_recording'}: return await self._record(tool,device,args,context)
        if tool in {'list_crashes','get_crash','download_app_container'}: return await self._diagnostics(tool,device,args,context)
        if tool=='build_and_run': return await self._build(device,args,context)
        if not sim: return await self._native_input(tool,device,args,context)
        return await self._input(tool,device,args,context)
    async def _input(self, tool, device, args, context):
        if tool=='fill' and args.get('frame_validation','exact')!='exact':
            context['sent']=False
            raise BridgeError('unsupported_capability','Text-target frame validation is a physical-device mode; no simulator input was sent')
        if tool in {'type_text','fill'} and args.get('input_method','keyboard')=='clipboard':
            context['sent']=False
            raise BridgeError('unsupported_capability','Explicit simulator clipboard-paste input is not qualified; no input was sent')
        targets=context.get('targets',{})
        async def command(*cmd): return await self._idb(device,*cmd,mutation=True)
        def point(name): return tuple(str(round(v)) for v in targets[name][:2])
        if tool=='tap': await command('ui','tap',*point('target'))
        elif tool=='double_tap': await command('ui','multi-tap',*point('target'),'--count','2','--pause',str(args.get('interval_ms',120)/1000))
        elif tool=='long_press': await command('ui','tap',*point('target'),'--duration',str(args.get('duration_ms',800)/1000))
        elif tool=='swipe': await command('ui','swipe',*point('start'),*point('end'),'--duration',str(args.get('duration_ms',300)/1000))
        elif tool=='drag': await command('ui','drag-and-drop',*point('start'),*point('end'),'--press-duration',str(args.get('hold_ms',300)/1000),'--duration',str(args.get('duration_ms',700)/1000))
        elif tool=='pinch': await command('ui','pinch',*point('center'),str(args['scale']),'--duration',str(args.get('duration_ms',500)/1000))
        elif tool=='type_text':
            from idb.common.hid import KEY_MAP
            if any(character not in KEY_MAP for character in args['text']): raise BridgeError('unsupported_capability','Text contains characters outside the qualified HID key map; AX fill supports Unicode replacement')
            if 'target' in targets: await command('ui','tap',*point('target'))
            await command('ui','text',args['text'])
        elif tool=='fill':
            # AX set-value is a direct replacement, not keyboard clear emulation.
            await command('ui','set-value',*point('target'),'--value',args['text'])
            if args.get('submit'): await command('ui','key','40')
            raw=normalize_ax(await self._idb(device,'ui','describe-all',json_output=True))
            x,y=targets['target'][:2]; matching=[e for e in raw if e['bounds']['x']<=x<=e['bounds']['x']+e['bounds']['width'] and e['bounds']['y']<=y<=e['bounds']['y']+e['bounds']['height'] and e.get('value')==args['text']]
            return {'delivery':'confirmed','verified':bool(matching),'completion_condition':'AX value replacement accepted','value_verified':bool(matching),'submitted':args.get('submit',False)}
        elif tool=='press_key':
            keys={'return':40,'delete':42,'escape':41,'tab':43,'space':44,'right':79,'left':80,'down':81,'up':82,'select_all':4}
            modifiers=args.get('modifiers',[])
            if args['key']=='select_all': modifiers=[*modifiers,'command']
            flags=['--'+{'alt':'option'}.get(m,m) for m in dict.fromkeys(modifiers)]
            await command('ui','key',str(keys[args['key']]),*flags)
        elif tool=='hide_keyboard':
            await command('ui','key','41')
            return {'delivery':'sent','keyboard_state':'unverifiable','verified':False,'completion_condition':'Semantic Escape dismissal delivered'}
        elif tool=='press_button':
            mapping={'home':'HOME','lock':'LOCK','side_button':'SIDE_BUTTON','siri':'SIRI','volume_up':'VOLUME_UP','volume_down':'VOLUME_DOWN'}
            if args['button'] not in mapping: raise BridgeError('unsupported_capability','This hardware button is not supported')
            await command('ui','button',mapping[args['button']])
        elif tool=='scroll':
            geometry=await self.geometry(device); w,h=geometry['logical_size'].values()
            if args.get('container_ref'):
                _,_,record=context['router'].observations.resolve(context['session_id'],device['device_id'],device['connection_epoch'],{'ref':args['container_ref'],'snapshot_id':args['snapshot_id']})
                box=next(e['bounds'] for e in record['elements'] if e['ref']==args['container_ref'])
                left,top,width,height=box['x'],box['y'],box['width'],box['height']
            else: left,top,width,height=0,0,w,h
            distance=min(args['distance_points'],(height if args['direction'] in {'up','down'} else width)*.7)
            if args['distance_points']>distance+.01: raise BridgeError('invalid_arguments','Scroll distance exceeds the safe viewport span; use bounded repeated steps')
            x,y=left+width/2,top+height/2
            dx,dy={'up':(0,distance),'down':(0,-distance),'left':(distance,0),'right':(-distance,0)}[args['direction']]
            await command('ui','swipe',str(round(x-dx/2)),str(round(y-dy/2)),str(round(x+dx/2)),str(round(y+dy/2)),'--duration','.3')
        elif tool=='system_gesture':
            gesture=args['gesture']
            if gesture=='home': await command('ui','button','HOME')
            elif gesture=='app_switcher':
                await command('ui','button','HOME'); await asyncio.sleep(.08); await command('ui','button','HOME')
            else:
                geometry=await self.geometry(device); w,h=geometry['logical_size'].values()
                x=w*.9 if gesture=='control_center' else w*.5
                await command('ui','swipe',str(round(x)),'1',str(round(x)),str(round(h*.65)),'--duration','.4')
        else: raise BridgeError('unsupported_capability','No qualified route for this operation')
        return {'delivery':'confirmed','verified':False,'completion_condition':'Backend confirmed input event delivery; application outcome is unverified'}
    async def _install(self, device, args, context):
        path=context.get('install_grants',context['router'].file_grants).check(args['artifact_path']); sim=device['backend']=='simulator'
        info=None
        if path.suffix=='.app' and path.is_dir():
            try: info=plistlib.loads((path/'Info.plist').read_bytes())
            except (OSError,plistlib.InvalidFileException) as exc: raise BridgeError('invalid_arguments','App bundle has no valid Info.plist') from exc
        elif path.suffix=='.ipa' and not sim and path.is_file():
            with zipfile.ZipFile(path) as archive:
                candidates=[n for n in archive.namelist() if re.fullmatch(r'Payload/[^/]+\.app/Info.plist',n)]
                if len(candidates)!=1: raise BridgeError('invalid_arguments','IPA must contain exactly one main app bundle')
                if archive.getinfo(candidates[0]).file_size>1024*1024: raise BridgeError('invalid_arguments','App manifest exceeds bound')
                info=plistlib.loads(archive.read(candidates[0]))
        else: raise BridgeError('invalid_arguments','Simulator installs require .app; physical installs require .app or .ipa')
        platforms=info.get('CFBundleSupportedPlatforms',[])
        expected='iPhoneSimulator' if sim else 'iPhoneOS'
        if expected not in platforms: raise BridgeError('invalid_arguments','App platform does not match selected device')
        bundle=info.get('CFBundleIdentifier')
        if not bundle or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._-]+',bundle): raise BridgeError('invalid_arguments','Invalid app bundle identifier')
        existing=next((a for a in await self._apps(device,{'include_system':True}) if a['bundle_id']==bundle),None)
        if existing and not args.get('replace'): raise BridgeError('artifact_conflict','App is already installed; replacement requires explicit destructive approval')
        try:
            if sim: await self._sim('install',device['native_id'],str(path),mutation=True,timeout=120)
            else: await self._dev(['device','install','app',str(path)],device,mutation=True,timeout=120)
        except BridgeError as exc: raise BridgeError('install_failed','Installation did not confirm completion',side_effect_state=exc.side_effect_state) from exc
        installed=next((a for a in await self._apps(device,{'include_system':True}) if a['bundle_id']==bundle),None)
        if not installed: raise BridgeError('delivery_outcome_unknown','Installed app metadata could not be verified',side_effect_state='unknown')
        return {'bundle_id':bundle,'version':info.get('CFBundleShortVersionString',info.get('CFBundleVersion')),'replaced':bool(existing),'delivery':'confirmed','verified':True,'completion_condition':'Installed bundle observed in app catalog'}
    async def _record(self, tool, device, args, context):
        session=context['session_id']; did=device['device_id']
        if tool=='stop_recording':
            recording=self.recordings.get(args['recording_id'])
            if not recording or recording['session_id']!=session or recording['device_id']!=did: raise BridgeError('invalid_arguments','Recording is not owned by this session and device')
            return await self._stop_recording(recording)
        context['sent']=False
        if device['backend']=='physical':
            capability=await self._physical_recording_capability(device,refresh=True)
            if capability['status']!='supported':
                raise BridgeError('unsupported_capability' if capability['status']=='unsupported' else 'prerequisite_required',capability['reason'])
        if any(r['device_id']==did and not r.get('final') for r in self.recordings.values()): raise BridgeError('artifact_conflict','This device already has an active recording')
        store=context['router'].artifacts; store.check_storage(256*1024*1024)
        path=store.new_path('.mp4'); maximum=args.get('max_duration_seconds',600)
        argv=['xcrun','simctl','io',device['native_id'],'recordVideo','--codec=h264',str(path)] if device['backend']=='simulator' else ['xcrun','devicectl','device','capture','screen-record','--device',device['native_id'],'--destination',str(path),'--duration',str(maximum),'--codec','h264']
        context['sent']=True
        proc=await asyncio.create_subprocess_exec(*argv,stdout=asyncio.subprocess.DEVNULL,stderr=asyncio.subprocess.PIPE,stdin=asyncio.subprocess.DEVNULL,start_new_session=True,env=minimal_environment())
        self.runner.own(proc)
        rid=uuid.uuid4().hex
        recording={'recording_id':rid,'device_id':did,'epoch':device['connection_epoch'],'session_id':session,'process':proc,'path':path,'started':time.monotonic(),'store':store,'args':args,'lock':asyncio.Lock(),'maximum':maximum,'errors':[],'gaps':[]}
        self.recordings[rid]=recording
        async def drain():
            while await proc.stderr.read(65536): pass
        try:
            ack=False
            async with asyncio.timeout(15):
                while line:=await proc.stderr.readline():
                    if b'recording started' in line.lower() or b'recording screen' in line.lower(): ack=True; break
            if not ack: raise BridgeError('backend_failure','Recording writer startup was not confirmed',side_effect_state='unknown')
        except BaseException:
            await self.runner.terminate(proc,signal.SIGINT); self.recordings.pop(rid,None); path.unlink(missing_ok=True); raise
        recording['drain']=asyncio.create_task(drain())
        async def auto_stop():
            try:
                while time.monotonic()-recording['started']<maximum:
                    await asyncio.sleep(min(1,maximum-(time.monotonic()-recording['started'])))
                    current=next((d for d in await self.discover(True) if d['device_id']==did),None)
                    if not current or not current['ready'] or current['connection_epoch']!=recording['epoch']:
                        recording['errors'].append('connection_changed_recording_stopped')
                        recording['gaps'].append({'at_elapsed_seconds':time.monotonic()-recording['started'],'reason':'connection_changed','resumed':False})
                        break
                await self._stop_recording(recording)
            except Exception: recording['errors'].append('automatic_finalization_failed')
        recording['timer']=asyncio.create_task(auto_stop())
        return {'recording_id':rid,'state':'recording','max_duration_seconds':maximum,'started_at':utcnow(),'media':'video_only','resume_after_reconnect':False,'completion_condition':'Backend acknowledged recording writer start'}
    async def _stop_recording(self, recording):
        async with recording['lock']:
            if recording.get('final'): return recording['final']
            timer=recording.get('timer')
            if timer and timer is not asyncio.current_task(): timer.cancel()
            proc=recording['process']; await self.runner.terminate(proc,signal.SIGINT)
            if recording.get('drain'): await recording['drain']
            elapsed=time.monotonic()-recording['started']; path=recording['path']; errors=recording['errors']; artifacts=[]
            if proc.returncode not in {0,130,-signal.SIGINT}: errors.append('recording_backend_exited_abnormally')
            if path.exists() and path.stat().st_size:
                try:
                    if helper:=os.environ.get('TAPMESH_NATIVE_TOOLS'):
                        probe=await self.runner.run([helper,'probe',str(path)],timeout=15)
                        media=json.loads(probe.stdout); duration=float(media['duration_seconds'])
                        if not isinstance(media.get('video_tracks'),int) or media['video_tracks']<1: raise ValueError('Missing video track')
                    else:
                        probe=await self.runner.run([os.environ.get('TAPMESH_FFPROBE',shutil.which('ffprobe') or 'ffprobe'),'-v','error','-show_entries','format=duration','-of','json',str(path)],timeout=15)
                        duration=float(json.loads(probe.stdout)['format']['duration'])
                    if not math.isfinite(duration) or duration<=0: raise ValueError('Invalid media duration')
                    artifact=recording['store'].finalize(path,'recording',recording['device_id'],recording['epoch'],destination=recording['args'].get('destination'),filename=recording['args'].get('filename'),state='partial' if errors else 'complete'); artifact['duration_seconds']=duration; artifacts.append(artifact)
                except (BridgeError,ValueError,KeyError): errors.append('recording_media_not_finalized_or_probe_failed')
            else: errors.append('recording_output_missing')
            path.unlink(missing_ok=True)
            result={'recording_id':recording['recording_id'],'state':'partial' if errors else 'complete','segments':artifacts,'gaps':recording.get('gaps',[]),'elapsed_seconds':elapsed,'errors':errors,'status':'partial' if errors else 'success','completion_condition':'Owned writer stopped; only probed finalized media is returned'}
            recording['final']=result
            return result
    async def _diagnostics(self, tool, device, args, context):
        sim=device['backend']=='simulator'; store=context['router'].artifacts; native=device['native_id']
        if tool=='list_crashes':
            if sim:
                raw=await self._idb(device,'crash','list',json_output=True)
                if isinstance(raw,dict): raw=[raw]
                reports=[]
                for item in raw:
                    filename=item.get('name') or item.get('crash_name'); bundle=item.get('bundle_id',item.get('bundle_identifier'))
                    if not filename: continue
                    cid=uuid.uuid5(uuid.NAMESPACE_URL,device['device_id']+'/'+filename).hex
                    self.crashes[cid]={'device_id':device['device_id'],'native_name':filename,'metadata':item}
                    timestamp=item.get('timestamp')
                    if args.get('bundle_id') and bundle!=args['bundle_id']: continue
                    reports.append({'crash_id':cid,'bundle_id':bundle,'timestamp':timestamp,'name':filename})
            else:
                raw=await self._dev(['device','info','files','--domain-type','systemCrashLogs','--no-recurse'],device)
                reports=[]
                for item in raw.get('files',[]):
                    filename=item.get('name') or item.get('path')
                    if not filename or Path(filename).name!=filename or not filename.endswith(('.ips','.crash')): continue
                    if args.get('bundle_id'): continue # Never pretend filenames establish bundle identity.
                    cid=uuid.uuid5(uuid.NAMESPACE_URL,device['device_id']+'/'+filename).hex
                    self.crashes[cid]={'device_id':device['device_id'],'native_name':filename,'metadata':item}; reports.append({'crash_id':cid,'name':filename,'timestamp':item.get('modificationDate'),'bundle_id':None})
            if args.get('since'):
                def recent(report):
                    value=report.get('timestamp')
                    if isinstance(value,(int,float)): return datetime.fromtimestamp(value,timezone.utc)>=datetime.fromisoformat(args['since'].replace('Z','+00:00'))
                    if isinstance(value,str):
                        try: return datetime.fromisoformat(value.replace('Z','+00:00'))>=datetime.fromisoformat(args['since'].replace('Z','+00:00'))
                        except ValueError: return False
                    return False
                reports=[r for r in reports if recent(r)]
            reports.sort(key=lambda r:r['crash_id']); cursor=args.get('cursor')
            if cursor and cursor not in {r['crash_id'] for r in reports}: raise BridgeError('invalid_arguments','Crash pagination cursor is no longer valid')
            offset=next((i+1 for i,r in enumerate(reports) if r['crash_id']==cursor),0); page=reports[offset:offset+args.get('limit',50)]
            return {'reports':page,'next_cursor':page[-1]['crash_id'] if page and offset+len(page)<len(reports) else None,'observed_at':utcnow(),'source':'idb' if sim else 'devicectl','freshness':{'state':'fresh'}}
        if tool=='get_crash':
            record=self.crashes.get(args['crash_id'])
            if not record or record['device_id']!=device['device_id']: raise BridgeError('invalid_arguments','Crash ID is unknown for this device; list crashes again')
            if args.get('symbols_path'): context['router'].file_grants.check(args['symbols_path'])
            if sim:
                output=await self._idb(device,'crash','show',record['native_name']); content=output.encode()
                artifact=store.write(content,'crash','.ips',device['device_id'],device['connection_epoch'],destination=args.get('destination'))
            else:
                temporary=store.new_path('.ips')
                try:
                    await self._dev(['device','copy','from','--domain-type','systemCrashLogs','--source',record['native_name'],'--destination',str(temporary)],device)
                    artifact=store.finalize(temporary,'crash',device['device_id'],device['connection_epoch'],destination=args.get('destination'))
                finally: temporary.unlink(missing_ok=True)
            return {'artifact':artifact,'summary':{'name':record['native_name']},'symbolication':{'state':'unsupported' if args.get('symbolicate') else 'not_requested','reason':'Matching UUID symbolication is not implemented; raw report preserved'},'status':'partial' if args.get('symbolicate') else 'success'}
        if tool=='download_app_container':
            store.check_storage(256*1024*1024)
            with tempfile.TemporaryDirectory(prefix='tapmesh-container-',dir=self.state) as directory:
                if sim:
                    source=Path((await self._sim('get_app_container',native,args['bundle_id'],'data')).stdout.decode().strip())
                    if not source.is_dir(): raise BridgeError('backend_failure','App data container is inaccessible')
                else:
                    source=Path(directory)/'data'
                    await self._dev(['device','copy','from','--domain-type','appDataContainer','--domain-identifier',args['bundle_id'],'--source','.','--destination',str(source)],device,timeout=120)
                archive=store.new_path('.zip'); count=0; total=0; manifest=[]
                try:
                    with zipfile.ZipFile(archive,'x',compression=zipfile.ZIP_DEFLATED) as output:
                        for path in source.rglob('*'):
                            if path.is_symlink(): continue
                            if path.is_file():
                                count+=1; total+=path.stat().st_size
                                if count>25000 or total>1024**3: raise BridgeError('insufficient_storage','App container exceeds bounded export limits')
                                relative=str(path.relative_to(source)); output.write(path,'data/'+relative); manifest.append({'path':relative,'size_bytes':path.stat().st_size})
                        output.writestr('tapmesh-manifest.json',json.dumps({'bundle_id':args['bundle_id'],'files':manifest,'limitations':['symlinks skipped','only accessible development-app data','app not frozen; files may change during capture']}))
                    artifact=store.finalize(archive,'app_container',device['device_id'],device['connection_epoch'],destination=args.get('destination'),filename=args.get('filename'))
                finally: archive.unlink(missing_ok=True)
            return {'artifact':artifact,'file_count':count,'uncompressed_bytes':total,'limitations':['Accessible app data only','App files may change during export','Symlinks skipped']}
        raise BridgeError('unsupported_capability','Diagnostic operation has no route')
    async def _build(self, device, args, context):
        project=context['router'].build_grants.check(args['project_path'])
        if project.suffix not in {'.xcodeproj','.xcworkspace'} or not project.is_dir(): raise BridgeError('invalid_arguments','project_path must be an approved .xcodeproj or .xcworkspace')
        derived=self.state/'builds'/uuid.uuid4().hex; derived.mkdir(parents=True,mode=0o700)
        configuration=args.get('configuration','Debug'); sim=device['backend']=='simulator'; stages=[]
        output=await self.runner.run(['xcodebuild','-workspace' if project.suffix=='.xcworkspace' else '-project',str(project),'-scheme',args['scheme'],'-configuration',configuration,'-destination','id='+device['native_id'],'-derivedDataPath',str(derived),'build'],timeout=args.get('timeout_ms',1800000)/1000,mutation=True,check=False)
        # Build logs can contain source paths and command content. Persist only in
        # the specifically approved artifact; no log text enters HTTP history.
        log=context['router'].artifacts.write((output.stdout+output.stderr)[-2*1024*1024:],'build_log','.log',device['device_id'],device['connection_epoch'])
        stages.append({'stage':'build','status':'success' if output.returncode==0 else 'failed','log_artifact':log})
        if output.returncode: raise BridgeError('build_failed','Xcode build failed',side_effect_state='unknown',details={'stages':stages})
        apps=list((derived/'Build'/'Products'/(configuration+('-iphonesimulator' if sim else '-iphoneos'))).glob('*.app'))
        if len(apps)!=1: raise BridgeError('build_failed','Build must resolve exactly one main app product',details={'stages':stages})
        # Derived products are owned by this approved build operation; their
        # transient install grant cannot be supplied through the public API.
        from .safety import PathGrants
        install_context={**context,'install_grants':PathGrants([derived])}
        try: installed=await self._install(device,{'artifact_path':str(apps[0]),'replace':False},install_context)
        except BridgeError as exc:
            raise BridgeError(exc.code,exc.message,side_effect_state=exc.side_effect_state,details={'stages':stages,'built_app_path':str(apps[0]),'log_artifact':log}) from exc
        stages.append({'stage':'install','status':'success','result':installed})
        if args.get('launch',True): stages.append({'stage':'launch','status':'success','result':await self.invoke('launch_app',device,{'bundle_id':installed['bundle_id']},context)})
        return {'stages':stages,'bundle_id':installed['bundle_id'],'log_artifact':log,'delivery':'confirmed','completion_condition':'Build and install verified; launch result separately reported'}
