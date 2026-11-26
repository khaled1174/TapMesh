import os
import asyncio
import hashlib
import time
import httpx
import pytest


def test_artifact_finalizes_hash_and_rejects_untrusted_id(tmp_path):
    from agent.artifacts import Artifacts
    from agent.safety import BridgeError
    a=Artifacts(tmp_path)
    artifact=a.write(b'hello','screenshot','.png','sim:1',2)
    assert artifact['sha256']=='2cf24dba5fb0a30e26e83b2ac5b9e29e1b161e5c1fa7425e73043362938b9824'
    assert a.path(artifact['artifact_id']).read_bytes()==b'hello'
    with pytest.raises(BridgeError): a.path('../outside')
    assert Artifacts(tmp_path).path(artifact['artifact_id']).read_bytes()==b'hello'


def test_prune_deletes_old_files_first(tmp_path):
    from agent.artifacts import Artifacts

    store = Artifacts(tmp_path)
    now = time.time()
    ids = [store.write(b'asset', 'screenshot', '.png')['artifact_id'] for _ in range(3)]
    for aid, days in zip(ids, (40, 20, 1)):
        path = store.path(aid)
        os.utime(path, (now - days * 86400,) * 2)

    assert store.prune(now=now) == {'deleted': 1, 'bytes_freed': 5}
    assert store.usage() == {'count': 2, 'bytes': 10}
    assert ids[0] not in Artifacts(tmp_path).records
    assert all(store.path(aid).exists() for aid in ids[1:])


def test_prune_enforces_the_size_cap_oldest_first(tmp_path):
    from agent.artifacts import Artifacts

    store = Artifacts(tmp_path)
    now = time.time()
    ids = [store.write(b'x', 'screenshot', '.png')['artifact_id'] for _ in range(3)]
    two_gib = 2 * 1024**3
    for offset, aid in enumerate(ids):
        path = store.path(aid)
        with path.open('r+b') as file:
            file.truncate(two_gib)
        os.utime(path, (now - 30 + offset,) * 2)

    assert store.prune(now=now) == {'deleted': 1, 'bytes_freed': two_gib}
    assert store.record(ids[0]) is None
    assert all(store.path(aid).stat().st_size == two_gib for aid in ids[1:])


def test_clear_removes_owned_files_and_keeps_exported_copies(tmp_path):
    from agent.artifacts import Artifacts

    exports = tmp_path / 'exports'
    exports.mkdir()
    store = Artifacts(tmp_path / 'saved', [exports])
    store.write(b'copy', 'screenshot', '.png', destination=exports, filename='export.png')
    store.write(b'two', 'screenshot', '.png')

    assert store.clear() == {'deleted': 2, 'bytes_freed': 7}
    assert store.usage() == {'count': 0, 'bytes': 0}
    assert list(store.root.iterdir()) == []
    assert (exports / 'export.png').read_bytes() == b'copy'


def test_export_conflict_leaves_no_untracked_copy(tmp_path):
    from agent.artifacts import Artifacts
    from agent.safety import BridgeError

    exports = tmp_path / 'exports'
    exports.mkdir()
    destination = exports / 'export.png'
    destination.write_bytes(b'previous export')
    store = Artifacts(tmp_path / 'saved', [exports])

    with pytest.raises(BridgeError) as failure:
        store.write(b'new export', 'screenshot', '.png', destination=exports, filename=destination.name)

    assert failure.value.code == 'artifact_conflict'
    assert list(store.root.iterdir()) == []
    assert destination.read_bytes() == b'previous export'


async def test_clear_requires_owner_token_and_removes_both_stores_idempotently(tmp_path):
    from agent.app import create_app as agent_app
    from agent.core import Router
    from server.app import create_app as server_app
    from test_router import DeviceFixture

    agent_token,server_token,owner_token='a'*64,'s'*64,'l'*64
    approvals={'X-DeviceBridge-Local-Authorization':'Bearer '+owner_token}
    router=Router(DeviceFixture(),state_dir=tmp_path/'agent')
    agent=agent_app(router,token=agent_token,approval_token=owner_token)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=agent),base_url='http://agent',headers={'Authorization':'Bearer '+agent_token}) as upstream:
        server=server_app(state_dir=tmp_path/'server',token=server_token,agent_client=upstream)
        coordinator=server.state.coordinator
        try:
            router.artifacts.write(b'mac','screenshot','.png')
            coordinator.artifacts.write(b'local-copy','screenshot','.png')
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=server),base_url='http://testserver',headers={'Authorization':'Bearer '+server_token}) as client:
                assert (await client.get('/api/storage')).json()=={'count':1,'bytes':10}
                assert (await upstream.post('/v1/storage/clear')).status_code==403
                assert (await client.post('/api/storage/clear')).status_code==403
                assert (await client.post('/api/storage/clear',headers={'X-DeviceBridge-Local-Authorization':'Bearer '+'w'*64})).status_code==403
                assert router.artifacts.usage()=={'count':1,'bytes':3}
                assert (await client.get('/api/storage')).json()=={'count':1,'bytes':10}

                response=await client.post('/api/storage/clear',headers=approvals)
                assert response.status_code==200 and response.json()=={'deleted':1,'bytes_freed':10}
                assert router.artifacts.usage()=={'count':0,'bytes':0}
                assert (await client.get('/api/storage')).json()=={'count':0,'bytes':0}
                assert (await client.post('/api/storage/clear',headers=approvals)).json()=={'deleted':0,'bytes_freed':0}
        finally:
            await coordinator.close()
            await router.close()


