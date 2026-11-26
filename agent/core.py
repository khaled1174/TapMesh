"""Policy and workflow coordinator. Every UI and MCP call crosses this router."""
import asyncio
import hmac
import json
import logging
import os
import re
import stat
import tempfile
import traceback
from copy import deepcopy
from contextlib import asynccontextmanager, suppress
from pathlib import Path
import time
from urllib.parse import urlsplit
import uuid
import weakref
from Contracts.catalog import CATALOG, BATCH_ALLOWED, validate, ContractError
from .safety import Approvals, BridgeError, ControlAccess, GRANT_SESSION, GRANT_TOOL, Observations, utcnow
from .artifacts import Artifacts


async def settle_cleanup(task):
    """Keep ownership until cleanup settles, including repeated cancellation."""
    cancelled=False
    while True:
        try:
            result=await asyncio.shield(task)
            break
        except asyncio.CancelledError:
            if task.done(): return await task
            cancelled=True
    if cancelled: raise asyncio.CancelledError
    return result


class _HeldDeviceStore:
    """Preserves Stop while the same supervisor replaces the agent process."""
    def __init__(self, path, instance, secret):
        self.path, self.instance, self.key = path, instance, secret.encode()

    def owner_proof(self, session):
        material=json.dumps([self.instance,session],separators=(',',':')).encode()
        return hmac.digest(self.key,material,'sha256').hex()

    def load(self):
        try:
            fd=os.open(self.path,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK)
            with os.fdopen(fd,'rb') as source:
                info=os.fstat(fd)
                if not stat.S_ISREG(info.st_mode) or info.st_uid!=os.getuid() or info.st_mode&0o077 or info.st_nlink!=1:
                    raise ValueError('Invalid ownership or permissions')
                raw=source.read(1024*1024+1)
            if len(raw)>1024*1024: raise ValueError('Hold record exceeds limit')
            data=json.loads(raw)
            if not isinstance(data,dict) or not isinstance(data.get('instance_id'),str): raise ValueError('Invalid hold record')
            if data['instance_id']!=self.instance:
                self.path.unlink(missing_ok=True)
                return set(),None
            proof,devices=data.get('owner_proof'),data.get('device_ids')
            if (not isinstance(proof,str) or not re.fullmatch('[0-9a-f]{64}',proof) or not isinstance(devices,list)
                or any(not isinstance(did,str) or not 1<=len(did)<=256 for did in devices)):
                raise ValueError('Invalid hold record')
            return set(devices),proof
        except FileNotFoundError:
            return set(),None
        except (OSError,ValueError) as exc:
            raise BridgeError('backend_failure','TapMesh could not load paused device controls') from exc

    def save(self, devices, proof):
        temporary=None
        try:
            if not devices:
                self.path.unlink(missing_ok=True)
                return
            data=json.dumps({'instance_id':self.instance,'owner_proof':proof,'device_ids':sorted(devices)})
            if len(data)>1024*1024:
                raise BridgeError('overloaded','TapMesh has too many paused devices')
            with tempfile.NamedTemporaryFile(mode='w',dir=self.path.parent,prefix='.device-holds-',delete=False) as output:
                temporary=Path(output.name)
                output.write(data)
                output.flush(); os.fsync(output.fileno())
            os.replace(temporary,self.path)
        except OSError as exc:
            raise BridgeError('backend_failure','TapMesh could not save paused device controls') from exc
        finally:
            if temporary is not None:
                with suppress(OSError): temporary.unlink(missing_ok=True)


