from dataclasses import dataclass
from contextlib import contextmanager, suppress
from contextvars import ContextVar
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
import hashlib
import hmac
import json
import os
import re
import stat
import tempfile
import time
import uuid


def utcnow():
    return datetime.now(timezone.utc).isoformat()


class BridgeError(Exception):
    def __init__(self, code, message, *, retryable=False, side_effect_state='not_sent', details=None):
        super().__init__(message)
        self.code,self.message,self.retryable,self.side_effect_state,self.details=code,message,retryable,side_effect_state,details or {}
    def public(self):
        result={'code':self.code,'message':self.message,'retryable':self.retryable,'side_effect_state':self.side_effect_state}
        if self.details: result['details']=self.details
        return result


def read_secret(filename):
    if not filename: raise RuntimeError('Credential file is not configured')
    path=Path(filename).expanduser()
    if path.is_symlink() or path.stat().st_mode & 0o077: raise RuntimeError('Credential must be an owner-only regular file')
    secret=path.read_text().strip()
    if len(secret)<43: raise RuntimeError('Credential must contain at least 256 bits of generated entropy')
    return secret


def read_secret_fd(fd):
    if fd < 3: raise RuntimeError('Invalid inherited approval credential descriptor')
    with os.fdopen(fd,'rb') as source:
        if not stat.S_ISFIFO(os.fstat(source.fileno()).st_mode): raise RuntimeError('Approval credential must arrive through a pipe')
        value=source.read(66).removesuffix(b'\n')
    if not re.fullmatch(rb'[0-9a-f]{64}',value): raise RuntimeError('Invalid inherited approval credential')
    return value.decode('ascii')


def inherited_approval_secret():
    descriptor=os.environ.pop('TAPMESH_APPROVAL_TOKEN_FD',None)
    if descriptor is None: return None
    try: fd=int(descriptor)
    except ValueError: raise RuntimeError('Invalid inherited approval credential descriptor') from None
    return read_secret_fd(fd)


def authenticates(header, secret):
    return bool(header and hmac.compare_digest(header.encode(),('Bearer '+secret).encode()))


class PathGrants:
    def __init__(self, roots):
        self.roots=[Path(p).expanduser().resolve() for p in roots]
    def check(self, value, *, exists=True):
        path=Path(value).expanduser()
        if not path.is_absolute() or '..' in path.parts: raise BridgeError('unsafe_path','An absolute path within an approved directory is required')
        # Reject symlinks anywhere under a grant, including the leaf. resolve alone
        # would allow links back inside a grant and leave time-of-check ambiguity.
        resolved=path.resolve(strict=False)
        for root in self.roots:
            if resolved.is_relative_to(root) and path.is_relative_to(root):
                current=root
                for part in path.relative_to(root).parts:
                    current=current/part
                    if current.is_symlink(): raise BridgeError('unsafe_path','Symlinks are not allowed in granted paths')
                if exists and not path.exists(): raise BridgeError('unsafe_path','Approved path does not exist')
                return path
        raise BridgeError('unsafe_path','Path is outside configured grants')


GRANT_SESSION='ai-clients'
GRANT_TOOL='allow_ai_control'
_UNSET=object()


class ControlAccess:
    """Durable control preferences and the current native app's session."""
    def __init__(self, path=None, *, fast_control=True, forced_off=False):
        self.path=Path(path) if path is not None else None
        self.forced_off=forced_off
        self.fast_control=fast_control and not forced_off
        self.agent_devices=set()
        self.owner_session=None
        if self.path is not None:
            try:
                settings=json.loads(self.path.read_text())
                if not isinstance(settings,dict) or set(settings)!={'fast_control','agent_devices'} or not isinstance(settings['fast_control'],bool):
                    raise ValueError('Invalid control settings')
                self.agent_devices=self._device_set(settings['agent_devices'])
                self.fast_control=settings['fast_control'] and not forced_off
            except FileNotFoundError: pass
            except (OSError, ValueError, BridgeError):
                self.fast_control=False
                self.agent_devices=set()

    @staticmethod
    def _device_set(devices):
        if not isinstance(devices,list) or len(devices)>64 or any(not isinstance(device,str) or not 1<=len(device)<=256 or not device.startswith('ios:') for device in devices):
            raise BridgeError('invalid_arguments','Allowed devices must be a list of up to 64 iOS device identifiers')
        return set(devices)

    def snapshot(self):
        return {'fast_control':self.fast_control,'agent_devices':sorted(self.agent_devices)}

    def _save(self, settings):
        temporary=None
        try:
            with tempfile.NamedTemporaryFile(mode='w',dir=self.path.parent,prefix='.control-settings-',delete=False) as stream:
                temporary=Path(stream.name)
                json.dump(settings,stream)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary,self.path)
        except OSError as exc:
            raise BridgeError('backend_failure','TapMesh could not save the device control settings') from exc
        finally:
            if temporary is not None:
                with suppress(OSError): temporary.unlink(missing_ok=True)

    def update(self, *, fast_control=_UNSET, agent_devices=_UNSET, owner_session=_UNSET):
        if fast_control is _UNSET: fast_control=self.fast_control
        if not isinstance(fast_control,bool): raise BridgeError('invalid_arguments','fast_control must be true or false')
        if fast_control and self.forced_off: raise BridgeError('invalid_arguments','Fast control is turned off by the service configuration')
        devices=self.agent_devices if agent_devices is _UNSET else self._device_set(agent_devices)
        if owner_session is _UNSET: owner_session=self.owner_session
        elif not isinstance(owner_session,str) or not 1<=len(owner_session)<=128:
            raise BridgeError('invalid_arguments','owner_session must contain 1–128 characters')
        settings={'fast_control':fast_control,'agent_devices':sorted(devices)}
        if self.path is not None and (fast_control!=self.fast_control or devices!=self.agent_devices):
            self._save(settings)
        self.fast_control,self.agent_devices,self.owner_session=fast_control,devices,owner_session
        return settings

    def allow_device(self, device_id):
        devices=self._device_set([device_id]) | self.agent_devices
        return self.update(agent_devices=sorted(devices))

    def immediate(self, session, device):
        owner=self.owner_session is not None and session==self.owner_session
        return self.fast_control and (owner or device['backend']=='simulator' or device['device_id'] in self.agent_devices)

    def needs_device_grant(self, session, device):
        return self.fast_control and device['backend']=='physical' and not self.immediate(session,device)


