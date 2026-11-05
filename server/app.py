"""Authenticated loopback NAS HTTP and official MCP Streamable HTTP service."""
import asyncio
import base64
from contextlib import asynccontextmanager
import hashlib
import json
import os
from pathlib import Path
import re
import uuid
import httpx
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import FileResponse, JSONResponse
from starlette.routing import Mount, Route
from mcp import types
from mcp.server.lowlevel import Server
from mcp.server.transport_security import TransportSecuritySettings
from Contracts import __version__
from Contracts.catalog import CATALOG, public_catalog
from agent.app import request_json, run_connected, update_lease
from agent.http_files import TrackedFileResponse
from agent.safety import BridgeError, authenticates, read_secret
from .coordinator import Coordinator
from .ocr import recognize as recognize_ocr


async def ocr_health():
    if os.environ.get('TAPMESH_OCR_ENGINE')=='vision':
        from .vision_ocr import capabilities
        try: return await capabilities()
        except BridgeError: return {'status':'unavailable','engine':'vision','languages':[]}
    from agent.process import Runner
    runner=Runner(output_limit=65536)
    try:
        result=await runner.run([os.environ.get('TAPMESH_TESSERACT','/usr/bin/tesseract'),'--list-langs'],timeout=2)
        languages={line.strip() for line in result.stdout.decode('utf-8','replace').splitlines()}
        ready={'eng','ara'}<=languages
        return {'status':'available' if ready else 'unavailable','engine':'tesseract',
                'languages':sorted(languages & {'eng','ara'})}
    except BridgeError:
        return {'status':'unavailable','engine':'tesseract','languages':[]}
    finally: await runner.close()


def make_mcp(coordinator, *, stdio_session=None):
    async def list_tools(ctx, params):
        return types.ListToolsResult(tools=[types.Tool(name=tool['name'],description=tool['description'],inputSchema=tool['inputSchema'],annotations=types.ToolAnnotations(readOnlyHint=not tool['mutation'],destructiveHint=tool['policy'] in {'D','H'},idempotentHint=False,openWorldHint=True)) for tool in CATALOG.values()])
    async def call_tool(ctx, params):
        # Legacy initialized MCP sessions use the SDK-issued opaque session ID.
        # Modern stateless clients must supply a stable client ID header to keep
        # selection; without one every call gets a fresh isolated session.
        headers=getattr(ctx.request,'headers',{})
        identity=stdio_session or headers.get('mcp-session-id') or headers.get('x-tapmesh-client-id') or getattr(getattr(ctx.request,'state',None),'tapmesh_client',None)
        if not identity: identity=uuid.uuid4().hex
        session='mcp:'+hashlib.sha256(identity.encode()).hexdigest()
        try: await ctx.session.report_progress(0,1,message='Routing device operation')
        except Exception: pass
        result=await coordinator.call(session,params.name,params.arguments or {})
        content=[]
        data=result.get('data',{})
        if isinstance(data,dict) and 'image' in data:
            image=data['image']; content.append(types.ImageContent(type='image',data=image['data'],mimeType=image['mime_type']))
        # The image is already emitted as MCP ImageContent; avoid duplicating its
        # base64 payload in human-readable JSON while retaining structured data.
        summary=json.loads(json.dumps(result))
        if isinstance(summary.get('data'),dict) and 'image' in summary['data']: summary['data']['image']={'mime_type':summary['data']['image']['mime_type'],'included_as':'MCP image content'}
        content.insert(0,types.TextContent(type='text',text=json.dumps(summary,separators=(',',':'))))
        try: await ctx.session.report_progress(1,1,message='Operation finished')
        except Exception: pass
        return types.CallToolResult(content=content,structuredContent=summary,isError=result['status']=='error' or result['status']=='partial')
    return Server('TapMesh',version=__version__,instructions='Select a device explicitly for this client. Observe before input. Resolve local approvals through the native app. Delivery never proves business success.',on_list_tools=list_tools,on_call_tool=call_tool,get_tool_input_schema=lambda name:CATALOG.get(name,{}).get('inputSchema'))