class Router:
    def __init__(self, backend, *, state_dir, approval_ttl=120, export_roots=(), build_roots=(), fast_control=False, access=None, hold_scope=None):
        # The device agent service turns fast control on by default (agent/app.py);
        # a bare Router keeps every non-O action behind an approval.
        self.backend=backend; self.sessions={}; self.locks={}; self.approvals=Approvals(approval_ttl)
        self.access=ControlAccess(fast_control=fast_control) if access is None else access
        self.observations=Observations(); self.artifacts=Artifacts(Path(state_dir)/'artifacts',export_roots)
        from .safety import PathGrants
        self.build_grants=PathGrants(build_roots); self.file_grants=PathGrants([*export_roots,*build_roots])
        self.epochs={}
        self.session_seen={}; self.session_active={}; self.max_sessions=128; self.session_ttl=1800
        self.work={}; self.generations={}; self.takeovers=set(); self.max_device_requests=32
        self.hold_store=_HeldDeviceStore(Path(state_dir)/'device-holds.json',*hold_scope) if hold_scope else None
        self.holds,self.hold_owner=self.hold_store.load() if self.hold_store else (set(),None)
        self.agent_activity={}
        self.recording_locks={}; self.creation_lock=asyncio.Lock()
        self.update_deadline=0.0; self.update_lease=None; self.active_downloads=0
        self.live_views=weakref.WeakSet()
        from .diagnostics import MetadataDiagnostics
        self.diagnostics=MetadataDiagnostics(state_dir)
        from .nas_ocr import NASOCR
        self.ocr=NASOCR.configured()
    def end_live(self, reason):
        for view in list(self.live_views): view.close(reason)
    async def close(self):
        self.end_live('shutdown')
        try: await self.backend.close()
        finally:
            if self.ocr: await self.ocr.close()
    def prepare_update(self, lease_id):
        # No awaits between checking work and closing intake: requests admitted
        # before this turn are counted, and later requests cannot race the stop.
        recordings=getattr(self.backend,'recordings',{})
        if time.monotonic()<self.update_deadline and self.update_lease!=lease_id:
            raise BridgeError('overloaded','Another update owns the local preparation lease',retryable=True)
        if self.session_active or self.takeovers or self.active_downloads or any(not r.get('final') for r in recordings.values()):
            raise BridgeError('overloaded','An update is waiting for active device work or recordings to finish',retryable=True)
        self.update_deadline=time.monotonic()+60
        self.update_lease=lease_id
        # Live viewers are not device work: they end (and fall back to
        # screenshots) instead of holding the update.
        self.end_live('update')
        return {'status':'prepared','expires_in_seconds':60,'lease_id':lease_id}
    def resume_after_update(self, lease_id):
        if self.update_lease is not None and self.update_lease!=lease_id:
            raise BridgeError('ownership_changed','Only the update that prepared this runtime can resume it')
        self.update_deadline=0.0; self.update_lease=None
        return {'status':'resumed'}
    async def live(self, session, device_id):
        """A live video viewer, authorized exactly as take_screenshot: valid
        session, discovered device, screen.capture capability and the tool's
        policy (O: no approval, ownership lock or takeover). Viewing is
        read-only and records no history entry; it never sends input."""
        if time.monotonic()<self.update_deadline: raise BridgeError('overloaded','TapMesh is preparing an update; reconnect after it restarts',retryable=True)
        self._session(session)
        if not isinstance(device_id,str) or not 1<=len(device_id)<=256: raise BridgeError('invalid_arguments','device_id must contain 1–256 characters')
        device=await self.device(session,{'device_id':device_id})
        await self._preflight('take_screenshot',{},device)
        self.approvals.require(session,device['device_id'],device['connection_epoch'],'take_screenshot',{},policy=self.policy('take_screenshot',{}))
        if not device.get('ready') or not hasattr(self.backend,'live'): raise BridgeError('live_unavailable','This device has no live video stream; use screenshots')
        view=await self.backend.live(device)
        self.live_views.add(view)
        return view
    async def devices(self, all=False):
        # Discovery adapters can reuse mutable dictionaries. A request's bound
        # identity/epoch must survive later discoveries unchanged.
        devices=deepcopy(await self.backend.discover(all))
        ocr_ready=await self.ocr.available() if self.ocr else False
        for device in devices:
            if self.ocr:
                caps=device.setdefault('capabilities',{})
                can_capture=caps.get('screen.capture',{}).get('status') in {'supported','degraded'}
                caps['ui.ocr']={'status':'supported' if ocr_ready and can_capture else 'requires_user_action',
                                'reason':'Text boxes from the configured OCR processor; no native accessibility roles' if ocr_ready and can_capture else 'Restore the configured OCR processor and screen capture prerequisites'}
                if ocr_ready and can_capture and caps.get('ui.inspect',{}).get('status') in {'unsupported','unknown'}:
                    caps['ui.inspect']={'status':'degraded','reason':'Bounded OCR text targets available; accessibility semantics unavailable'}
            did=device['device_id']; epoch=device['connection_epoch']
            if did in self.epochs and self.epochs[did]!=epoch: self.observations.invalidate(did)
            self.epochs[did]=epoch
        return devices
    async def device(self, session, args):
        did=args.get('device_id') or self.sessions.get(session)
        if not did: raise BridgeError('device_not_selected','Select a device for this session or provide device_id')
        for device in await self.devices(True):
            if device['device_id']==did: return device
        raise BridgeError('device_not_found','The explicit device ID was not discovered')
    def capability(self, tool, device):
        if tool in {'wait_for','batch_commands','request_human_action'}: return
        cap=device.get('capabilities',{}).get(CATALOG[tool]['capability'],{'status':'unsupported','reason':'Backend does not provide this operation'})
        if cap['status'] in {'unsupported','unknown'}: raise BridgeError('unsupported_capability',cap.get('reason','Capability is unsupported'))
        if cap['status']=='requires_user_action': raise BridgeError('prerequisite_required',cap.get('reason','Complete backend prerequisites'))
    def policy(self, tool, args):
        policy=CATALOG[tool]['policy']
        if tool in {'type_text','fill'} and args.get('input_method')=='clipboard': policy='S'
        if tool=='open_url':
            url=args['url']; scheme=urlsplit(url).scheme.lower()
            if not scheme or scheme in {'file','javascript','data','shell','ssh','x-man-page'} or any(ord(c)<32 for c in url): raise BridgeError('invalid_arguments','URL scheme or contents are not permitted')
            if scheme not in {'http','https'}: policy='S'
        if tool=='install_app' and args.get('replace'): policy='D'
        if tool=='launch_app' and (args.get('arguments') or args.get('environment')): policy='S'
        if tool=='batch_commands':
            order={'O':0,'C':1,'S':2,'D':3,'H':4,'U':5}
            policy=max((self.policy(c['tool'],c['arguments']) for c in args['commands']),key=order.get)
        return policy
    def _session(self, session):
        if not isinstance(session,str) or not 1<=len(session)<=128: raise BridgeError('invalid_arguments','session_id must contain 1–128 characters')
        now=time.monotonic()
        for key,seen in list(self.session_seen.items()):
            if now-seen>self.session_ttl and not self.session_active.get(key):
                self.session_seen.pop(key,None); self.sessions.pop(key,None)
        if session not in self.session_seen and len(self.session_seen)>=self.max_sessions:
            raise BridgeError('overloaded','Session capacity reached; wait for idle sessions to expire',retryable=True)
        self.session_seen[session]=now
    def _check_owner_session(self, session, owner_authorized):
        if not owner_authorized and self.access.owner_session is not None and session==self.access.owner_session:
            raise BridgeError('local_authorization_required','This session must use the TapMesh owner credential')
    def _is_owner(self, session, owner_authorized):
        return owner_authorized and self.access.owner_session is not None and session==self.access.owner_session
    def _check_hold(self, device_id, session, owner_authorized):
        if device_id in self.holds and not self._is_owner(session,owner_authorized):
            raise BridgeError('device_held','The owner paused AI control of this device in TapMesh; wait until they resume it',retryable=True)
    def _require_approval(self, session, device, tool, arguments, *, policy, owner_authorized, consume=False):
        self._check_owner_session(session,owner_authorized)
        if policy=='C' and self.access.needs_device_grant(session,device):
            self.approvals.require(GRANT_SESSION,device['device_id'],0,GRANT_TOOL,{},policy='A')
        self.approvals.require(session,device['device_id'],device['connection_epoch'],tool,arguments,policy=policy,
                               consume=consume,immediate=policy=='C' and self.access.immediate(session,device))
    def _prune_activity(self, now):
        for did,action in list(self.agent_activity.items()):
            if not action['count'] and now-action['at']>=60: self.agent_activity.pop(did,None)
    def activity(self):
        now=time.monotonic(); self._prune_activity(now)
        devices=[]
        for did in sorted(self.holds | self.agent_activity.keys()):
            action=self.agent_activity.get(did)
            devices.append({'device_id':did,'active':bool(action and action['count']),
                            'seconds_since_action':now-action['at'] if action else None,
                            'tool':action['tool'] if action else None,'held':did in self.holds})
        return {'devices':devices}
    def release_holds(self):
        if self.holds: self._replace_holds(set(),None)
    def reconcile_hold_owner(self, previous_session):
        session=self.access.owner_session
        if self.hold_store and session is not None:
            if self.holds and self.hold_store.owner_proof(session)!=self.hold_owner: self.release_holds()
        elif previous_session!=session:
            self.release_holds()
    def _replace_holds(self, devices, owner_proof):
        if self.hold_store: self.hold_store.save(devices,owner_proof)
        self.holds,self.hold_owner=devices,owner_proof if devices else None
    async def hold(self, device_id, held):
        if not isinstance(device_id,str) or not 1<=len(device_id)<=256:
            raise BridgeError('invalid_arguments','device_id must contain 1–256 characters')
        if not isinstance(held,bool): raise BridgeError('invalid_arguments','held must be true or false')
        owner_session=self.access.owner_session
        await self.device(owner_session,{'device_id':device_id})
        if owner_session!=self.access.owner_session:
            raise BridgeError('ownership_changed','The TapMesh owner session changed. Try again from the current session.')
        proof=None
        if self.hold_store:
            if owner_session is None:
                raise BridgeError('local_authorization_required','Connect the owner workspace before changing paused device controls')
            proof=self.hold_store.owner_proof(owner_session)
        if held:
            # Close admission before cancelling so new agent work cannot enter
            # while earlier device work is releasing input.
            if device_id not in self.holds: self._replace_holds(self.holds | {device_id},proof)
            tasks=[task for task,work in self.work.get(device_id,{}).items()
                   if not task.done() and not self._is_owner(work['session_id'],work['owner_authorized'])]
            await self._cancel_work(tasks)
        elif device_id in self.holds: self._replace_holds(self.holds - {device_id},proof)
        return {'device_id':device_id,'held':device_id in self.holds}
    @asynccontextmanager
    async def _ownership(self, did, session, tool=None, *, owner_authorized=False, recording_lock=None):
        self._check_hold(did,session,owner_authorized)
        takeover_eligible=recording_lock is None
        if takeover_eligible and did in self.takeovers:
            raise BridgeError('ownership_changed','The owner took control of this device; observe again before continuing')
        work=self.work.setdefault(did,{})
        if takeover_eligible and sum(item['takeover_eligible'] for item in work.values())>=self.max_device_requests:
            raise BridgeError('overloaded','Device work queue is full',retryable=True)
        task=asyncio.current_task(); generation=self.generations.get(did,0)
        work[task]={'session_id':session,'owner_authorized':owner_authorized,'takeover_eligible':takeover_eligible}
        action=None
        try:
            lock=self.locks.setdefault(did,asyncio.Lock()) if takeover_eligible else recording_lock
            async with lock:
                self._check_hold(did,session,owner_authorized)
                if takeover_eligible and (generation!=self.generations.get(did,0) or did in self.takeovers):
                    raise BridgeError('ownership_changed','Device ownership changed before execution')
                if tool is not None and not self._is_owner(session,owner_authorized):
                    now=time.monotonic(); self._prune_activity(now)
                    action=self.agent_activity.setdefault(did,{'count':0})
                    action.update(count=action['count']+1,at=now,tool=tool)
                yield
        finally:
            if action is not None: action.update(count=action['count']-1,at=time.monotonic(),tool=tool)
            work.pop(task,None)
            if not work: self.work.pop(did,None)
    async def _cancel_work(self, tasks):
        if not tasks: return
        for task in tasks: task.cancel()
        # Acknowledgement includes input and subprocess cleanup even if the
        # HTTP client disconnects while waiting.
        await settle_cleanup(asyncio.gather(*tasks,return_exceptions=True))
    async def takeover(self, session, device_id, *, preserve_approval=None):
        self._session(session)
        if not isinstance(device_id,str) or not 1<=len(device_id)<=256: raise BridgeError('invalid_arguments','device_id must contain 1–256 characters')
        device=await self.device(session,{'device_id':device_id})
        if device_id in self.takeovers: raise BridgeError('ownership_changed','Another local takeover is already in progress')
        self.takeovers.add(device_id)
        self.generations[device_id]=self.generations.get(device_id,0)+1
        tasks=[task for task,work in self.work.get(device_id,{}).items()
               if work['takeover_eligible'] and task is not asyncio.current_task() and not task.done()]
        self.observations.takeover(device_id,session,interrupted=bool(tasks))
        self.approvals.revoke_device(device_id,preserve=preserve_approval)
        try: await self._cancel_work(tasks)
        finally: self.takeovers.discard(device_id)
        return {'status':'success','data':{'device_id':device_id,'connection_epoch':device['connection_epoch'],'cancelled_requests':len(tasks),'ownership_generation':self.generations[device_id]}}
    async def _start_simulator(self, session, arguments, context, owner_authorized):
        deadline=arguments.get('timeout_ms',180000)/1000
        sid=arguments.get('simulator_id')
        device=await self.device(session,{'device_id':sid if sid.startswith('sim:') else 'sim:'+sid}) if sid else None
        did=device['device_id'] if device else None; epoch=device['connection_epoch'] if device else 0
        approval_device=device or {'device_id':None,'connection_epoch':0,'backend':'simulator'}
        self._check_hold(did,session,owner_authorized)
        self._require_approval(session,approval_device,'start_simulator',arguments,policy='C',owner_authorized=owner_authorized)
        async with asyncio.timeout(deadline):
            if device:
                async with self._ownership(did,session,'start_simulator',owner_authorized=owner_authorized):
                    current=await self.device(session,{'device_id':did})
                    self._check_hold(did,session,owner_authorized)
                    if current['connection_epoch']!=epoch: raise BridgeError('connection_changed','Simulator changed before startup')
                    self._require_approval(session,current,'start_simulator',arguments,policy='C',owner_authorized=owner_authorized,consume=True)
                    context['sent']=True
                    try: return await self.backend.invoke('start_simulator',None,arguments,context)
                    finally: self.observations.invalidate(did)
            # Only unbound creation is serialized globally. Once the adapter
            # reveals the actual ID it binds the ordinary per-device lease.
            await self.creation_lock.acquire(); creation_held=True; owner=None
            async def bind(found):
                nonlocal owner,creation_held
                candidate=self._ownership(found['device_id'],session,'start_simulator',owner_authorized=owner_authorized)
                await candidate.__aenter__(); owner=candidate
                context['created_device_id']=found['device_id']
                self.creation_lock.release(); creation_held=False
            context['bind_simulator']=bind
            try:
                self._require_approval(session,approval_device,'start_simulator',arguments,policy='C',owner_authorized=owner_authorized,consume=True)
                context['sent']=True
                return await self.backend.invoke('start_simulator',None,arguments,context)
            finally:
                if owner: await owner.__aexit__(None,None,None)
                if creation_held: self.creation_lock.release()
                if context.get('created_device_id'): self.observations.invalidate(context['created_device_id'])
    async def _preflight(self, tool, args, device):
        # A session must still be able to finalize its owned writer after a
        # disconnect or capability change. Ownership is checked before dispatch.
        if tool!='stop_recording': self.capability(tool,device)
        if tool=='batch_commands':
            did=device['device_id']
            for child in args['commands']:
                if child['tool'] not in BATCH_ALLOWED: raise BridgeError('invalid_arguments','Tool is not permitted in a batch')
                validate(child['tool'],child['arguments'])
                if child['arguments'].get('device_id',did)!=did: raise BridgeError('invalid_arguments','Batch children must use the single bound device')
                await self._preflight(child['tool'],child['arguments'],device)
        if tool in {'save_screenshot','start_recording','download_app_container','get_crash'}:
            if args.get('destination'):
                directory=self.artifacts.grants.check(args['destination'])
                if not directory.is_dir(): raise BridgeError('unsafe_path','Destination must be an approved directory')
            filename=args.get('filename')
            if filename and (Path(filename).name!=filename or filename in {'.','..'}): raise BridgeError('unsafe_path','Filename must be one component')
            if filename:
                target=Path(args.get('destination') or self.artifacts.root)/filename
                if target.exists(): raise BridgeError('artifact_conflict','Destination already exists')
        if tool=='build_and_run': self.build_grants.check(args['project_path'])
        if tool=='install_app': self.file_grants.check(args['artifact_path'])
        self.policy(tool,args)
    async def call(self, session, tool, arguments, *, owner_authorized=False):
        envelope={'request_id':uuid.uuid4().hex}; device=None
        context={'sent':False,'session_id':session,'router':self,'owner_authorized':owner_authorized}; admitted=False
        try:
            if time.monotonic()<self.update_deadline: raise BridgeError('overloaded','TapMesh is preparing an update; reconnect after it restarts',retryable=True)
            self._check_owner_session(session,owner_authorized)
            self._session(session); self.session_active[session]=self.session_active.get(session,0)+1; admitted=True
            arguments=deepcopy(arguments)
            validate(tool,arguments)
            if tool=='list_devices':
                devices=await self.devices(arguments.get('include_all_simulators',False))
                health=getattr(self.backend,'health',{})
                tools={name:health[name] for name in ('simctl','devicectl') if health.get(name) in {'available','unavailable'}}
                data={'devices':devices,'selected_device_id':self.sessions.get(session),'tools':tools}
            elif tool=='select_device':
                device=await self.device(session,arguments); self.sessions[session]=device['device_id']; data={'selected_device_id':device['device_id'],'ready':device['ready'],'readiness_reasons':device.get('readiness_reasons',[])}
            elif tool=='wait':
                start=time.monotonic(); await asyncio.wait_for(asyncio.sleep(arguments['duration_ms']/1000),arguments.get('timeout_ms',120000)/1000); data={'elapsed_ms':round((time.monotonic()-start)*1000),'completion_condition':'Monotonic delay elapsed'}
            elif tool=='start_simulator':
                data=await self._start_simulator(session,arguments,context,owner_authorized)
                if arguments.get('select',True): self.sessions[session]=data['device_id']
                device=await self.device(session,{'device_id':data['device_id']})
            else:
                owns_device=CATALOG[tool]['mutation'] or tool=='request_human_action'
                if owns_device: self._check_hold(arguments.get('device_id') or self.sessions.get(session),session,owner_authorized)
                device=await self.device(session,arguments)
                did=device['device_id']
                if owns_device: self._check_hold(did,session,owner_authorized)
                await self._preflight(tool,arguments,device)
                policy=self.policy(tool,arguments)
                if tool=='stop_recording':
                    recording=getattr(self.backend,'recordings',{}).get(arguments['recording_id'])
                    if not recording or recording['session_id']!=session or recording['device_id']!=device['device_id']: raise BridgeError('invalid_arguments','Recording is not owned by this session and device')
                    policy='O'
                if owns_device: self._check_hold(did,session,owner_authorized)
                self._require_approval(session,device,tool,arguments,policy=policy,owner_authorized=owner_authorized)
                deadline=arguments.get('timeout_ms',10000 if tool=='wait_for' else 1800000 if tool=='build_and_run' else 120000 if tool in {'request_human_action','batch_commands'} else 30000)/1000
                async def perform():
                    current=await self.device(session,{'device_id':did})
                    if owns_device: self._check_hold(did,session,owner_authorized)
                    if current['connection_epoch']!=device['connection_epoch']: raise BridgeError('connection_changed','Device connection changed before execution')
                    self._require_approval(session,current,tool,arguments,policy=policy,owner_authorized=owner_authorized,consume=True)
                    return await self._execute(session,tool,arguments,current,context)
                if owns_device:
                    async with asyncio.timeout(deadline):
                        if tool=='stop_recording':
                            rid=arguments['recording_id']
                            lock=self.recording_locks.setdefault(rid,asyncio.Lock())
                            try:
                                async with self._ownership(did,session,tool,owner_authorized=owner_authorized,recording_lock=lock): data=await perform()
                            finally:
                                # Backend keeps the durable final result; idle
                                # locks need no second retention map.
                                if not lock.locked() and not getattr(lock,'_waiters',None): self.recording_locks.pop(rid,None)
                        else:
                            async with self._ownership(did,session,tool,owner_authorized=owner_authorized): data=await perform()
                else: data=await asyncio.wait_for(perform(),deadline)
            envelope.update(status='success',data=data)
            if isinstance(data,dict) and data.get('status')=='partial': envelope['status']='partial'
        except ContractError as exc: envelope.update(status='error',error=BridgeError('invalid_arguments',str(exc)).public())
        except BridgeError as exc:
            if context['sent'] and exc.side_effect_state=='not_sent': exc.side_effect_state='unknown'
            envelope.update(status='error',error=exc.public())
        except asyncio.CancelledError:
            envelope.update(status='error',error=BridgeError('cancelled','Operation cancelled; inspect device state before retrying' if context['sent'] else 'Operation cancelled',side_effect_state='unknown' if context['sent'] else 'not_sent').public())
        except TimeoutError:
            envelope.update(status='error',error=BridgeError('delivery_outcome_unknown' if context['sent'] else 'deadline_exceeded','Operation deadline exceeded',side_effect_state='unknown' if context['sent'] else 'not_sent').public())
        except Exception as exc:
            diagnostic_id=uuid.uuid4().hex
            frames=[{'file':Path(frame.filename).name,'line':frame.lineno,'function':frame.name} for frame in traceback.extract_tb(exc.__traceback__)[-8:]]
            from .diagnostics import sanitize_event
            metadata=sanitize_event({'event':'unexpected_backend_exception','diagnostic_id':diagnostic_id,'exception_type':type(exc).__name__,'stack':frames})
            self.diagnostics.append(metadata)
            logging.getLogger('tapmesh.diagnostics').warning(json.dumps(metadata,separators=(',',':')))
            envelope.update(status='error',error=BridgeError('backend_failure','Unexpected backend failure; use the diagnostic reference in local service logs',side_effect_state='unknown' if context['sent'] else 'not_sent',details={'diagnostic_id':diagnostic_id}).public())
        finally:
            if admitted:
                self.session_active[session]-=1
                if not self.session_active[session]: self.session_active.pop(session,None)
                self.session_seen[session]=time.monotonic()
        if device: envelope.update(device_id=device['device_id'],connection_epoch=device['connection_epoch'])
        return envelope
    async def _elements(self, session, device, args, context):
        with self.observations.pinned(device['device_id'],device['connection_epoch']) as capture:
            return await self._elements_capture(session,device,args,context,capture)
    async def _check_capture(self, session, device, capture):
        try: current=await self.device(session,{'device_id':device['device_id']})
        except BridgeError as exc:
            self.observations.invalidate(device['device_id'])
            raise BridgeError('stale_observation','Device disappeared during capture') from exc
        if current['connection_epoch']!=device['connection_epoch']:
            self.observations.invalidate(device['device_id'])
            raise BridgeError('stale_observation','Device connection changed during capture')
        self.observations.check_capture(capture)
    async def _elements_capture(self, session, device, args, context, capture):
        source=args.get('source','auto')
        if source=='ocr':
            if not self.ocr: raise BridgeError('unsupported_capability','NAS OCR processor is not configured')
            result=await self.ocr.inspect(self.backend,device,context)
        else:
            try: result=await self.backend.invoke('list_elements',device,args,context)
            except BridgeError as exc:
                if source!='auto' or not self.ocr or exc.code not in {'unsupported_capability','prerequisite_required','backend_failure','deadline_exceeded'}: raise
                result=await self.ocr.inspect(self.backend,device,context)
                result['fallback_reason']=exc.code
        elements=deepcopy(result['elements']); maximum=args.get('max_elements',200)
        await self._check_capture(session,device,capture)
        for element in elements: element['ref']=uuid.uuid4().hex
        obs=self.observations.issue(session,device['device_id'],device['connection_epoch'],result['logical_size'],None,result['source'],orientation=result.get('orientation','unknown'),elements=elements[:maximum])
        self.observations.records[obs['observation_id']]['elements_complete']=len(elements)<=maximum and not result.get('backend_truncated',False)
        if '_captured_monotonic' in result:
            record=self.observations.records[obs['observation_id']]
            record['at']=result['_captured_monotonic']
            if result.get('_observed_at'): obs['observed_at']=result['_observed_at']
        if result.get('fallback_reason'): obs['fallback_reason']=result['fallback_reason']
        if result.get('_frame_pixel_sha256'): obs['frame_pixel_sha256']=result['_frame_pixel_sha256']
        return {**obs,'elements':elements[:maximum],'truncated':len(elements)>maximum or result.get('backend_truncated',False),'total_observed':len(elements)}
    def _match(self, elements, match):
        needle=match['text'] if match.get('case_sensitive',True) else match['text'].casefold()
        found=[]
        for element in elements:
            value=element.get('label') or element.get('value') or ''
            value=value if match.get('case_sensitive',True) else value.casefold()
            if value==needle if match['match']=='exact' else needle in value: found.append(element)
        occurrence=match.get('occurrence')
        if not found or occurrence and occurrence>len(found): raise BridgeError('stale_element_reference','No matching element was observed')
        if len(found)>1 and not occurrence: raise BridgeError('ambiguous_text_match','Multiple elements match; choose an occurrence',details={'candidate_count':len(found)})
        return found[(occurrence or 1)-1]
    async def _targets(self, session, device, args):
        did,epoch=device['device_id'],device['connection_epoch']
        targets={name:self.observations.resolve(session,did,epoch,args[name])
                 for name in ('target','start','end','center') if name in args}
        if 'start' in targets and 'end' in targets and targets['start'][2] is not targets['end'][2]:
            raise BridgeError('stale_observation','Gesture endpoints must share one observation')
        if targets and hasattr(self.backend,'geometry'):
            with self.observations.pinned(did,epoch) as capture:
                current=await self.backend.geometry(device)
                self.observations.check_capture(capture)
            for _,_,record in targets.values():
                public=record['public']
                if current['logical_size']!=public['logical_size'] or public['orientation']!='unknown' and current.get('orientation')!=public['orientation']:
                    self.observations.invalidate(did)
                    raise BridgeError('stale_observation','Observed display geometry changed; obtain a fresh snapshot')
        return targets
    async def _execute(self, session, tool, args, device, context):
        did=device['device_id']; epoch=device['connection_epoch']
        if tool=='get_device_info':
            extra={}
            if hasattr(self.backend,'geometry'):
                try: extra['screen']=await self.backend.geometry(device)
                except BridgeError as exc: extra['screen']={'status':'unavailable','reason':exc.message}
            return {**extra,**device,'observed_at':utcnow(),'source':'discovery','freshness':{'state':'fresh'},'limits':{'observation_max_age_ms':300000,'batch_max_children':25,'text_max_utf8_bytes':65536,'recording_max_seconds':3600}}
        if tool=='list_elements': return await self._elements(session,device,args,context)
        if tool=='request_human_action':
            verified=False
            if args.get('resume_condition'): verified=(await self._condition(session,device,args['resume_condition'],context))[0]
            return {'state':'completed','verified':verified,'completion_condition':'Local human acknowledged the handoff','resume_condition_satisfied':verified}
        if tool=='wait_for':
            start=time.monotonic(); timeout=args.get('timeout_ms',10000)/1000
            while True:
                satisfied,observation=await self._condition(session,device,args['condition'],context)
                if satisfied: return {'satisfied':True,'elapsed_ms':round((time.monotonic()-start)*1000),'observation':observation}
                if time.monotonic()-start>=timeout: raise BridgeError('deadline_exceeded','Condition was not observed before deadline')
                await asyncio.sleep(args.get('poll_interval_ms',250)/1000)
        if tool=='batch_commands':
            results=[]
            for i,child in enumerate(args['commands']):
                child_context={**context,'sent':False,'in_batch':True}
                try:
                    current=await self.device(session,{'device_id':did})
                    if current['connection_epoch']!=epoch: raise BridgeError('connection_changed','Batch device connection changed before this step')
                    data=await asyncio.wait_for(self._execute(session,child['tool'],child['arguments'],device,child_context),child['arguments'].get('timeout_ms',30000)/1000)
                    result={'index':i,'tool':child['tool'],'status':'success','data':data}
                except (BridgeError,TimeoutError,asyncio.CancelledError) as exc:
                    if not isinstance(exc,BridgeError): exc=BridgeError('cancelled' if isinstance(exc,asyncio.CancelledError) else 'deadline_exceeded','Batch step interrupted',side_effect_state='unknown' if child_context['sent'] else 'not_sent')
                    if child_context['sent'] and exc.side_effect_state=='not_sent': exc.side_effect_state='unknown'
                    result={'index':i,'tool':child['tool'],'status':'error','error':exc.public()}
                    results.append(result)
                    if args.get('stop_on_error',True) or exc.side_effect_state!='not_sent' or exc.code in {'cancelled','deadline_exceeded','delivery_outcome_unknown','protected_action_requires_human','connection_changed','device_disconnected','device_held'}: break
                    continue
                finally: context['sent']=context['sent'] or child_context['sent']
                results.append(result)
            partial=len(results)!=len(args['commands']) or any(r['status']!='success' for r in results)
            return {'status':'partial' if partial else 'success','results':results,'executed_steps':len(results),'total_steps':len(args['commands']),'rollback':False}
        if tool=='wait':
            start=time.monotonic(); await asyncio.sleep(args['duration_ms']/1000); return {'elapsed_ms':round((time.monotonic()-start)*1000)}
        if tool=='tap_text':
            snapshot=await self._elements(session,device,{'source':args.get('source','auto'),'max_elements':1000},context)
            element=self._match(snapshot['elements'],args['match'])
            data=await self._execute(session,'tap',{'target':{'ref':element['ref'],'snapshot_id':snapshot['snapshot_id']}},device,context)
            return {**data,'chosen_match':element,'source':snapshot['source']}
        if tool=='scroll_to':
            previous=None
            for step in range(args.get('max_steps',8)+1):
                snapshot=await self._elements(session,device,{'source':'auto','max_elements':1000},context)
                try:
                    if 'match' in args: element=self._match(snapshot['elements'],args['match'])
                    else:
                        found=[e for e in snapshot['elements'] if all(e.get(k)==v for k,v in args['selector'].items())]
                        if len(found)>1: raise BridgeError('ambiguous_text_match','Selector matches multiple elements')
                        if not found: raise BridgeError('stale_element_reference','No matching element')
                        element=found[0]
                    return {'found':True,'steps':step,'element':element,'observation':snapshot}
                except BridgeError as exc:
                    if exc.code!='stale_element_reference': raise
                signature=[(e.get('label'),e['bounds']) for e in snapshot['elements']]
                if step==args.get('max_steps',8) or signature==previous: return {'found':False,'steps':step,'exhausted':True,'reason':'no_progress' if signature==previous else 'max_steps'}
                previous=signature
                await self._execute(session,'scroll',{'direction':args['direction'],'distance_points':400},device,context)
        if tool=='handle_alert':
            old=self.observations.record(session,did,epoch,args['snapshot_id'],element=True)
            snapshot=await self._elements(session,device,{'source':'accessibility','max_elements':1000},context)
            modal=await self.backend.modal(device) if hasattr(self.backend,'modal') else None
            if modal and modal.get('kind')!='app': raise BridgeError('protected_action_requires_human','Complete the system or security modal on the device')
            if not modal and not any('alert' in (e.get('role','').lower()) for e in snapshot['elements']): raise BridgeError('unsupported_capability','No accessibility alert is observable')
            if 'button_ref' in args:
                chosen=next((e for e in old['elements'] if e['ref']==args['button_ref']),None)
                if not chosen: raise BridgeError('stale_element_reference','Alert button ref is stale')
                match={'text':chosen['label'],'match':'exact'}
            else: match={'text':args['button_label'],'match':'exact'}
            protected=('password','passcode','trust','apple account','apple id','payment','purchase','allow access','sign in','face id','touch id','permission')
            if any(word in ' '.join(e.get('label','') for e in snapshot['elements']).lower() for word in protected): raise BridgeError('protected_action_requires_human','Complete the protected prompt on the device')
            chosen=self._match([e for e in snapshot['elements'] if 'button' in e.get('role','').lower()],match)
            return await self._execute(session,'tap',{'target':{'ref':chosen['ref'],'snapshot_id':snapshot['snapshot_id']}},device,context)
        if tool=='go_back':
            if args.get('strategy','auto')=='edge_gesture': raise BridgeError('unsupported_capability','Edge-back gesture is not qualified for this app')
            snapshot=await self._elements(session,device,{'source':'accessibility','max_elements':1000},context)
            candidates=[e for e in snapshot['elements'] if (e.get('identifier')=='BackButton' or e.get('label') in {'Back','رجوع'}) and 'button' in e.get('role','').lower()]
            if len(candidates)!=1: raise BridgeError('unsupported_capability','No unique navigation Back button is exposed')
            data=await self._execute(session,'tap',{'target':{'ref':candidates[0]['ref'],'snapshot_id':snapshot['snapshot_id']}},device,context)
            return {**data,'strategy':'navigation_element'}
        # Physical text entry has no accessibility preflight; the backend refuses
        # input to a locked device, and the app asks the person for its own
        # confirmations.
        targets=await self._targets(session,device,args)
        if tool in {'type_text','fill'} and device.get('backend')!='physical':
            snapshot=await self._elements(session,device,{'source':'accessibility','max_elements':1000},context)
            if any('secure' in e.get('role','').lower() or e.get('secure') for e in snapshot['elements']): raise BridgeError('protected_action_requires_human','Complete sensitive text entry on the device')
        context['targets']=targets
        if CATALOG[tool]['mutation']:
            self._check_hold(did,session,context['owner_authorized'])
            context['sent']=True
        try:
            if tool in {'take_screenshot','save_screenshot'}:
                with self.observations.pinned(did,epoch) as capture:
                    result=await self.backend.invoke(tool,device,args,context)
                    await self._check_capture(session,device,capture)
            else: result=await self.backend.invoke(tool,device,args,context)
            # Check connection after input. Never auto-retry a post-send disconnect.
            if CATALOG[tool]['mutation'] and tool not in {'shut_down_simulator','stop_recording'}:
                try: current=await self.device(session,{'device_id':did})
                except BridgeError as exc: raise BridgeError('delivery_outcome_unknown','Device disappeared after sending the action',side_effect_state='unknown') from exc
                if current['connection_epoch']!=epoch or not current['ready']: raise BridgeError('delivery_outcome_unknown','Connection changed after sending the action',side_effect_state='unknown')
            return result
        except BaseException as exc:
            if isinstance(exc,BridgeError) and context['sent'] and exc.side_effect_state=='not_sent': exc.side_effect_state='unknown'
            if CATALOG[tool]['capability'].startswith('input.') and hasattr(self.backend,'release_input'):
                release=asyncio.create_task(asyncio.wait_for(self.backend.release_input(device,context),2))
                try: await settle_cleanup(release)
                except BaseException: pass
            raise
        finally:
            # Ordinary screen input (tap, swipe, typing…) leaves earlier pictures usable, so a
            # person or agent can act again before the next frame arrives. Other changes
            # (rotation, appearance, apps) still require a fresh observation.
            if CATALOG[tool]['mutation'] and not CATALOG[tool]['capability'].startswith('input.'): self.observations.invalidate(did)
    async def _condition(self, session, device, condition, context):
        kind=condition['type']
        current=await self.device(session,{'device_id':device['device_id']})
        if current['connection_epoch']!=device['connection_epoch']: raise BridgeError('connection_changed','Connection epoch changed while waiting')
        if kind=='device_ready': return current['ready'],{'observed_at':utcnow(),'source':'device_state','ready':current['ready']}
        if kind=='foreground_app':
            self.capability('get_foreground_app',device); result=await self.backend.invoke('get_foreground_app',device,{},context)
            return result.get('bundle_id')==condition['bundle_id'],result
        self.capability('list_elements',device)
        snapshot=await self._elements(session,device,{'source':condition.get('source','auto'),'max_elements':1000},context)
        if kind=='element_present': return any(all(e.get(k)==v for k,v in condition['selector'].items()) for e in snapshot['elements']),snapshot
        if kind=='text_absent' and snapshot['truncated']: raise BridgeError('unsupported_capability','Absence cannot be established from a truncated element snapshot')
        try: self._match(snapshot['elements'],condition['match']); present=True
        except BridgeError as exc:
            if exc.code=='ambiguous_text_match': present=True
            elif exc.code=='stale_element_reference': present=False
            else: raise
        return (not present if kind=='text_absent' else present),snapshot
