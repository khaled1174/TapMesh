"""Same authenticated routing for native HTTP and MCP clients, with NAS storage."""
import asyncio
import base64
import hashlib
import io
import json
from pathlib import Path
import sqlite3
import time
import uuid
import httpx
from PIL import Image
from Contracts.catalog import CATALOG, validate, ContractError
from agent.artifacts import Artifacts
from agent.safety import BridgeError, utcnow
from agent.core import settle_cleanup


class Coordinator:
    def __init__(self, state_dir, client):
        self.state=Path(state_dir); self.state.mkdir(parents=True,exist_ok=True,mode=0o700)
        self.artifacts=Artifacts(self.state/'artifacts'); self.client=client
        self.db=sqlite3.connect(self.state/'metadata.sqlite3',check_same_thread=False)
        self.db.execute('PRAGMA journal_mode=WAL'); self.db.execute('CREATE TABLE IF NOT EXISTS history (id TEXT PRIMARY KEY, at TEXT, session TEXT, tool TEXT, status TEXT, error TEXT, elapsed_ms INTEGER)')
        self.db.execute('CREATE TABLE IF NOT EXISTS transfers (remote_id TEXT PRIMARY KEY, local_id TEXT, sha256 TEXT)'); self.db.commit()
        self.active_requests=0; self.update_deadline=0.0; self.update_lease=None; self.active_downloads=0
    async def close(self): self.db.close()
    def history(self, limit=100):
        rows=self.db.execute('SELECT id,at,tool,status,error,elapsed_ms FROM history ORDER BY rowid DESC LIMIT ?',(min(max(limit,1),500),)).fetchall()
        return [dict(zip(('request_id','at','tool','status','error_code','elapsed_ms'),row)) for row in rows]
    async def call(self, session, tool, arguments):
        if time.monotonic()<self.update_deadline:
            return {'request_id':uuid.uuid4().hex,'status':'error','error':BridgeError('overloaded','TapMesh is preparing an update; reconnect after it restarts',retryable=True).public()}
        self.active_requests+=1
        try: return await self._call(session,tool,arguments)
        finally: self.active_requests-=1
    def prepare_update(self, lease_id):
        if time.monotonic()<self.update_deadline and self.update_lease!=lease_id:
            raise BridgeError('overloaded','Another update owns the local preparation lease',retryable=True)
        if self.active_requests or self.active_downloads:
            raise BridgeError('overloaded','An update is waiting for active requests or artifact transfers to finish',retryable=True)
        self.update_deadline=time.monotonic()+60
        self.update_lease=lease_id
        return {'status':'prepared','expires_in_seconds':60,'lease_id':lease_id}
    def resume_after_update(self, lease_id):
        if self.update_lease is not None and self.update_lease!=lease_id:
            raise BridgeError('ownership_changed','Only the update that prepared this runtime can resume it')
        self.update_deadline=0.0; self.update_lease=None
    async def _call(self, session, tool, arguments):
        start=time.monotonic(); rid=uuid.uuid4().hex
        try:
            validate(tool,arguments)
            if not isinstance(session,str) or not 1<=len(session)<=128: raise BridgeError('invalid_arguments','session_id must contain 1–128 characters')
            # Capture a fresh native PNG, while JPEG encoding/resizing is portable
            # work performed on the NAS. Device geometry stays in device points.
            forwarded=dict(arguments)
            if tool=='take_screenshot': forwarded.update(format='png',max_width=4096)
            timeout=arguments.get('timeout_ms',1800000 if tool=='build_and_run' else 180000 if tool=='start_simulator' else 120000 if tool in {'batch_commands','request_human_action','wait'} else 30000)/1000+10
            response=await self.client.post('/v1/call',json={'session_id':session,'tool':tool,'arguments':forwarded},timeout=timeout)
            response.raise_for_status(); result=response.json()
            if not isinstance(result,dict) or 'status' not in result or 'request_id' not in result: raise BridgeError('backend_failure','Mac agent returned invalid tool envelope')
            if result.get('status') in {'success','partial'}:
                if tool=='take_screenshot' and result.get('data',{}).get('image'): result['data']=await asyncio.to_thread(self.process_image,result['data'],arguments)
            result=await self._transfer_tree(result)
        except ContractError as exc: result={'request_id':rid,'status':'error','error':BridgeError('invalid_arguments',str(exc)).public()}
        except BridgeError as exc: result={'request_id':rid,'status':'error','error':exc.public()}
        except asyncio.CancelledError: raise
        except (httpx.HTTPError,ValueError):
            mutation=CATALOG.get(tool,{}).get('mutation',False)
            result={'request_id':rid,'status':'error','error':BridgeError('delivery_outcome_unknown' if mutation else 'backend_failure','Agent transport interrupted; no automatic retry was performed',retryable=not mutation,side_effect_state='unknown' if mutation else 'not_sent').public()}
        self.db.execute('INSERT OR REPLACE INTO history VALUES (?,?,?,?,?,?,?)',(result['request_id'],utcnow(),hashlib.sha256(session.encode()).hexdigest()[:16] if isinstance(session,str) else 'invalid',tool if tool in CATALOG else 'unknown',result['status'],result.get('error',{}).get('code'),round((time.monotonic()-start)*1000)))
        self.db.execute('DELETE FROM history WHERE rowid NOT IN (SELECT rowid FROM history ORDER BY rowid DESC LIMIT 5000)'); self.db.commit()
        return result
    @staticmethod
    def process_image(data, arguments):
        raw=base64.b64decode(data['image']['data'],validate=True)
        if len(raw)>8*1024*1024: raise BridgeError('backend_failure','Screenshot exceeds portable processing bound')
        with Image.open(io.BytesIO(raw)) as source:
            source.load(); image=source.copy()
        maximum=arguments.get('max_width',1280)
        if image.width>maximum: image.thumbnail((maximum,16384))
        format=arguments.get('format','jpeg'); output=io.BytesIO()
        if format=='jpeg': image=image.convert('RGB')
        image.save(output,format='JPEG' if format=='jpeg' else 'PNG',**({'quality':round(arguments.get('quality',.8)*100)} if format=='jpeg' else {}))
        encoded=output.getvalue()
        if len(encoded)>8*1024*1024: raise BridgeError('backend_failure','Processed screenshot exceeds 8 MiB')
        data['image']={'mime_type':'image/jpeg' if format=='jpeg' else 'image/png','data':base64.b64encode(encoded).decode()}
        data['image_size']={'width':image.width,'height':image.height}; logical=data['logical_size']
        data['image_to_device_transform']={'a':logical['width']/image.width,'b':0,'c':0,'d':logical['height']/image.height,'tx':0,'ty':0}
        data.update(requested_max_width=maximum,effective_width=image.width,processed_on='server')
        return data
    async def _transfer_tree(self, value):
        if isinstance(value,list): return [await self._transfer_tree(item) for item in value]
        if not isinstance(value,dict): return value
        if {'artifact_id','sha256','size_bytes','state'}<=value.keys():
            try: return await self._transfer(value)
            except (BridgeError,httpx.HTTPError,OSError): return self._transfer_failed(value)
        result={k:await self._transfer_tree(v) for k,v in value.items()}
        def failed(item):
            if isinstance(item,dict): return item.get('storage')=='transfer_failed' or any(failed(v) for v in item.values())
            return isinstance(item,list) and any(failed(v) for v in item)
        if 'request_id' in result and failed(result):
            result['artifact_transfer_status']='partial'
            if result.get('status')!='error': result['status']='partial'
        return result
    @staticmethod
    def _transfer_failed(artifact):
        return {k:v for k,v in artifact.items() if k!='local_path'}|{'storage':'transfer_failed','state':'partial','error':{'code':'backend_failure','message':'NAS artifact transfer failed; source remains on the Mac','side_effect_state':'confirmed','retryable':True}}
    async def _transfer(self, artifact):
        remote=artifact['artifact_id']; expected=artifact['sha256']; size=artifact['size_bytes']
        if not isinstance(size,int) or not 0<=size<=2*1024**3: raise BridgeError('backend_failure','Artifact size exceeds NAS transfer bound')
        # Validate even cached source IDs; arbitrary paths never reach the agent.
        import re
        if not re.fullmatch('[0-9a-f]{32}',remote): raise BridgeError('unsafe_path','Invalid remote artifact identifier')
        row=self.db.execute('SELECT local_id,sha256 FROM transfers WHERE remote_id=?',(remote,)).fetchone()
        if row and row[1]==expected and row[0] in self.artifacts.records:
            local={k:v for k,v in self.artifacts.records[row[0]].items() if not k.startswith('_')}
        else:
            self.artifacts.check_storage(size+1024*1024); suffix=Path(artifact.get('local_path','')).suffix
            if not re.fullmatch(r'\.[a-zA-Z0-9]{1,8}',suffix): suffix='.bin'
            path=self.artifacts.new_path(suffix); digest=hashlib.sha256(); total=0
            try:
                async with self.client.stream('GET','/v1/artifacts/'+remote,timeout=120) as response:
                    response.raise_for_status()
                    with path.open('xb') as output:
                        async for chunk in response.aiter_bytes(1024*1024):
                            total+=len(chunk)
                            if total>size or total>2*1024**3: raise BridgeError('backend_failure','Artifact exceeded its declared transfer size')
                            digest.update(chunk); output.write(chunk)
                if total!=size or digest.hexdigest()!=expected: raise BridgeError('backend_failure','Artifact checksum or size did not match')
                finalizer=asyncio.create_task(asyncio.to_thread(self.artifacts.finalize,path,artifact.get('kind','artifact'),artifact.get('device_id'),artifact.get('connection_epoch'),state=artifact['state']))
                local=await settle_cleanup(finalizer)
                self.db.execute('INSERT OR REPLACE INTO transfers VALUES (?,?,?)',(remote,local['artifact_id'],expected)); self.db.commit()
            except (BridgeError,httpx.HTTPError,OSError):
                return self._transfer_failed(artifact)
            finally: path.unlink(missing_ok=True)
        return {**artifact,**local,'storage':'server','download_url':'/api/artifacts/'+local['artifact_id']}
