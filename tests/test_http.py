from pathlib import Path
import pytest
from starlette.testclient import TestClient


def test_agent_authentication_and_approvals_are_separate(tmp_path):
    from agent.app import create_app
    from agent.core import Router
    from test_router import DeviceFixture
    token='a'*64; local='b'*64
    app=create_app(Router(DeviceFixture(),state_dir=tmp_path),token=token,approval_token=local)
    with TestClient(app) as client:
        assert client.get('/health').status_code==200
        assert client.post('/v1/call',json={'session_id':'a','tool':'list_devices','arguments':{}}).status_code==401
        headers={'Authorization':'Bearer '+token}
        assert client.post('/v1/call',headers=headers,json={'session_id':'a','tool':'list_devices','arguments':{}}).json()['status']=='success'
        assert client.get('/v1/approvals',headers=headers).status_code==403
        headers['X-DeviceBridge-Local-Authorization']='Bearer '+local
        assert client.get('/v1/approvals',headers=headers).json()=={'approvals':[]}
        assert client.post('/v1/call',headers=headers,json={'session_id':'a','tool':'wait','arguments':{'duration_ms':0},'bypass':True}).status_code==400


def test_nas_relay_persists_finalized_artifact_and_hides_mac_path(tmp_path):
    import httpx
    from server.coordinator import Coordinator
    import asyncio, hashlib
    content=b'fixture artifact'
    sha=hashlib.sha256(content).hexdigest()
    async def route(request):
        if request.url.path=='/v1/call': return httpx.Response(200,json={'request_id':'r','status':'success','data':{'artifact':{'artifact_id':'a'*32,'kind':'screenshot','state':'complete','local_path':'/private/mac.png','size_bytes':len(content),'sha256':sha,'created_at':'now','device_id':'sim:1','connection_epoch':1}}})
        if request.url.path=='/v1/artifacts/'+'a'*32: return httpx.Response(200,content=content)
        return httpx.Response(404)
    async def verify():
        async with httpx.AsyncClient(transport=httpx.MockTransport(route),base_url='http://127.0.0.1:48792') as client:
            service=Coordinator(tmp_path,client)
            result=await service.call('s','save_screenshot',{})
            artifact=result['data']['artifact']
            assert artifact['storage']=='server'
            assert artifact['local_path']!='/private/mac.png'
            assert service.artifacts.path(artifact['artifact_id']).read_bytes()==content
            assert service.history()[0]['tool']=='save_screenshot'
    asyncio.run(verify())