class AuthMiddleware:
    def __init__(self, app, token, processor_token=None): self.app,self.token,self.processor_token=app,token,processor_token
    async def __call__(self, scope, receive, send):
        if scope['type']=='http' and scope.get('path')!='/health':
            request=Request(scope)
            internal=scope.get('path') in {'/internal/ocr','/internal/ocr/health'}
            expected=self.processor_token if internal else self.token
            if not expected or not authenticates(request.headers.get('authorization'),expected):
                await JSONResponse({'error':'unauthorized'},status_code=401,headers={'WWW-Authenticate':'Bearer'})(scope,receive,send); return
            length=request.headers.get('content-length')
            if length and (not length.isdigit() or int(length)>(12*1024*1024 if internal else 1024*1024)):
                await JSONResponse({'error':'request_too_large'},status_code=413)(scope,receive,send); return
        if scope['type']=='http' and scope.get('path','').startswith('/mcp'):
            request=Request(scope)
            cookie=request.cookies.get('tapmesh_client','')
            fresh=not re.fullmatch('[0-9a-f]{32}',cookie)
            if fresh: cookie=uuid.uuid4().hex
            scope.setdefault('state',{})['tapmesh_client']=cookie
            original_send=send
            async def send_cookie(message):
                if fresh and message['type']=='http.response.start':
                    message.setdefault('headers',[]).append((b'set-cookie',('tapmesh_client='+cookie+'; Path=/mcp; HttpOnly; SameSite=Strict').encode()))
                await original_send(message)
            send=send_cookie
        await self.app(scope,receive,send)


