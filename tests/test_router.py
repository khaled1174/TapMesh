import asyncio
import pytest
from Contracts.catalog import CATALOG


class DeviceFixture:
    def __init__(self):
        self.calls=[]; self.concurrent=0; self.max_concurrent=0
    async def discover(self, include_all=False):
        return [{'device_id':'sim:fixture','backend':'simulator','native_id':'fixture','name':'Fixture','ready':True,'connection_epoch':1,'capabilities':{t['capability']:{'status':'supported'} for t in CATALOG.values()}}]
    async def invoke(self, tool, device, args, context):
        self.calls.append((tool,args))
        if tool=='list_elements':
            return {'logical_size':{'width':100,'height':200},'elements':[{'label':'Save','role':'Button','bounds':{'x':0,'y':0,'width':10,'height':10},'source':'accessibility'},{'label':'Save','role':'Button','bounds':{'x':20,'y':0,'width':10,'height':10},'source':'accessibility'}],'source':'accessibility'}
        self.concurrent+=1; self.max_concurrent=max(self.max_concurrent,self.concurrent)
        await asyncio.sleep(.02)
        self.concurrent-=1
        return {'delivery':'confirmed','completion_condition':'Fixture observed'}
    async def close(self): pass


@pytest.fixture
async def router(tmp_path):
    from agent.core import Router
    fixture=DeviceFixture()
    r=Router(fixture, state_dir=tmp_path, approval_ttl=60)
    yield r,fixture
    await r.close()


async def allow(r, session, tool, arguments):
    result=await r.call(session,tool,arguments)
    assert result['error']['code']=='approval_required'
    r.approvals.decide(result['error']['details']['approval_id'],'allow')
    return await r.call(session,tool,arguments)


async def test_device_selection_is_never_implicit_or_shared(router):
    r,_=router
    assert (await r.call('a','get_device_info',{}))['error']['code']=='device_not_selected'
    assert (await r.call('a','select_device',{'device_id':'sim:fixture'}))['status']=='success'
    assert (await r.call('b','get_device_info',{}))['error']['code']=='device_not_selected'
    assert (await r.call('a','get_device_info',{}))['device_id']=='sim:fixture'


async def test_batch_preflight_rejects_mixed_device_without_side_effects(router):
    r,f=router
    result=await r.call('a','batch_commands',{'device_id':'sim:fixture','commands':[{'tool':'open_url','arguments':{'url':'https://example.com','device_id':'other'}}]})
    assert result['error']['code']=='invalid_arguments'
    assert f.calls==[]


async def test_ambiguous_text_never_delivers_a_tap(router):
    r,f=router
    result=await allow(r,'a','tap_text',{'device_id':'sim:fixture','match':{'text':'Save','match':'exact'}})
    assert result['error']['code']=='ambiguous_text_match'
    assert [c[0] for c in f.calls]==['list_elements']


async def test_mutation_ownership_is_held_across_awaits(router):
    r,f=router
    a={'device_id':'sim:fixture','url':'https://example.com'}
    b={'device_id':'sim:fixture','url':'https://example.org'}
    for session,args in [('a',a),('b',b)]:
        pending=await r.call(session,'open_url',args)
        r.approvals.decide(pending['error']['details']['approval_id'],'allow')
    results=await asyncio.gather(r.call('a','open_url',a),r.call('b','open_url',b))
    assert all(x['status']=='success' for x in results)
    assert f.max_concurrent==1


async def test_wait_is_cancellable_without_mutation_ownership(router):
    r,_=router
    task=asyncio.create_task(r.call('a','wait',{'duration_ms':10000}))
    await asyncio.sleep(.03); task.cancel()
    result=await task
    assert result['error']['code']=='cancelled'
    assert result['error']['side_effect_state']=='not_sent'


async def test_stop_recording_is_scoped_and_repeatable_without_new_approval(router):
    r,f=router
    f.recordings={'owned':{'session_id':'a','device_id':'sim:fixture','final':{'state':'complete'}}}
    result=await r.call('a','stop_recording',{'device_id':'sim:fixture','recording_id':'owned'})
    assert result['status']=='success'
    result=await r.call('b','stop_recording',{'device_id':'sim:fixture','recording_id':'owned'})
    assert result['error']['code']=='invalid_arguments'


