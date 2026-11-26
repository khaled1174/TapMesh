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
from .core import settle_cleanup
from .safety import BridgeError, ControlAccess, read_secret, authenticates, inherited_approval_secret
from .http_files import ClosingStreamingResponse, LIVE_MEDIA_TYPE, TrackedFileResponse, live_error


DISCONNECT_POLL_SECONDS=.05


async def request_json(request, allowed, required):
    body=bytearray()
    async for chunk in request.stream():
        body.extend(chunk)
        if len(body)>1024*1024: raise BridgeError('invalid_arguments','Request body exceeds 1 MiB')
    try: value=json.loads(body,parse_constant=lambda _: (_ for _ in ()).throw(ValueError()))
    except (ValueError,UnicodeDecodeError): raise BridgeError('invalid_arguments','Invalid JSON document')
    if not isinstance(value,dict) or set(value)-set(allowed) or not set(required)<=set(value): raise BridgeError('invalid_arguments','Invalid closed request envelope')
    return value


async def request_control_settings(request):
    payload=await request_json(request,{'fast_control','agent_devices','owner_session'},set())
    if not payload: raise BridgeError('invalid_arguments','Provide at least one device control setting')
    if 'fast_control' in payload and not isinstance(payload['fast_control'],bool):
        raise BridgeError('invalid_arguments','fast_control must be true or false')
    return payload


def update_lease(payload):
    lease=payload['lease_id']
    if not isinstance(lease,str) or not 1<=len(lease)<=128 or any(not 33<=ord(char)<=126 for char in lease):
        raise BridgeError('invalid_arguments','Invalid update lease identifier')
    return lease


def fast_control_default():
    """Fast control is on unless the service was started with it turned off.
    The native app pushes the owner's saved choice on every connection."""
    return os.environ.get('TAPMESH_FAST_CONTROL','1').strip().lower() not in {'0','false','off','no'}


async def run_connected(request, operation):
    """Propagate HTTP disconnect to owned device work instead of abandoning it."""
    task=asyncio.create_task(operation)
    try:
        while not task.done():
            _,pending=await asyncio.wait((task,),timeout=DISCONNECT_POLL_SECONDS)
            if pending and await request.is_disconnected():
                task.cancel()
                return await settle_cleanup(task)
        return task.result()
    except BaseException:
        task.cancel()
        await settle_cleanup(asyncio.gather(task,return_exceptions=True))
        raise