def create_app(*, state_dir=None, token=None, agent_client=None, processor_token=None):
    state_dir=state_dir or os.environ.get('TAPMESH_STATE_DIR','/var/lib/tapmesh')
    token=token or read_secret(os.environ.get('TAPMESH_SERVER_TOKEN_FILE'))
    owns_client=agent_client is None
    if agent_client is None:
        agent_token=read_secret(os.environ.get('TAPMESH_AGENT_TOKEN_FILE'))
        processor_token=processor_token or agent_token
        url=os.environ.get('TAPMESH_AGENT_URL','http://127.0.0.1:48792')
        from urllib.parse import urlsplit
        parsed=urlsplit(url)
        if parsed.scheme!='http' or parsed.hostname not in {'127.0.0.1','localhost','::1'} or parsed.username or parsed.password: raise RuntimeError('Agent URL must use loopback HTTP through the verified SSH tunnel')
        agent_client=httpx.AsyncClient(base_url=url,headers={'Authorization':'Bearer '+agent_token},limits=httpx.Limits(max_connections=16,max_keepalive_connections=8),timeout=40,follow_redirects=False,trust_env=False)
    coordinator=Coordinator(state_dir,agent_client); mcp=make_mcp(coordinator)
    mcp_app=mcp.streamable_http_app(streamable_http_path='/mcp',json_response=True,session_idle_timeout=1800,max_sessions=128,max_request_body_size=1024*1024,host='127.0.0.1',transport_security=TransportSecuritySettings(enable_dns_rebinding_protection=True,allowed_hosts=['127.0.0.1:*','localhost:*','[::1]:*','testserver'],allowed_origins=['http://127.0.0.1:*','http://localhost:*']))
    async def health(request): return JSONResponse({'status':'ok','version':__version__,'service':'TapMesh coordinator','agent_transport':'loopback','instance_id':os.environ.get('TAPMESH_INSTANCE_ID')})
    async def tools(request): return JSONResponse({'version':__version__,'tools':public_catalog()})
    async def devices(request):
        session=request.query_params.get('session_id','native-discovery')
        return JSONResponse(await coordinator.call(session,'list_devices',{'include_all_simulators':request.query_params.get('include_all_simulators')=='true'}))
    async def call(request):
        try:
            payload=await request_json(request,{'session_id','tool','arguments'},{'session_id','tool','arguments'})
            if not isinstance(payload['tool'],str) or not isinstance(payload['arguments'],dict): raise BridgeError('invalid_arguments','Invalid tool or arguments type')
            return JSONResponse(await run_connected(request,coordinator.call(payload['session_id'],payload['tool'],payload['arguments'])))
        except BridgeError as exc: return JSONResponse({'error':exc.public()},status_code=400)
    async def approvals(request):
        local=request.headers.get('x-devicebridge-local-authorization')
        if not local: return JSONResponse({'error':'local_authorization_required'},status_code=403)
        try:
            path='/v1/approvals'+('/'+request.path_params['approval_id'] if 'approval_id' in request.path_params else '')
            headers={'X-DeviceBridge-Local-Authorization':local}
            if request.method=='POST':
                body=await request_json(request,{'decision'},{'decision'})
                response=await agent_client.post(path,headers=headers,json=body)
            else: response=await agent_client.get(path,headers=headers)
            return JSONResponse(response.json(),status_code=response.status_code)
        except (httpx.HTTPError,ValueError): return JSONResponse({'error':'agent_unavailable'},status_code=502)
        except BridgeError as exc: return JSONResponse({'error':exc.public()},status_code=400)
    async def takeover(request):
        local=request.headers.get('x-devicebridge-local-authorization')
        if not local: return JSONResponse({'error':'local_authorization_required'},status_code=403)
        try:
            payload=await request_json(request,{'session_id','device_id'},{'session_id','device_id'})
            response=await agent_client.post('/v1/takeover',headers={'X-DeviceBridge-Local-Authorization':local},json=payload,timeout=30)
            return JSONResponse(response.json(),status_code=response.status_code)
        except (httpx.HTTPError,ValueError): return JSONResponse({'error':'agent_unavailable'},status_code=502)
        except BridgeError as exc: return JSONResponse({'error':exc.public()},status_code=400)
    async def artifact(request):
        try:
            path=coordinator.artifacts.path(request.path_params['artifact_id'])
            return TrackedFileResponse(path,tracker=coordinator,filename=path.name,headers={'Cache-Control':'no-store'})
        except BridgeError as exc: return JSONResponse({'error':exc.public()},status_code=404)
    async def history(request): return JSONResponse({'activity':coordinator.history()})
    async def update(request):
        local=request.headers.get('x-devicebridge-local-authorization')
        if not local: return JSONResponse({'error':'local_authorization_required'},status_code=403)
        headers={'X-DeviceBridge-Local-Authorization':local}
        try:
            payload=await request_json(request,{'action','lease_id'},{'action','lease_id'})
            lease=update_lease(payload)
            if payload['action'] not in {'prepare','resume'}: raise BridgeError('invalid_arguments','Unknown update coordination action')
            response=await agent_client.post('/v1/update',headers=headers,json=payload,timeout=5)
            if response.status_code!=200: return JSONResponse(response.json(),status_code=response.status_code)
            if payload['action']=='resume':
                coordinator.resume_after_update(lease)
                return JSONResponse({'status':'resumed'})
            try: result=coordinator.prepare_update(lease)
            except BridgeError:
                await agent_client.post('/v1/update',headers=headers,json={'action':'resume','lease_id':lease},timeout=5)
                raise
            return JSONResponse(result)
        except BridgeError as exc:
            return JSONResponse({'error':exc.public()},status_code=409 if exc.code=='overloaded' else 400)
        except (httpx.HTTPError,ValueError): return JSONResponse({'error':'agent_unavailable'},status_code=502)
    async def processor_health(request):
        return JSONResponse(await ocr_health(),headers={'Cache-Control':'no-store'})
    async def processor(request):
        try:
            body=bytearray()
            async for chunk in request.stream():
                if len(body)+len(chunk)>12*1024*1024:
                    return JSONResponse({'error':'request_too_large'},status_code=413)
                body.extend(chunk)
            try: payload=json.loads(body,parse_constant=lambda _: (_ for _ in ()).throw(ValueError()))
            except (ValueError,UnicodeDecodeError): raise BridgeError('invalid_arguments','Invalid OCR JSON document')
            if (not isinstance(payload,dict) or set(payload)!={'image_base64','logical_size'}
                    or not isinstance(payload['image_base64'],str) or not isinstance(payload['logical_size'],dict)):
                raise BridgeError('invalid_arguments','Invalid closed OCR request')
            try: image=base64.b64decode(payload['image_base64'],validate=True)
            except (ValueError,UnicodeError): raise BridgeError('invalid_arguments','Invalid OCR image encoding')
            if len(image)>8*1024*1024: raise BridgeError('invalid_arguments','OCR image exceeds 8 MiB')
            result=await run_connected(request,recognize_ocr(image,logical_size=payload['logical_size']))
            return JSONResponse(result,headers={'Cache-Control':'no-store'})
        except BridgeError as exc:
            status=400 if exc.code=='invalid_arguments' else 503 if exc.code=='prerequisite_required' else 504 if exc.code=='deadline_exceeded' else 502
            return JSONResponse({'error':exc.public()},status_code=status,headers={'Cache-Control':'no-store'})
    @asynccontextmanager
    async def lifespan(app):
        async with mcp.session_manager.run(): yield
        await coordinator.close()
        if owns_client: await agent_client.aclose()
    routes=[Route('/health',health),Route('/internal/ocr/health',processor_health),Route('/internal/ocr',processor,methods=['POST']),Route('/api/tools',tools),Route('/api/devices',devices),Route('/api/call',call,methods=['POST']),Route('/api/takeover',takeover,methods=['POST']),Route('/api/approvals',approvals),Route('/api/approvals/{approval_id}',approvals,methods=['POST']),Route('/api/artifacts/{artifact_id}',artifact),Route('/api/history',history),Route('/api/update',update,methods=['POST']),Mount('/',app=mcp_app)]
    app=Starlette(routes=routes,lifespan=lifespan); app.state.coordinator=coordinator; app.state.mcp=mcp
    app.add_middleware(AuthMiddleware,token=token,processor_token=processor_token)
    return app


def main():
    import uvicorn
    fd=int(os.environ['TAPMESH_LISTEN_FD']) if 'TAPMESH_LISTEN_FD' in os.environ else None
    uvicorn.run(create_app(),host='127.0.0.1',port=int(os.environ.get('TAPMESH_SERVER_PORT','48793')),fd=fd,access_log=False,log_level='warning',timeout_graceful_shutdown=5)


if __name__=='__main__': main()