async def test_owned_recording_can_stop_after_device_loses_recording_capability(router):
    r,f=router
    discover=f.discover
    async def unavailable(include_all=False):
        devices=await discover(include_all)
        devices[0]['ready']=False
        devices[0]['capabilities']['screen.record']={'status':'unsupported','reason':'Recording no longer available'}
        return devices
    f.discover=unavailable
    f.recordings={'owned':{'session_id':'a','device_id':'sim:fixture','final':{'state':'complete'}}}
    result=await r.call('a','stop_recording',{'device_id':'sim:fixture','recording_id':'owned'})
    assert result['status']=='success'
    denied=await r.call('b','stop_recording',{'device_id':'sim:fixture','recording_id':'owned'})
    assert denied['error']['code']=='invalid_arguments'
    assert [call[0] for call in f.calls]==['stop_recording']


async def test_unexpected_errors_have_redacted_diagnostic_reference(router, caplog):
    import logging
    r,f=router
    async def broken(*args): raise RuntimeError('PRIVATE TEXT MUST NEVER APPEAR')
    f.invoke=broken
    result=await allow(r,'a','open_url',{'device_id':'sim:fixture','url':'https://example.com'})
    assert result['error']['code']=='backend_failure'
    assert result['error']['details']['diagnostic_id']
    assert 'PRIVATE TEXT' not in caplog.text
    assert 'RuntimeError' in caplog.text


async def test_cancelled_input_attempts_release_before_lease_is_reused(router):
    r,f=router
    released=asyncio.Event(); entered=asyncio.Event()
    async def slow(tool,device,args,context):
        entered.set(); await asyncio.sleep(60)
    async def release(device,context): released.set()
    f.invoke=slow; f.release_input=release
    observation=r.observations.issue('a','sim:fixture',1,{'width':100,'height':200},None,'accessibility')
    args={'device_id':'sim:fixture','target':{'x':10,'y':10,'coordinate_space':'device_points','observation_id':observation['observation_id']}}
    pending=await r.call('a','tap',args);r.approvals.decide(pending['error']['details']['approval_id'],'allow')
    call=asyncio.create_task(r.call('a','tap',args));await entered.wait();call.cancel();result=await call
    assert result['error']['side_effect_state']=='unknown'
    assert released.is_set()


async def test_post_send_disappearance_is_unknown_and_never_retried(router):
    r,f=router
    original=f.discover
    gone=False
    async def discover(include_all=False): return [] if gone else await original(include_all)
    async def disconnect(tool,device,args,context):
        nonlocal gone
        f.calls.append((tool,args));gone=True
        return {'delivery':'sent'}
    f.discover=discover;f.invoke=disconnect
    result=await allow(r,'a','open_url',{'device_id':'sim:fixture','url':'https://example.com'})
    assert result['error']['code']=='delivery_outcome_unknown'
    assert result['error']['side_effect_state']=='unknown'
    assert len(f.calls)==1


async def test_mutation_deadline_includes_time_waiting_for_ownership(router):
    r,f=router
    args={'device_id':'sim:fixture','url':'https://example.com','timeout_ms':20}
    pending=await r.call('a','open_url',args);r.approvals.decide(pending['error']['details']['approval_id'],'allow')
    lock=r.locks.setdefault('sim:fixture',asyncio.Lock())
    await lock.acquire()
    task=asyncio.create_task(r.call('a','open_url',args))
    await asyncio.sleep(.06)
    finished=task.done()
    lock.release();result=await task
    assert finished is True
    assert result['error']['code']=='deadline_exceeded'
    assert result['error']['side_effect_state']=='not_sent'
    assert f.calls==[]


async def test_back_navigation_uses_platform_back_button_identity(router):
    r,f=router;original=f.invoke
    async def invoke(tool,device,args,context):
        if tool=='list_elements': return {'logical_size':{'width':100,'height':200},'source':'accessibility','elements':[{'label':'Previous screen','identifier':'BackButton','role':'AXButton','bounds':{'x':1,'y':1,'width':30,'height':30},'source':'accessibility'}]}
        return await original(tool,device,args,context)
    f.invoke=invoke
    result=await allow(r,'a','go_back',{'device_id':'sim:fixture'})
    assert result['status']=='success'
    assert result['data']['strategy']=='navigation_element'