def create_app(router=None, *, token=None, approval_token=None):
    token=token or read_secret(os.environ.get('TAPMESH_AGENT_TOKEN_FILE'))
    inherited_secret=inherited_approval_secret()
    approval_token=inherited_secret or approval_token or read_secret(os.environ.get('TAPMESH_APPROVAL_TOKEN_FILE'))
    instance=os.environ.get('TAPMESH_INSTANCE_ID')
    if router is None:
        from .backends import MacBackend
        from .core import Router
        state=Path(os.environ.get('TAPMESH_STATE_DIR','~/Library/Application Support/TapMesh/agent-state')).expanduser()
        roots=lambda name:[p for p in os.environ.get(name,'').split(os.pathsep) if p]
        access=ControlAccess(state/'control-settings.json',fast_control=True,forced_off=not fast_control_default())
        hold_scope=(instance,approval_token) if instance and os.environ.get('TAPMESH_BUNDLED_RUNTIME')=='1' else None
        router=Router(MacBackend(state),state_dir=state,export_roots=roots('TAPMESH_EXPORT_ROOTS'),build_roots=roots('TAPMESH_BUILD_ROOTS'),access=access,hold_scope=hold_scope)
    def is_owner_authorized(request):
        return authenticates(request.headers.get('x-devicebridge-local-authorization'),approval_token)
    def auth(request, local=False):
        if not authenticates(request.headers.get('authorization'),token): return JSONResponse({'error':'unauthorized'},status_code=401,headers={'WWW-Authenticate':'Bearer'})
        if local and not is_owner_authorized(request): return JSONResponse({'error':'local_authorization_required'},status_code=403)
        return None
    async def health(request):
        return JSONResponse({'status':'ok','version':__version__,'host':socket.gethostname(),'backend_health':getattr(router.backend,'health',{}),'instance_id':os.environ.get('TAPMESH_INSTANCE_ID'),
                             'approval_secret':'pipe' if inherited_secret is not None else 'file'})
    async def call(request):
        if denied:=auth(request): return denied
        try:
            payload=await request_json(request,{'session_id','tool','arguments'},{'session_id','tool','arguments'})
            if not isinstance(payload['tool'],str) or not isinstance(payload['arguments'],dict): raise BridgeError('invalid_arguments','Invalid tool or arguments type')
            result=await run_connected(request,router.call(payload['session_id'],payload['tool'],payload['arguments'],
                                                            owner_authorized=is_owner_authorized(request)))
            return JSONResponse(result)
        except BridgeError as exc: return JSONResponse({'error':exc.public()},status_code=400)
    async def approvals(request):
        if denied:=auth(request,True): return denied
        return JSONResponse({'approvals':router.approvals.pending()})
    async def decide(request):
        if denied:=auth(request,True): return denied
        try:
            payload=await request_json(request,{'decision'},{'decision'})
            def allow_grant(record):
                if record['policy']=='A':
                    router.access.allow_device(record['device_id'])
                    router.approvals.revoke_pending('C',matching=lambda pending: pending['device_id']==record['device_id'])
            approval=router.approvals.decide(request.path_params['approval_id'],payload['decision'],on_allow=allow_grant)
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
    async def activity(request):
        if denied:=auth(request): return denied
        return JSONResponse(router.activity(),headers={'Cache-Control':'no-store'})
    async def hold(request):
        if denied:=auth(request,True): return denied
        try:
            payload=await request_json(request,{'device_id','held'},{'device_id','held'})
            return JSONResponse(await router.hold(payload['device_id'],payload['held']))
        except BridgeError as exc: return JSONResponse({'error':exc.public()},status_code=400)
    async def settings(request):
        if denied:=auth(request,True): return denied
        try:
            if request.method=='POST':
                payload=await request_control_settings(request)
                access=router.access
                was_fast,old_devices,old_owner=access.fast_control,access.agent_devices,access.owner_session
                snapshot=access.update(**payload)
                try: router.reconcile_hold_owner(old_owner)
                except BridgeError:
                    access.owner_session=old_owner
                    raise
                if access.fast_control:
                    added=access.agent_devices-old_devices
                    if not was_fast: router.approvals.revoke_pending('C')
                    elif added or access.owner_session!=old_owner:
                        router.approvals.revoke_pending('C',matching=lambda record: record['device_id'] in added or record['session_id']==access.owner_session)
                    if added: router.approvals.revoke_pending('A',matching=lambda record: record['device_id'] in added)
                elif was_fast: router.approvals.revoke_pending('A')
                return JSONResponse(snapshot)
            return JSONResponse(router.access.snapshot())
        except BridgeError as exc: return JSONResponse({'error':exc.public()},status_code=400)
    async def artifact(request):
        if denied:=auth(request): return denied
        try:
            path=router.artifacts.path(request.path_params['artifact_id'])
            return TrackedFileResponse(path,tracker=router,filename=path.name,headers={'Cache-Control':'no-store'})
        except BridgeError as exc: return JSONResponse({'error':exc.public()},status_code=404)
    async def clear_storage(request):
        if denied:=auth(request,True): return denied
        try:
            result=await asyncio.to_thread(router.artifacts.clear)
        except OSError:
            return JSONResponse({'error':BridgeError('backend_failure','Some saved files could not be deleted. Try again.').public()},status_code=500)
        return JSONResponse(result,headers={'Cache-Control':'no-store'})
    async def live(request):
        # Read-only live video, authorized like take_screenshot (see Router.live).
        if denied:=auth(request): return denied
        try: view=await run_connected(request,router.live(request.query_params.get('session_id'),request.path_params['device_id']))
        except BridgeError as exc: return live_error(exc)
        async def release(): view.close()
        return ClosingStreamingResponse(view.messages(),on_close=release,media_type=LIVE_MEDIA_TYPE,headers={'Cache-Control':'no-store'})
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
    app=Starlette(routes=[Route('/health',health),Route('/v1/call',call,methods=['POST']),Route('/v1/takeover',takeover,methods=['POST']),Route('/v1/activity',activity),Route('/v1/hold',hold,methods=['POST']),Route('/v1/approvals',approvals),Route('/v1/approvals/{approval_id}',decide,methods=['POST']),Route('/v1/settings',settings,methods=['GET','POST']),Route('/v1/artifacts/{artifact_id}',artifact),Route('/v1/storage/clear',clear_storage,methods=['POST']),Route('/v1/live/{device_id}',live),Route('/v1/update',update,methods=['POST'])],lifespan=lifespan)
    app.state.router=router
    return app


def main():
    import uvicorn
    fd=int(os.environ['TAPMESH_LISTEN_FD']) if 'TAPMESH_LISTEN_FD' in os.environ else None
    uvicorn.run(create_app(),host='127.0.0.1',port=int(os.environ.get('TAPMESH_AGENT_PORT','48792')),fd=fd,access_log=False,log_level='warning',timeout_graceful_shutdown=5)


if __name__=='__main__': main()