class Approvals:
    """Exact-action approvals and one-time grants for device control."""
    def __init__(self, ttl=120, maximum=512, terminal_ttl=300):
        self.ttl=ttl; self.maximum=maximum; self.terminal_ttl=terminal_ttl
        self.records={}; self.actions={}
    def revoke_pending(self, policy, *, matching=None):
        for record in self.records.values():
            if record['policy']==policy and record['decision']=='pending' and (matching is None or matching(record)):
                self._terminal(record,'revoked')
        self._prune()
    def _terminal(self, record, decision):
        record['decision']=decision; record['_terminal_at']=time.monotonic()
    def _prune(self):
        now=time.monotonic()
        for aid,record in list(self.records.items()):
            if record['decision'] in {'pending','allow'} and now>record['_expires']:
                self._terminal(record,'expired')
            if record['decision'] in {'expired','consumed','revoked'}:
                if self.actions.get(record['digest'])==aid: self.actions.pop(record['digest'],None)
            if '_terminal_at' in record and now-record['_terminal_at']>=self.terminal_ttl:
                self.records.pop(aid,None)
                if self.actions.get(record['digest'])==aid: self.actions.pop(record['digest'],None)
    def revoke_device(self, device, *, preserve=None):
        for record in self.records.values():
            if record['device_id']==device and record['id']!=preserve and record['policy']!='A':
                self._terminal(record,'revoked')
        self._prune()
    def _digest(self, session, device, epoch, tool, arguments, policy):
        raw=json.dumps([session,device,epoch,tool,arguments,policy],sort_keys=True,separators=(',',':'),allow_nan=False).encode()
        return hashlib.sha256(raw).hexdigest()
    def require(self, session, device, epoch, tool, arguments, *, policy, consume=False, immediate=False):
        if policy=='O' or policy=='C' and immediate: return
        self._prune()
        digest=self._digest(session,device,epoch,tool,arguments,policy)
        aid=self.actions.get(digest); record=self.records.get(aid)
        if record and record['decision']=='deny':
            # An exact action may ask again. Keep a denied device grant stable
            # until pruning so an agent cannot repeatedly prompt the owner.
            if policy!='A': self.actions.pop(digest,None)
            raise BridgeError('approval_denied','Local coordinator denied this action',details={'approval_id':aid})
        if record and record['decision']=='allow':
            if consume:
                self._terminal(record,'consumed'); self.actions.pop(digest,None)
            return
        if not record or record['decision']=='consumed':
            if len(self.records)>=self.maximum:
                raise BridgeError('overloaded','Local approval capacity reached; resolve pending approvals or wait for expiry',retryable=True)
            aid=uuid.uuid4().hex
            record={'id':aid,'session_id':session,'device_id':device,'connection_epoch':epoch,'tool':tool,'policy':policy,'digest':digest,'decision':'pending','created_at':utcnow(),'expires_in_seconds':self.ttl,'_expires':time.monotonic()+self.ttl}
            # Only names and size are shown for sensitive content. Exact canonical
            # arguments remain in the digest and never enter logs/history/UI.
            record['argument_summary']={k:('[redacted]' if k in {'text','instructions','environment','url'} else v if isinstance(v,(str,int,float,bool)) else '[structured]') for k,v in arguments.items()}
            self.records[aid]=record; self.actions[digest]=aid
        if policy=='A':
            raise BridgeError('approval_required','The owner must allow AI control of this device in the TapMesh app',details={'approval_id':aid,'policy':'A','scope':'device'})
        code='protected_action_requires_human' if policy=='U' else 'approval_required'
        raise BridgeError(code,'Complete this action in the local coordinator',details={'approval_id':aid,'policy':policy})
    def pending(self):
        self._prune()
        return [{k:v for k,v in record.items() if not k.startswith('_')} for record in self.records.values() if record['decision']=='pending' and record['_expires']>time.monotonic()]
    def decide(self, aid, decision, *, on_allow=None):
        self._prune()
        record=self.records.get(aid)
        if not record: raise BridgeError('invalid_arguments','Unknown approval ID')
        if decision not in {'allow','deny'}: raise BridgeError('invalid_arguments','Decision must be allow or deny')
        if record['decision']=='expired' or record['_expires']<time.monotonic(): raise BridgeError('approval_expired','Local approval expired')
        if record['decision']!='pending': raise BridgeError('invalid_arguments','Approval was already decided')
        if decision=='deny': self._terminal(record,decision)
        else:
            if on_allow is not None: on_allow(record)
            if record['policy']=='A':
                # The persistent allowlist is the authority. No reusable
                # approval should survive removal of the device from that list.
                self._terminal(record,'consumed')
                self.actions.pop(record['digest'],None)
            else: record['decision']=decision
        return {**{k:v for k,v in record.items() if not k.startswith('_')},'decision':decision}


