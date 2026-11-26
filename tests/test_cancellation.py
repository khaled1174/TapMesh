import asyncio
import socket
import sys
import httpx
import pytest
import uvicorn
from test_router import DeviceFixture


class ConnectedRequest:
    async def is_disconnected(self):
        return False


@pytest.mark.parametrize('fails', [False, True])
async def test_completed_operation_returns_without_a_disconnect_poll(monkeypatch, fails):
    from agent import app

    monkeypatch.setattr(app, 'DISCONNECT_POLL_SECONDS', 3600)
    result, error = object(), ValueError('Operation failed')

    async def immediate():
        if fails:
            raise error
        return result

    async with asyncio.timeout(2):
        if fails:
            with pytest.raises(ValueError) as raised:
                await app.run_connected(ConnectedRequest(), immediate())
            assert raised.value is error
        else:
            assert await app.run_connected(ConnectedRequest(), immediate()) is result


async def test_disconnect_waits_for_operation_cleanup_and_preserves_its_result(monkeypatch):
    from agent import app

    monkeypatch.setattr(app, 'DISCONNECT_POLL_SECONDS', 0)
    cleaning, release = asyncio.Event(), asyncio.Event()
    cancelled_result = object()

    class DisconnectedRequest:
        async def is_disconnected(self):
            return True

    async def operation():
        try:
            await asyncio.Future()
        except asyncio.CancelledError:
            cleaning.set()
            await release.wait()
            return cancelled_result

    async with asyncio.timeout(2), asyncio.TaskGroup() as tasks:
        call = tasks.create_task(app.run_connected(DisconnectedRequest(), operation()))
        await cleaning.wait()
        assert not call.done()
        release.set()
    assert call.result() is cancelled_result


async def test_repeated_request_cancellation_does_not_interrupt_operation_cleanup(monkeypatch):
    from agent import app

    monkeypatch.setattr(app, 'DISCONNECT_POLL_SECONDS', 3600)
    entered, cleaning, release = asyncio.Event(), asyncio.Event(), asyncio.Event()
    cleaned = False

    async def operation():
        nonlocal cleaned
        try:
            entered.set()
            await asyncio.Future()
        finally:
            cleaning.set()
            await release.wait()
            cleaned = True

    async with asyncio.timeout(2), asyncio.TaskGroup() as tasks:
        call = tasks.create_task(app.run_connected(ConnectedRequest(), operation()))
        await entered.wait()
        call.cancel()
        await cleaning.wait()
        call.cancel()
        await asyncio.sleep(0)
        assert not call.done()
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await call
        assert cleaned


async def test_real_http_disconnect_cascades_through_nas_agent_and_child(tmp_path):
    from agent.app import create_app as agent_app
    from server.app import create_app as server_app
    from agent.core import Router
    from agent.process import Runner
    class ChildBackend(DeviceFixture):
        def __init__(self): super().__init__(); self.runner=Runner()
        async def invoke(self,tool,device,args,context):
            await self.runner.run([sys.executable,'-c','import time; time.sleep(60)'],timeout=90,mutation=True)
            return {'delivery':'confirmed'}
        async def close(self): await self.runner.close()
    backend=ChildBackend(); router=Router(backend,state_dir=tmp_path/'agent')
    sockets=[]; servers=[]; tasks=[]
    def listen():
        sock=socket.socket(); sock.bind(('127.0.0.1',0)); sock.listen(); sockets.append(sock)
        return sock
    agent_socket=listen(); nas_socket=listen()
    agent_url='http://127.0.0.1:'+str(agent_socket.getsockname()[1]); nas_url='http://127.0.0.1:'+str(nas_socket.getsockname()[1])
    upstream=httpx.AsyncClient(base_url=agent_url,headers={'Authorization':'Bearer '+'a'*64})
    apps=[agent_app(router,token='a'*64,approval_token='b'*64),server_app(state_dir=tmp_path/'server',token='c'*64,agent_client=upstream)]
    try:
        for app,sock in zip(apps,sockets):
            server=uvicorn.Server(uvicorn.Config(app,log_level='critical',access_log=False)); servers.append(server)
            tasks.append(asyncio.create_task(server.serve(sockets=[sock])))
        async with asyncio.timeout(5):
            while not all(s.started for s in servers): await asyncio.sleep(.01)
        async with httpx.AsyncClient(base_url=nas_url,headers={'Authorization':'Bearer '+'c'*64},timeout=90) as client:
            payload={'session_id':'s','tool':'launch_app','arguments':{'device_id':'sim:fixture','bundle_id':'io.tapmesh.fixture'}}
            pending=(await client.post('/api/call',json=payload)).json()
            router.approvals.decide(pending['error']['details']['approval_id'],'allow')
            call=asyncio.create_task(client.post('/api/call',json=payload))
            async with asyncio.timeout(5):
                while not backend.runner.processes: await asyncio.sleep(.01)
            call.cancel()
            with pytest.raises(asyncio.CancelledError): await call
            async with asyncio.timeout(5):
                while backend.runner.processes: await asyncio.sleep(.01)
            assert not backend.runner.processes
    finally:
        for server in servers: server.should_exit=True
        await asyncio.gather(*tasks,return_exceptions=True)
        await upstream.aclose()
        for sock in sockets: sock.close()
