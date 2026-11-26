"""Opaque, finalized artifact store shared by the Mac agent and Linux coordinator."""
from pathlib import Path
import hashlib
import json
import os
import re
import shutil
import stat
from threading import RLock
import time
import uuid
from .safety import BridgeError, PathGrants, utcnow


class Artifacts:
    def __init__(self, root, export_roots=()):
        self.root=Path(root).expanduser().resolve(); self.root.mkdir(parents=True,exist_ok=True,mode=0o700)
        self.grants=PathGrants([self.root,*export_roots]); self.records={}; self._lock=RLock()
        for file in self.root.glob('*.json'):
            if file.is_symlink() or not re.fullmatch('[0-9a-f]{32}',file.stem): continue
            try:
                value=json.loads(file.read_text())
                if value['artifact_id']==file.stem: self.records[file.stem]=value
            except (OSError,ValueError,KeyError,TypeError): continue
        self.prune()
    def new_path(self, suffix):
        return self.root/(uuid.uuid4().hex+suffix)
    def check_storage(self, required=100*1024*1024):
        if shutil.disk_usage(self.root).free<required: raise BridgeError('insufficient_storage','Insufficient free artifact storage')
    def finalize(self, source, kind, device=None, epoch=None, *, destination=None, filename=None, state='complete'):
        source=Path(source)
        if source.is_symlink() or not source.is_file(): raise BridgeError('backend_failure','Backend did not produce a regular artifact file')
        if source.stat().st_size>2*1024**3: raise BridgeError('insufficient_storage','Artifact exceeds the 2 GiB initial limit')
        aid=uuid.uuid4().hex
        owned=self.root/(aid+source.suffix)
        metadata=self.root/(aid+'.json')
        copied=metadata_created=recorded=False
        try:
            with source.open('rb') as incoming, owned.open('xb') as output:
                copied=True
                shutil.copyfileobj(incoming,output,1024*1024); output.flush(); os.fsync(output.fileno())
            os.chmod(owned,0o600)
            with owned.open('rb') as file:
                digest=hashlib.file_digest(file,'sha256').hexdigest()
            local=owned
            if destination is not None or filename is not None:
                directory=self.grants.check(destination or self.root)
                if not directory.is_dir(): raise BridgeError('unsafe_path','Artifact destination must be an approved directory')
                filename=filename or owned.name
                if Path(filename).name!=filename or filename in {'.','..'}: raise BridgeError('unsafe_path','Filename must be a single safe component')
                target=self.grants.check(directory/filename,exists=False)
                try:
                    with target.open('xb') as output, owned.open('rb') as incoming:
                        shutil.copyfileobj(incoming,output,1024*1024); output.flush(); os.fsync(output.fileno())
                except FileExistsError as exc: raise BridgeError('artifact_conflict','Artifact destination already exists') from exc
                os.chmod(target,0o600); local=target
            record={'artifact_id':aid,'kind':kind,'state':state,'local_path':str(local),'size_bytes':owned.stat().st_size,'sha256':digest,'created_at':utcnow(),'device_id':device,'connection_epoch':epoch,'_stored_path':str(owned)}
            with self._lock:
                with metadata.open('x') as file:
                    metadata_created=True
                    json.dump(record,file)
                metadata.chmod(0o600)
                self.records[aid]=record
                recorded=True
                self.prune()
            return {k:v for k,v in record.items() if not k.startswith('_')}
        finally:
            if not recorded:
                if copied: owned.unlink(missing_ok=True)
                if metadata_created: metadata.unlink(missing_ok=True)
    def write(self, content, kind, suffix, device=None, epoch=None, **kwargs):
        self.check_storage(len(content)+1024*1024)
        path=self.new_path(suffix)
        try:
            with path.open('xb') as output: output.write(content)
            return self.finalize(path,kind,device,epoch,**kwargs)
        finally: path.unlink(missing_ok=True)
    def path(self, aid):
        if not re.fullmatch('[0-9a-f]{32}',aid): raise BridgeError('unsafe_path','Invalid artifact identifier')
        with self._lock:
            record=self.records.get(aid)
            if not record: raise BridgeError('invalid_arguments','Artifact not found')
            stored=self._owned_file(aid,record)
            if stored is None: raise BridgeError('unsafe_path','Artifact is not available')
            return stored[0]

    def record(self, aid):
        with self._lock:
            record=self.records.get(aid)
            if record and self._owned_file(aid,record) is not None:
                return {k:v for k,v in record.items() if not k.startswith('_')}
        return None

    def _owned_file(self, aid, record):
        raw=record.get('_stored_path')
        if not isinstance(raw,str): return None
        path=Path(raw)
        if path.parent!=self.root or not path.name.startswith(aid+'.') or path.name==aid+'.json': return None
        try: info=path.lstat()
        except FileNotFoundError: return None
        return (path,info) if stat.S_ISREG(info.st_mode) else None

    def _entries(self):
        entries=[]
        for aid,record in self.records.items():
            if owned:=self._owned_file(aid,record): entries.append((aid,*owned))
        return entries

    def usage(self):
        with self._lock:
            entries=self._entries()
            return {'count':len(entries),'bytes':sum(info.st_size for _,_,info in entries)}

    def _remove(self, entries):
        deleted=bytes_freed=0
        for aid,path,info in entries:
            try: path.unlink()
            except FileNotFoundError: pass
            else:
                deleted+=1
                bytes_freed+=info.st_size
            self.records.pop(aid,None)
            (self.root/(aid+'.json')).unlink(missing_ok=True)
        return {'deleted':deleted,'bytes_freed':bytes_freed}

    def prune(self, *, max_age_days=30, max_bytes=5*1024**3, now=None):
        cutoff=(time.time() if now is None else now)-max_age_days*86400
        with self._lock:
            recent=[]
            expired=[]
            for entry in sorted(self._entries(),key=lambda item:(item[2].st_mtime,item[0])):
                (expired if entry[2].st_mtime<cutoff else recent).append(entry)
            remaining=sum(info.st_size for _,_,info in recent)
            for entry in recent:
                if remaining<=max_bytes: break
                expired.append(entry)
                remaining-=entry[2].st_size
            return self._remove(expired)

    def clear(self):
        with self._lock:
            return self._remove(self._entries())