class Observations:
    def __init__(self, max_age=300, maximum=256):
        self.max_age=max_age; self.maximum=maximum; self.records={}; self.revisions={}
        self._capture=ContextVar('tapmesh_observation_capture',default=None)
    def invalidate(self, device):
        self.revisions[device]=self.revisions.get(device,0)+1
        self.records={key:record for key,record in self.records.items() if record['device']!=device}
    def takeover(self, device, session, *, interrupted):
        preserved={key:record for key,record in self.records.items() if record['device']==device and record['session']==session} if not interrupted else {}
        self.invalidate(device)
        for record in preserved.values(): record['revision']=self.revisions[device]
        self.records.update(preserved)
    @contextmanager
    def pinned(self, device, epoch):
        capture=(device,epoch,self.revisions.get(device,0))
        token=self._capture.set(capture)
        try: yield capture
        finally: self._capture.reset(token)
    def check_capture(self, capture):
        if capture[2]!=self.revisions.get(capture[0],0):
            raise BridgeError('stale_observation','Device changed during capture; obtain a fresh observation')
    def issue(self, session, device, epoch, logical, image_size, source, *, orientation='unknown', elements=None):
        capture=self._capture.get()
        if capture:
            if capture[:2]!=(device,epoch): raise BridgeError('stale_observation','Capture identity changed before publication')
            self.check_capture(capture)
        logical=deepcopy(logical); elements=deepcopy(elements)
        oid=uuid.uuid4().hex; sid=uuid.uuid4().hex if elements is not None else None
        public={'observation_id':oid,'device_id':device,'connection_epoch':epoch,'observed_at':utcnow(),'source':source,'orientation':orientation,'logical_size':logical,'freshness':{'state':'fresh','max_age_ms':self.max_age*1000}}
        if image_size:
            iw,ih=image_size; public['image_size']={'width':iw,'height':ih}
            public['image_to_device_transform']={'a':logical['width']/iw,'b':0,'c':0,'d':logical['height']/ih,'tx':0,'ty':0}
        if sid: public['snapshot_id']=sid
        record={'session':session,'device':device,'epoch':epoch,'at':time.monotonic(),'revision':self.revisions.get(device,0),'public':public,'elements':elements or []}
        self.records[oid]=record
        if sid: self.records[sid]=record
        while len(self.records)>self.maximum*2: self.records.pop(next(iter(self.records)))
        return public
    def record(self, session, device, epoch, identifier, *, element=False):
        record=self.records.get(identifier)
        error='stale_element_reference' if element else 'stale_observation'
        if not record or record['session']!=session or record['device']!=device or record['epoch']!=epoch or time.monotonic()-record['at']>self.max_age or record['revision']!=self.revisions.get(device,0):
            raise BridgeError(error,'Obtain a fresh observation in this session and connection epoch')
        return record
    def resolve(self, session, device, epoch, target):
        if 'ref' in target:
            record=self.record(session,device,epoch,target['snapshot_id'],element=True)
            candidates=[e for e in record['elements'] if e['ref']==target['ref']]
            if len(candidates)!=1: raise BridgeError('stale_element_reference','Element reference is absent from this snapshot')
            box=candidates[0]['bounds']; x=box['x']+box['width']/2; y=box['y']+box['height']/2
        else:
            record=self.record(session,device,epoch,target['observation_id']); x,y=target['x'],target['y']
        size=record['public']['logical_size']
        if not (0<=x<size['width'] and 0<=y<size['height']): raise BridgeError('invalid_arguments','Target is outside the observed display')
        return x,y,record
