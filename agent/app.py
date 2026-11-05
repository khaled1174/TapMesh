"""TapMesh authenticated loopback Mac device agent."""
import asyncio
from contextlib import asynccontextmanager
import json
import os
from pathlib import Path
import socket
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse, FileResponse
from starlette.routing import Route
from Contracts import __version__
from .safety import BridgeError, read_secret, authenticates
from .http_files import TrackedFileResponse


async def request_json(request, allowed, required):
    body=bytearray()
    async for chunk in request.stream():
        body.extend(chunk)
        if len(body)>1024*1024: raise BridgeError('invalid_arguments','Request body exceeds 1 MiB')
    try: value=json.loads(body,parse_constant=lambda _: (_ for _ in ()).throw(ValueError()))
    except (ValueError,UnicodeDecodeError): raise BridgeError('invalid_arguments','Invalid JSON document')
    if not isinstance(value,dict) or set(value)-set(allowed) or not set(required)<=set(value): raise BridgeError('invalid_arguments','Invalid closed request envelope')
    return value


def update_lease(payload):
    lease=payload['lease_id']
    if not isinstance(lease,str) or not 1<=len(lease)<=128 or any(not 33<=ord(char)<=126 for char in lease):
        raise BridgeError('invalid_arguments','Invalid update lease identifier')
    return lease


async def run_connected(request, operation):
    """Propagate HTTP disconnect to owned device work instead of abandoning it."""
    task=asyncio.create_task(operation)
    try:
        while not task.done():
            await asyncio.sleep(.05)
            if await request.is_disconnected():
                task.cancel(); break
        return await task
    except asyncio.CancelledError:
        task.cancel()
        try: await task
        except asyncio.CancelledError: pass
        raise


def create_app(router=None, *, token=None, approval_token=None):
    if router is None:
        from .backends import MacBackend
        from .core import Router
        state=Path(os.environ.get('TAPMESH_STATE_DIR','~/Library/Application Support/TapMesh/agent-state')).expanduser()
        roots=lambda name:[p for p in os.environ.get(name,'').split(os.pathsep) if p]
        router=Router(MacBackend(state),state_dir=state,export_roots=roots('TAPMESH_EXPORT_ROOTS'),build_roots=roots('TAPMESH_BUILD_ROOTS'))
    token=token or read_secret(os.environ.get('TAPMESH_AGENT_TOKEN_FILE'))
    approval_token=approval_token or read_secret(os.environ.get('TAPMESH_APPROVAL_TOKEN_FILE'))
    def auth(request, local=False):
        if not authenticates(request.headers.get('authorization'),token): return JSONResponse({'error':'unauthorized'},status_code=401,headers={'WWW-Authenticate':'Bearer'})
        if local and not authenticates(request.headers.get('x-devicebridge-local-authorization'),approval_token): return JSONResponse({'error':'local_authorization_required'},status_code=403)
        return None
    async def health(request):
        return JSONResponse({'status':'ok','version':__version__,'host':socket.gethostname(),'backend_health':getattr(router.backend,'health',{}),'instance_id':os.environ.get('TAPMESH_INSTANCE_ID')})
    async def call(request):
        if denied:=auth(request): return denied
        try:
            payload=await request_json(request,{'session_id','tool','arguments'},{'session_id','tool','arguments'})
            if not isinstance(payload['tool'],str) or not isinstance(payload['arguments'],dict): raise BridgeError('invalid_arguments','Invalid tool or arguments type')
            result=await run_connected(request,router.call(payload['session_id'],payload['tool'],payload['arguments']))
            return JSONResponse(result)
        except BridgeError as exc: return JSONResponse({'error':exc.public()},status_code=400)
    async def approvals(request):
        if denied:=auth(request,True): return denied
        return JSONResponse({'approvals':router.approvals.pending()})
    async def decide(request):
        if denied:=auth(request,True): return denied
        try:
            payload=await request_json(request,{'decision'},{'decision'})
            approval=router.approvals.decide(request.path_params['approval_id'],payload['decision'])
            if payload['decision']=='allow' and approval['policy']=='U':
                await router.takeover(approval['session_id'],approval['device_id'],preserve_approval=approval['id'])
            return JSONResponse({'approval':approval})
        except BridgeError as exc: return JSONResponse({'error':exc.public()},status_code=400)
    async def takeover(request):
        if denied:=auth(request,True): return denied
        try:
            payload=await request_json(request,{'session_id','device_id'},{'session_id','device_id'})
            return JSONResponse(await router.takeover(payload['session_id'],payload['device_id']))
        except BridgeError as exc: return JSONResponse({'error':exc.public()},status_code=400)
    async def artifact(request):
        if denied:=auth(request): return denied
        try:
            path=router.artifacts.path(request.path_params['artifact_id'])
            return TrackedFileResponse(path,tracker=router,filename=path.name,headers={'Cache-Control':'no-store'})
        except BridgeError as exc: return JSONResponse({'error':exc.public()},status_code=404)
    async def update(request):
        if denied:=auth(request,True): return denied
        try:
            payload=await request_json(request,{'action','lease_id'},{'action','lease_id'})
            lease=update_lease(payload)
            if payload['action']=='prepare': result=router.prepare_update(lease)
            elif payload['action']=='resume': result=router.resume_after_update(lease)
            else: raise BridgeError('invalid_arguments','Unknown update coordination action')
            return JSONResponse(result)
        except BridgeError as exc:
            return JSONResponse({'error':exc.public()},status_code=409 if exc.code=='overloaded' else 400)
    @asynccontextmanager
    async def lifespan(app):
        try: yield
        finally: await router.close()
    app=Starlette(routes=[Route('/health',health),Route('/v1/call',call,methods=['POST']),Route('/v1/takeover',takeover,methods=['POST']),Route('/v1/approvals',approvals),Route('/v1/approvals/{approval_id}',decide,methods=['POST']),Route('/v1/artifacts/{artifact_id}',artifact),Route('/v1/update',update,methods=['POST'])],lifespan=lifespan)
    app.state.router=router
    return app


def main():
    import uvicorn
    fd=int(os.environ['TAPMESH_LISTEN_FD']) if 'TAPMESH_LISTEN_FD' in os.environ else None
    uvicorn.run(create_app(),host='127.0.0.1',port=int(os.environ.get('TAPMESH_AGENT_PORT','48792')),fd=fd,access_log=False,log_level='warning',timeout_graceful_shutdown=5)


if __name__=='__main__': main()