async def test_transfer_does_not_publish_after_saved_files_are_cleared(tmp_path):
    from server.coordinator import Coordinator

    started,finish=asyncio.Event(),asyncio.Event()
    content=b'screenshot-contents'
    artifact={'artifact_id':'a'*32,'kind':'screenshot','state':'complete','size_bytes':len(content),'sha256':hashlib.sha256(content).hexdigest()}

    class PendingFile(httpx.AsyncByteStream):
        async def __aiter__(self):
            started.set()
            await finish.wait()
            yield content

    async def agent(request):
        if request.url.path=='/v1/storage/clear':
            return httpx.Response(200,json={'deleted':1,'bytes_freed':len(content)})
        if request.url.path.startswith('/v1/artifacts/'):
            return httpx.Response(200,stream=PendingFile())
        return httpx.Response(200,json={'request_id':'test','status':'success','data':{'artifact':artifact}})

    async with httpx.AsyncClient(transport=httpx.MockTransport(agent),base_url='http://agent') as client:
        coordinator=Coordinator(tmp_path,client)
        pending=asyncio.create_task(coordinator.call('owner','save_screenshot',{}))
        try:
            await asyncio.wait_for(started.wait(),1)
            await asyncio.wait_for(coordinator.clear_storage(local_authorization='Bearer token'),1)
            finish.set()
            result=await asyncio.wait_for(pending,1)
            assert result['status']=='partial'
            assert result['data']['artifact']['storage']=='transfer_failed'
            assert 'download_url' not in result['data']['artifact']
            assert coordinator.artifacts.usage()=={'count':0,'bytes':0}
            assert not list(coordinator.artifacts.root.iterdir())
        finally:
            finish.set()
            await asyncio.gather(pending,return_exceptions=True)
            await coordinator.close()


async def test_missing_cached_artifact_is_refetched_instead_of_returning_a_dead_path(tmp_path):
    from server.coordinator import Coordinator

    body=b'recording'
    artifact={'artifact_id':'b'*32,'kind':'recording','state':'complete','size_bytes':len(body),'sha256':hashlib.sha256(body).hexdigest()}
    transfers=[]

    async def agent(request):
        if request.url.path.startswith('/v1/artifacts/'):
            transfers.append(request.url.path)
            return httpx.Response(200,content=body)
        return httpx.Response(200,json={'request_id':'test','status':'success','data':{'artifact':artifact}})

    async with httpx.AsyncClient(transport=httpx.MockTransport(agent),base_url='http://agent') as client:
        coordinator=Coordinator(tmp_path,client)
        try:
            one=await coordinator.call('owner','save_screenshot',{})
            aid=one['data']['artifact']['artifact_id']
            coordinator.artifacts.path(aid).unlink()
            two=await coordinator.call('owner','save_screenshot',{})
            replacement=two['data']['artifact']['artifact_id']
            assert replacement!=aid and coordinator.artifacts.path(replacement).read_bytes()==body
            assert len(transfers)==2
        finally:await coordinator.close()


async def test_startup_prunes_only_old_owned_derived_build_directories(tmp_path):
    from agent.backends import MacBackend

    profile=tmp_path/'profile'
    builds=profile/'builds'
    exports=tmp_path/'exports'
    old=builds/('a'*32)
    fresh=builds/('b'*32)
    unmanaged=builds/'imported-project'
    link=builds/('c'*32)
    for path in (old,fresh,unmanaged,exports):path.mkdir(parents=True)
    (exports/'keep').write_bytes(b'copy')
    link.symlink_to(exports,target_is_directory=True)
    age=(time.time()-8*86400,)*2
    for path in (old,unmanaged,exports):os.utime(path,age)

    backend=MacBackend(profile)
    try:
        assert not old.exists()
        assert fresh.is_dir() and unmanaged.is_dir() and link.is_symlink()
        assert (exports/'keep').read_bytes()==b'copy'
    finally:await backend.close()

    linked_profile=tmp_path/'linked-profile'
    linked_profile.mkdir()
    (linked_profile/'builds').symlink_to(exports,target_is_directory=True)
    candidate=exports/('d'*32)
    candidate.mkdir()
    os.utime(candidate,age)
    backend=MacBackend(linked_profile)
    try:assert candidate.is_dir()
    finally:await backend.close()
