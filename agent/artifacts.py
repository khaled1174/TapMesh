"""Opaque, finalized artifact store shared by the Mac agent and Linux coordinator."""
from pathlib import Path
import hashlib
import json
import os
import re
import shutil
import tempfile
import uuid
from .safety import BridgeError, PathGrants, utcnow


class Artifacts:
    def __init__(self, root, export_roots=()):
        self.root=Path(root).expanduser().resolve(); self.root.mkdir(parents=True,exist_ok=True,mode=0o700)
        self.grants=PathGrants([self.root,*export_roots]); self.records={}
        for file in self.root.glob('*.json'):
            try:
                value=json.loads(file.read_text()); self.records[value['artifact_id']]=value
            except (OSError,ValueError,KeyError): continue
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
        with source.open('rb') as incoming, owned.open('xb') as output:
            shutil.copyfileobj(incoming,output,1024*1024); output.flush(); os.fsync(output.fileno())
        os.chmod(owned,0o600)
        digest=hashlib.file_digest(owned.open('rb'),'sha256').hexdigest()
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
        metadata=self.root/(aid+'.json'); metadata.write_text(json.dumps(record)); metadata.chmod(0o600)
        self.records[aid]=record
        return {k:v for k,v in record.items() if not k.startswith('_')}
    def write(self, content, kind, suffix, device=None, epoch=None, **kwargs):
        self.check_storage(len(content)+1024*1024)
        path=self.new_path(suffix)
        try:
            with path.open('xb') as output: output.write(content)
            return self.finalize(path,kind,device,epoch,**kwargs)
        finally: path.unlink(missing_ok=True)
    def path(self, aid):
        if not re.fullmatch('[0-9a-f]{32}',aid): raise BridgeError('unsafe_path','Invalid artifact identifier')
        record=self.records.get(aid)
        if not record: raise BridgeError('invalid_arguments','Artifact not found')
        path=Path(record['_stored_path'])
        if path.is_symlink() or path.parent!=self.root or not path.is_file(): raise BridgeError('unsafe_path','Artifact is not available')
        return path
