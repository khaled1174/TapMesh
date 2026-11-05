from starlette.testclient import TestClient
import httpx
import pytest


def test_server_same_router_and_catalog_requires_bearer(tmp_path):
    from server.app import create_app
    async def agent(request):
        if request.url.path=='/v1/call':
            data=__import__('json').loads(request.content)
            return httpx.Response(200,json={'request_id':'r','status':'success','data':{'devices':[],'selected_device_id':None}})
        return httpx.Response(404)
    upstream=httpx.AsyncClient(transport=httpx.MockTransport(agent),base_url='http://127.0.0.1:48792')
    app=create_app(state_dir=tmp_path,token='a'*64,agent_client=upstream)
    with TestClient(app) as client:
        assert client.get('/health').status_code==200
        assert client.get('/api/tools').status_code==401
        assert client.post('/mcp',json={}).status_code==401
        headers={'Authorization':'Bearer '+'a'*64}
        catalog=client.get('/api/tools',headers=headers).json()
        assert len(catalog['tools'])==50
        assert client.get('/api/devices',headers=headers).json()['status']=='success'
        initialized=client.post('/mcp',headers={**headers,'Accept':'application/json, text/event-stream'},json={'jsonrpc':'2.0','id':1,'method':'initialize','params':{'protocolVersion':'2025-11-25','capabilities':{},'clientInfo':{'name':'test','version':'1'}}})
        assert initialized.status_code==200,initialized.text
        session=initialized.headers['mcp-session-id']
        h={**headers,'Accept':'application/json, text/event-stream','MCP-Protocol-Version':'2025-11-25','Mcp-Session-Id':session}
        assert client.post('/mcp',headers=h,json={'jsonrpc':'2.0','method':'notifications/initialized'}).status_code==202
        listed=client.post('/mcp',headers=h,json={'jsonrpc':'2.0','id':2,'method':'tools/list','params':{}})
        assert len(listed.json()['result']['tools'])==50
        called=client.post('/mcp',headers=h,json={'jsonrpc':'2.0','id':3,'method':'tools/call','params':{'name':'list_devices','arguments':{}}})
        assert called.json()['result']['isError'] is False
        assert called.json()['result']['structuredContent']['status']=='success'
        client.delete('/mcp',headers=h)
