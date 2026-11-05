import asyncio
import pytest
from starlette.testclient import TestClient
from agent.core import Router
from agent.app import create_app
from agent.safety import BridgeError
from test_router import DeviceFixture


@pytest.mark.asyncio
async def test_update_cannot_interrupt_another_clients_active_work_or_recording(tmp_path):
    backend=DeviceFixture()
    router=Router(backend,state_dir=tmp_path)
    active=asyncio.create_task(router.call('external-mcp','wait',{'duration_ms':1000}))
    await asyncio.sleep(0)
    with pytest.raises(BridgeError,match='active device work'):
        router.prepare_update('owner-one')
    active.cancel(); await active
    backend.recordings={'external':{'final':None}}
    with pytest.raises(BridgeError): router.prepare_update('owner-one')
    backend.recordings['external']['final']={'status':'success'}
    assert router.prepare_update('owner-one')['status']=='prepared'
    with pytest.raises(BridgeError): router.resume_after_update('other-owner')
    rejected=await router.call('new-mcp','list_devices',{})
    assert rejected['error']['code']=='overloaded'
    assert rejected['error']['side_effect_state']=='not_sent'
    assert not router.session_active
    assert router.resume_after_update('owner-one')=={'status':'resumed'}
    assert (await router.call('new-mcp','list_devices',{}))['status']=='success'
    await router.close()


def test_update_intake_gate_requires_private_owner_authorization(tmp_path):
    router=Router(DeviceFixture(),state_dir=tmp_path)
    app=create_app(router,token='a'*64,approval_token='b'*64)
    with TestClient(app) as client:
        assert client.post('/v1/update',json={'action':'prepare'}).status_code==401
        headers={'Authorization':'Bearer '+'a'*64}
        assert client.post('/v1/update',headers=headers,json={'action':'prepare'}).status_code==403
        headers['X-DeviceBridge-Local-Authorization']='Bearer '+'b'*64
        assert client.post('/v1/update',headers=headers,json={'action':'unknown'}).status_code==400
        assert client.post('/v1/update',headers=headers,json={'action':'prepare','lease_id':'owner-one'}).json()['status']=='prepared'
        assert client.post('/v1/update',headers=headers,json={'action':'resume','lease_id':'owner-one'}).json()=={'status':'resumed'}


@pytest.mark.asyncio
async def test_coordinator_update_waits_for_final_artifact_transfer(tmp_path):
    import hashlib
    import httpx
    from server.coordinator import Coordinator
    content=b'owned artifact'
    entered=asyncio.Event(); release=asyncio.Event()
    async def upstream(request):
        if request.url.path=='/v1/call':
            return httpx.Response(200,json={'request_id':'test','status':'success','data':{'artifact':{
                'artifact_id':'a'*32,'sha256':hashlib.sha256(content).hexdigest(),'size_bytes':len(content),
                'state':'complete','kind':'screenshot','device_id':'sim:owned','connection_epoch':1,'local_path':'/private/test.png'}}})
        entered.set(); await release.wait()
        return httpx.Response(200,content=content)
    async with httpx.AsyncClient(transport=httpx.MockTransport(upstream),base_url='http://127.0.0.1') as client:
        coordinator=Coordinator(tmp_path,client)
        request=asyncio.create_task(coordinator.call('external','save_screenshot',{}))
        await entered.wait()
        with pytest.raises(BridgeError): coordinator.prepare_update('owner-one')
        release.set(); assert (await request)['status']=='success'
        assert coordinator.prepare_update('owner-one')['status']=='prepared'
        assert (await coordinator.call('new-client','list_devices',{}))['error']['code']=='overloaded'
        with pytest.raises(BridgeError): coordinator.resume_after_update('other-owner')
        coordinator.resume_after_update('owner-one')
        await coordinator.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('kind',['agent','coordinator'])
async def test_download_response_stays_owned_through_last_body_and_obeys_update_gate(tmp_path,kind):
    import httpx
    from server.coordinator import Coordinator
    from agent.http_files import TrackedFileResponse
    path=tmp_path/'download.bin'; path.write_bytes(b'owned bytes'*20000)
    client=httpx.AsyncClient()
    tracker=Router(DeviceFixture(),state_dir=tmp_path/'agent') if kind=='agent' else Coordinator(tmp_path/'server',client)
    entered=asyncio.Event(); release=asyncio.Event(); messages=[]
    async def send(message):
        messages.append(message)
        if message['type']=='http.response.body':
            entered.set(); await release.wait()
    async def receive():
        await asyncio.Event().wait()  # ASGI waits for a future disconnect.
        return {'type':'http.disconnect'}
    response=TrackedFileResponse(path,tracker=tracker)
    request=asyncio.create_task(response({'type':'http','method':'GET','headers':[],'extensions':{}},receive,send))
    try:
        await asyncio.wait_for(entered.wait(),2)
        assert tracker.active_downloads==1
        with pytest.raises(BridgeError): tracker.prepare_update('owner-one')
        release.set(); await request
        assert tracker.active_downloads==0
        tracker.prepare_update('owner-one')
        messages.clear()
        await response({'type':'http','method':'GET','headers':[],'extensions':{}},receive,send)
        assert messages[0]['status']==503
        assert b'owned bytes' not in b''.join(message.get('body',b'') for message in messages)
    finally:
        release.set(); await request
        await tracker.close(); await client.aclose()


@pytest.mark.asyncio
async def test_cancelled_artifact_finalizer_remains_tracked_until_its_thread_settles(tmp_path,monkeypatch):
    import hashlib
    import threading
    import httpx
    from server.coordinator import Coordinator
    content=b'owned finalization'
    async def upstream(request):
        if request.url.path=='/v1/call':
            return httpx.Response(200,json={'request_id':'thread','status':'success','data':{'artifact':{
                'artifact_id':'b'*32,'sha256':hashlib.sha256(content).hexdigest(),'size_bytes':len(content),
                'state':'complete','kind':'screenshot','device_id':'sim:owned','connection_epoch':1,'local_path':'/private/test.png'}}})
        return httpx.Response(200,content=content)
    entered=threading.Event(); release=threading.Event()
    async with httpx.AsyncClient(transport=httpx.MockTransport(upstream),base_url='http://127.0.0.1') as client:
        tracker=Coordinator(tmp_path,client)
        original=tracker.artifacts.finalize
        def finalize(*args,**kwargs):
            entered.set()
            if not release.wait(5): raise RuntimeError('Test finalizer deadline')
            return original(*args,**kwargs)
        monkeypatch.setattr(tracker.artifacts,'finalize',finalize)
        request=asyncio.create_task(tracker.call('external','save_screenshot',{}))
        try:
            assert await asyncio.to_thread(entered.wait,2)
            request.cancel(); await asyncio.sleep(0)
            assert not request.done()
            with pytest.raises(BridgeError): tracker.prepare_update('owner-one')
            release.set()
            with pytest.raises(asyncio.CancelledError): await request
            assert tracker.active_requests==0
            assert tracker.prepare_update('owner-one')['status']=='prepared'
        finally:
            release.set()
            if not request.done():
                try: await request
                except asyncio.CancelledError: pass
            await tracker.close()
