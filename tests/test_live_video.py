"""Live video fan-out (application/vnd.tapmesh.live) without a device."""
import asyncio
from contextlib import asynccontextmanager
import json
import struct
import httpx
import pytest
from starlette.testclient import TestClient
from test_native_physical import Fakes, FakeDecoder, FakeTransport, PORTRAIT, nal, rtp
from test_router import DeviceFixture

FORMAT,PARAMS,FRAME,HEARTBEAT,END=1,2,3,4,5


def sps(width, height, *, sub_layers=0, chroma=1):
    """Synthetic HEVC SPS NAL (zeroed profile bits exercise emulation prevention)."""
    bits=[]
    def put(value,n):bits.extend((value>>(n-1-i))&1 for i in range(n))
    def ue(value):
        value+=1;n=value.bit_length();put(0,n-1);put(value,n)
    put(0,4);put(sub_layers,3);put(1,1);put(1,8);put(0,80);put(93,8)
    for _ in range(sub_layers):put(1,1);put(1,1)
    if sub_layers:put(0,2*(8-sub_layers))
    for _ in range(sub_layers):put(0,88);put(0,8)
    ue(0);ue(chroma)
    if chroma==3:put(0,1)
    ue(width);ue(height);put(0,1);put(1,1)
    while len(bits)%8:bits.append(0)
    data=bytes(int(''.join(map(str,bits[i:i+8])),2) for i in range(0,len(bits),8))
    out=bytearray();zeros=0
    for byte in data:
        if zeros>=2 and byte<=3:out.append(3);zeros=0
        out.append(byte);zeros=zeros+1 if byte==0 else 0
    return bytes([33<<1,1])+bytes(out)


SPS=sps(1328,2896)
VPS,PPS=nal(32,b'vps'),nal(34,b'pps')


def key(live, seq, sps_nal=SPS):
    for i,unit in enumerate((VPS,sps_nal,PPS)):live.packet(rtp(seq+i,unit,marker=False))
    live.packet(rtp(seq+3,nal(19,b'idr')))
    return seq+4


def delta(live, seq, body=b'd'):
    live.packet(rtp(seq,nal(1,body)));return seq+1


def screen(decode=True):
    from agent.native_physical import LiveScreen
    FakeDecoder.created.clear();transport=FakeTransport()
    live=LiveScreen(transport,('fd00::1',5000),111,222,decoder_factory=FakeDecoder,decode=decode)
    live.display_pixels=(1320,2868)
    return live,transport


def parse(data):
    out=[];i=0
    while i<len(data):
        kind,length=struct.unpack('!BI',data[i:i+5]);out.append((kind,data[i+5:i+5+length]));i+=5+length
    assert i==len(data)
    return out


def units(payload):
    out=[];i=0
    while i<len(payload):
        n=int.from_bytes(payload[i:i+4],'big');out.append(payload[i+4:i+4+n]);i+=4+n
    return out


def queued(sub):
    return [parse(m)[0] for m in sub._queue]


def test_wire_framing_and_sps_picture_size():
    from agent.native_physical import live_message,hevc_sps_size
    assert live_message(HEARTBEAT)==b'\x04\x00\x00\x00\x00'
    assert live_message(END,b'{}')==b'\x05\x00\x00\x00\x02{}'
    assert hevc_sps_size(SPS)==(1328,2896)
    assert b'\x00\x00\x03' in SPS  # emulation prevention is removed before parsing
    assert hevc_sps_size(sps(2896,1328,sub_layers=2,chroma=3))==(2896,1328)
    with pytest.raises(ValueError):hevc_sps_size(SPS[:8])
    # SPS captured from a wired iPhone (1320x2868 display, level 5.0 Main profile).
    assert hevc_sps_size(bytes.fromhex('420111016000000300b000000300000300964800a62002d45880ce4914862e7f13f0bfa1bf50ffaa08fd5413faaa0afd55417faaaa0cfd5554a6e040404010'))==(1328,2896)


async def test_late_subscriber_gets_format_params_keyframe_and_never_earlier_deltas():
    live,transport=screen()
    try:
        seq=key(live,1);seq=delta(live,seq)  # before anyone watches
        firs=len(transport.firs())
        sub=live.subscribe();await asyncio.sleep(0)
        assert len(transport.firs())==firs+1  # forced keyframe request on subscribe
        seq=delta(live,seq,b'early')
        assert not sub._queue and sub.dropped==1
        seq=key(live,seq);seq=delta(live,seq,b'next')
        messages=queued(sub)
        assert [k for k,_ in messages]==[FORMAT,PARAMS,FRAME,FRAME]
        assert json.loads(messages[0][1])=={'version':1,'codec':'hevc','coded_width':1328,'coded_height':2896,'crop':{'x':0,'y':0,'width':1320,'height':2868}}
        assert units(messages[1][1])==[VPS,SPS,PPS]
        flags,pts=struct.unpack('!BQ',messages[2][1][:9])
        assert flags==1 and pts>0 and units(messages[2][1][9:])==[nal(19,b'idr')]  # no parameter sets inside FRAME
        assert messages[3][1][0]==0 and units(messages[3][1][9:])==[nal(1,b'next')]
        # The screenshot decoder is fed independently of viewers.
        assert live.frames==5 and (await live.frame((1320,2868),newer_within=0)).size==(1320,2868)
    finally:await live.aclose()
    assert sub.closed and parse(sub._queue[-1])[0]==(END,b'{"reason": "stream_closed"}')


async def test_corrupt_access_unit_is_never_forwarded_and_viewers_wait_for_a_keyframe():
    live,transport=screen()
    try:
        sub=live.subscribe();seq=key(live,1);sub._queue.clear()
        await asyncio.sleep(.6);firs=len(transport.firs())
        live.packet(rtp(seq+1,nal(1,b'after-gap')));await asyncio.sleep(0)  # seq skipped
        assert sub.waiting_key and not sub._queue and len(transport.firs())>firs
        delta(live,seq+2)
        assert not sub._queue
        key(live,seq+3)
        assert [k for k,_ in queued(sub)]==[FRAME] and sub._queue[0][5]==1  # params unchanged: not resent
    finally:await live.aclose()


async def test_slow_viewer_drops_to_next_keyframe_without_blocking_others():
    live,transport=screen()
    try:
        slow=live.subscribe(max_messages=6);fast=live.subscribe()
        seq=key(live,1)
        received=queued(fast);fast._queue.clear();fast._bytes=0
        await asyncio.sleep(.6);firs=len(transport.firs())
        for i in range(10):
            seq=delta(live,seq,bytes([i]))
            received+=queued(fast);fast._queue.clear();fast._bytes=0
        assert [k for k,_ in received]==[FORMAT,PARAMS,FRAME]+[FRAME]*10
        assert slow.overflows==1 and slow.waiting_key and not slow._queue and slow._bytes==0
        await asyncio.sleep(0)
        assert len(transport.firs())==firs+1 and slow.dropped>=1
        key(live,seq)
        assert [k for k,_ in queued(slow)]==[FORMAT,PARAMS,FRAME] and slow._queue[2][5]==1
    finally:await live.aclose()


async def test_byte_bound_drops_to_a_keyframe_and_never_queues_an_oversized_one():
    live,transport=screen()
    try:
        sub=live.subscribe(max_bytes=600)
        seq=key(live,1);assert len(sub._queue)==3
        seq=delta(live,seq,b'x'*200);assert len(sub._queue)==4 and sub._bytes<=600
        seq=delta(live,seq,b'x'*300)  # over the byte bound: drop all, wait for a key
        assert sub.waiting_key and not sub._queue and sub._bytes==0
        seq=delta(live,seq);assert not sub._queue
        seq=key(live,seq)
        assert [k for k,_ in queued(sub)]==[FORMAT,PARAMS,FRAME] and sub._bytes<=600
        live.packet(rtp(seq,nal(19,b'i'*700)))  # a keyframe that can never fit is not queued
        assert sub.waiting_key and not sub._queue
    finally:await live.aclose()


async def test_heartbeat_when_no_frame_was_sent_for_a_second():
    live,transport=screen()
    try:
        sub=live.subscribe();messages=sub.messages(heartbeat=.05)
        assert await asyncio.wait_for(anext(messages),1)==b'\x04\x00\x00\x00\x00'
        seq=key(live,1)
        assert [parse(await anext(messages))[0][0] for _ in range(3)]==[FORMAT,PARAMS,FRAME]
        assert parse(await asyncio.wait_for(anext(messages),1))[0]==(HEARTBEAT,b'')
        live.end_subscribers('device_disconnected')
        assert parse(await anext(messages))[0]==(END,b'{"reason": "device_disconnected"}')
        with pytest.raises(StopAsyncIteration):await anext(messages)
        assert not live.subscribers
    finally:await live.aclose()


async def test_parameter_change_resends_format_and_params_before_its_keyframe():
    live,transport=screen()
    try:
        sub=live.subscribe();seq=key(live,1);sub._queue.clear()
        rotated=sps(2896,1328)
        key(live,seq,rotated)
        messages=queued(sub)
        assert [k for k,_ in messages]==[FORMAT,PARAMS,FRAME]
        assert json.loads(messages[0][1])['crop']=={'x':0,'y':0,'width':2868,'height':1320}
        assert units(messages[1][1])==[VPS,rotated,PPS]
    finally:await live.aclose()


async def test_viewers_work_without_a_decoder_and_request_keyframes():
    live,transport=screen(decode=False)
    try:
        assert not transport.firs()
        sub=live.subscribe();await asyncio.sleep(0)
        assert len(transport.firs())==1
        key(live,1)
        assert [k for k,_ in queued(sub)]==[FORMAT,PARAMS,FRAME] and not FakeDecoder.created
    finally:await live.aclose()


async def test_consumer_that_never_reads_is_closed_as_stalled(monkeypatch):
    import agent.native_physical as native
    monkeypatch.setattr(native,'LIVE_STALL_SECONDS',.1)
    live,transport=screen();live.run()
    try:
        sub=live.subscribe()
        for _ in range(100):
            if sub.closed:break
            await asyncio.sleep(.02)
        assert sub.closed and sub.end_reason=='stalled' and not live.subscribers
    finally:await live.aclose()


async def test_silent_start_ends_viewers_but_a_static_screen_stays_open():
    for received_packets in (False,True):
        live,_=screen();now=[live.started_at]
        live.clock=lambda:now[0]
        live.run()
        try:
            subscriber=live.subscribe()
            if received_packets:key(live,1)
            now[0]+=5.1
            await asyncio.sleep(.3)
            assert live.dead is not received_packets
            if not received_packets:
                assert queued(subscriber)==[(END,b'{"reason": "stream_ended"}')]
            else:
                assert not subscriber.closed
        finally:await live.aclose()


async def test_send_failure_of_any_kind_ends_viewers():
    live,transport=screen()
    async def fail(*args):
        raise RuntimeError('transport closed')
    transport.sendto=fail
    try:
        subscriber=live.subscribe()
        await asyncio.sleep(0)
        assert live.dead and subscriber.end_reason=='stream_ended'
    finally:await live.aclose()


async def test_dead_stream_does_not_serve_stale_frames():
    live,_=screen();now=[live.started_at]
    live.clock=lambda:now[0]
    try:
        key(live,1)
        live.dead=True
        assert await live.frame(newer_within=0) is not None
        now[0]+=5.1
        assert await live.frame(newer_within=0) is None
    finally:await live.aclose()


# ---- NativePhysical: shared stream, idle rule, END reasons ------------------

class LiveFakes(Fakes):
    """Fakes whose stream is a real LiveScreen over a fake RTP transport."""
    @asynccontextmanager
    async def stream(self, rsd, decode=True):
        from agent.native_physical import LiveScreen
        if self.stream_error:raise self.stream_error
        self.streams_opened+=1
        live=LiveScreen(FakeTransport(),('fd00::1',5000),111,222,decoder_factory=FakeDecoder,decode=decode)
        self.streams.append(live);live.run()
        try:yield live
        finally:
            await live.aclose();self.streams_closed+=1


async def test_empty_stream_retries_back_off_across_sessions_then_reset_after_frames():
    from agent.native_physical import _NoFastPath
    fakes=LiveFakes();now=[100.0]
    adapter=fakes.adapter(clock=lambda:now[0])
    try:
        for delay in (10,30,60):
            assert await adapter.subscribe('bound') is not None
            fakes.streams[-1].dead=True
            now[0]+=5
            assert await adapter.subscribe('bound') is None
            await adapter.close_device('bound')
            now[0]+=delay-.1
            assert await adapter.subscribe('bound') is None
            now[0]+=.1
        assert await adapter.subscribe('bound') is not None
        key(fakes.streams[-1],1)
        await adapter.close_device('bound')
        assert await adapter.subscribe('bound') is not None
        fakes.streams[-1].dead=True
        assert await adapter.subscribe('bound') is None
        async with adapter._lease('bound') as session:
            with pytest.raises(_NoFastPath):await adapter._hid(session)
        now[0]+=10
        assert await adapter.subscribe('bound') is not None
    finally:await adapter.aclose()


async def test_viewer_shares_the_stream_with_screenshots_and_input_and_never_opens_hid():
    fakes=LiveFakes();adapter=fakes.adapter()
    try:
        sub=await adapter.subscribe('bound')
        live=fakes.streams[0]
        assert fakes.streams_opened==1 and fakes.media_opened==0 and live.display_pixels==(1320,2868)
        key(live,1)
        assert json.loads(queued(sub)[0][1])['crop']=={'x':0,'y':0,'width':1320,'height':2868}
        assert (await adapter.screenshot('bound')).size==(1320,2868)
        await adapter.input('tap','bound',PORTRAIT,{'target':(10,20,None)},{})
        second=await adapter.subscribe('bound')
        assert fakes.streams_opened==1 and fakes.media_opened==1 and len(live.subscribers)==2
        await adapter.close_device('bound')
        assert sub.end_reason=='device_disconnected' and second.end_reason=='device_disconnected'
    finally:await adapter.aclose()


async def test_idle_close_is_suppressed_while_viewed_then_applies_after_the_last_viewer():
    fakes=LiveFakes();adapter=fakes.adapter(idle_seconds=.1)
    try:
        sub=await adapter.subscribe('bound')
        await asyncio.sleep(.5)
        assert fakes.streams_closed==0 and adapter.sessions and not sub.closed
        sub.close()
        for _ in range(100):
            if not adapter.sessions:break
            await asyncio.sleep(.02)
        assert fakes.streams_closed==1 and fakes.tunnels_closed==1 and not adapter.sessions
    finally:await adapter.aclose()


async def test_network_device_or_failed_stream_has_no_viewer_and_shutdown_ends_viewers():
    import time
    from test_native_physical import network
    fakes=LiveFakes();adapter=fakes.adapter(probe=network)
    assert await adapter.subscribe('bound') is None and fakes.tunnels_opened==0
    fakes=LiveFakes();fakes.stream_error=OSError('no stream');adapter=fakes.adapter()
    try:
        assert await adapter.subscribe('bound') is None
        assert adapter.sessions['bound'].stream_retry_at>time.monotonic()
        fakes.stream_error=None
        assert await adapter.subscribe('bound') is None  # not retried immediately
    finally:await adapter.aclose()
    fakes=LiveFakes();adapter=fakes.adapter()
    sub=await adapter.subscribe('bound')
    await adapter.aclose()
    assert sub.end_reason=='shutdown' and fakes.streams_closed==1


async def test_backend_routes_only_wired_physical_devices(tmp_path):
    from agent.backends import MacBackend
    from agent.safety import BridgeError
    backend=MacBackend(tmp_path)
    try:
        with pytest.raises(BridgeError) as error:await backend.live({'device_id':'sim:1','native_id':'1','backend':'simulator','connection_epoch':1})
        assert error.value.code=='live_unavailable'
        class Native:
            def __init__(self):self.serials=[]
            async def subscribe(self,serial):self.serials.append(serial);return None
            async def close_device(self,serial):pass
            async def aclose(self):pass
        backend.native_physical=Native();backend.native_serials['ios:owned']='bound'
        with pytest.raises(BridgeError) as error:await backend.live({'device_id':'ios:owned','native_id':'owned','backend':'physical','connection_epoch':1})
        assert error.value.code=='live_unavailable' and backend.native_physical.serials==['bound']
    finally:await backend.close()


# ---- HTTP: agent route and server relay --------------------------------------

def asgi_get(app, path, query='', headers=None):
    """Drive one streaming GET directly (TestClient buffers whole bodies)."""
    disconnect=asyncio.Event();sent=[];state={'requested':False}
    async def receive():
        if not state['requested']:
            state['requested']=True;return {'type':'http.request','body':b'','more_body':False}
        await disconnect.wait();return {'type':'http.disconnect'}
    async def send(message):sent.append(message)
    scope={'type':'http','asgi':{'version':'3.0','spec_version':'2.3'},'http_version':'1.1','method':'GET','scheme':'http','path':path,'raw_path':path.encode(),'query_string':query.encode(),'root_path':'','headers':[(k.lower().encode(),v.encode()) for k,v in (headers or {}).items()],'client':('127.0.0.1',1),'server':('127.0.0.1',1)}
    return asyncio.create_task(app(scope,receive,send)),sent,disconnect


def body(sent):return b''.join(m.get('body',b'') for m in sent if m['type']=='http.response.body')


async def viewer_count(app):
    task,sent,_=asgi_get(app,'/health')
    await asyncio.wait_for(task,1)
    return json.loads(body(sent))['live_viewers']


async def until(predicate, seconds=2):
    for _ in range(int(seconds/.01)):
        if predicate():return
        await asyncio.sleep(.01)
    raise AssertionError('condition not reached')


class LiveDevice(DeviceFixture):
    def __init__(self, live=None):
        super().__init__();self.screen=live;self.views=[]
    async def live(self, device):
        view=self.screen.subscribe();self.views.append(view);return view


async def test_agent_live_route_streams_read_only_and_unsubscribes_on_disconnect(tmp_path):
    from agent.app import create_app
    from agent.core import Router
    live,transport=screen();token='a'*64
    router=Router(LiveDevice(live),state_dir=tmp_path)
    app=create_app(router,token=token,approval_token='b'*64)
    try:
        task,sent,disconnect=asgi_get(app,'/v1/live/sim:fixture','session_id=viewer',{'Authorization':'Bearer '+token})
        await until(lambda:live.subscribers)
        key(live,1)
        await until(lambda:len(parse(body(sent)))>=3)
        start=sent[0]
        assert start['status']==200 and (b'content-type',b'application/vnd.tapmesh.live') in start['headers'] and (b'cache-control',b'no-store') in start['headers']
        assert [k for k,_ in parse(body(sent))][:3]==[FORMAT,PARAMS,FRAME]
        # Read-only: no approval, ownership work, held session activity or device call.
        assert router.approvals.pending()==[] and not router.work and not router.session_active and router.backend.calls==[]
        disconnect.set();await asyncio.wait_for(task,2)
        assert not live.subscribers and router.backend.views[0].closed
    finally:
        await live.aclose();await router.close()


async def test_agent_live_route_refuses_before_streaming(tmp_path):
    from agent.app import create_app
    from agent.core import Router
    token='a'*64;headers={'Authorization':'Bearer '+token}
    with TestClient(create_app(Router(DeviceFixture(),state_dir=tmp_path),token=token,approval_token='b'*64)) as client:
        assert client.get('/v1/live/sim:fixture?session_id=s').status_code==401
        response=client.get('/v1/live/sim:fixture?session_id=s',headers=headers)  # simulator: no live stream
        assert response.status_code==409 and response.json()['error']['code']=='live_unavailable'
        assert client.get('/v1/live/sim:missing?session_id=s',headers=headers).status_code==404
        assert client.get('/v1/live/sim:fixture',headers=headers).status_code==400


async def test_prepared_update_ends_live_viewers(tmp_path):
    from agent.core import Router
    live,transport=screen()
    router=Router(LiveDevice(live),state_dir=tmp_path)
    try:
        view=await router.live('s','sim:fixture')
        router.prepare_update('lease')
        assert view.end_reason=='update'
        from agent.safety import BridgeError
        with pytest.raises(BridgeError) as error:await router.live('s','sim:fixture')
        assert error.value.code=='overloaded'
    finally:
        await live.aclose();await router.close()


async def test_server_relays_live_stream_and_closes_upstream_on_client_disconnect(tmp_path):
    from server.app import create_app
    upstream={'closed':False,'requests':[]}
    async def chunks():
        try:
            yield b'\x04\x00\x00\x00\x00'
            while True:
                await asyncio.sleep(.01);yield b'\x04\x00\x00\x00\x00'
        finally:upstream['closed']=True
    async def agent(request):
        upstream['requests'].append(request)
        if request.url.path=='/v1/live/ios:missing':return httpx.Response(404,json={'error':{'code':'device_not_found'}})
        return httpx.Response(200,headers={'content-type':'application/vnd.tapmesh.live'},content=chunks())
    client=httpx.AsyncClient(transport=httpx.MockTransport(agent),base_url='http://127.0.0.1:48792',headers={'Authorization':'Bearer '+'c'*64})
    app=create_app(state_dir=tmp_path,token='a'*64,agent_client=client)
    auth={'Authorization':'Bearer '+'a'*64}
    task,sent,disconnect=asgi_get(app,'/api/live/ios:device-1','session_id=viewer',auth)
    await until(lambda:len(body(sent))>=15)
    assert await viewer_count(app)==1
    assert sent[0]['status']==200 and (b'content-type',b'application/vnd.tapmesh.live') in sent[0]['headers']
    request=upstream['requests'][0]
    assert request.url.path=='/v1/live/ios:device-1' and request.url.params['session_id']=='viewer'
    assert request.headers['authorization']=='Bearer '+'c'*64 and request.extensions['timeout']['read'] is None
    disconnect.set();await asyncio.wait_for(task,2)
    await until(lambda:upstream['closed'])
    assert await viewer_count(app)==0
    task,sent,disconnect=asgi_get(app,'/api/live/ios:missing','session_id=viewer',auth)
    await asyncio.wait_for(task,2)
    assert sent[0]['status']==404 and json.loads(body(sent))['error']['code']=='device_not_found'
    assert await viewer_count(app)==0
    task,sent,disconnect=asgi_get(app,'/api/live/ios:device-1','session_id=viewer')
    await asyncio.wait_for(task,2)
    assert sent[0]['status']==401
    await client.aclose()


async def test_server_ends_the_client_stream_when_the_agent_stream_ends(tmp_path):
    from server.app import create_app
    async def agent(request):
        async def chunks():
            yield b'\x05\x00\x00\x00\x02{}'
        return httpx.Response(200,headers={'content-type':'application/vnd.tapmesh.live'},content=chunks())
    client=httpx.AsyncClient(transport=httpx.MockTransport(agent),base_url='http://127.0.0.1:48792')
    app=create_app(state_dir=tmp_path,token='a'*64,agent_client=client)
    task,sent,disconnect=asgi_get(app,'/api/live/ios:device-1','session_id=viewer',{'Authorization':'Bearer '+'a'*64})
    await asyncio.wait_for(task,2)
    assert parse(body(sent))==[(END,b'{}')] and sent[-1]['more_body'] is False
    assert await viewer_count(app)==0
    await client.aclose()


async def test_server_limits_pending_viewers_and_releases_slots_on_handshake_failure_and_disconnect(tmp_path):
    from server.app import create_app
    started=[];reject=asyncio.Event()
    async def agent(request):
        started.append(request)
        await reject.wait()
        return httpx.Response(409,json={'error':{'code':'live_unavailable'}})

    async with httpx.AsyncClient(transport=httpx.MockTransport(agent),base_url='http://127.0.0.1:48792') as client:
        app=create_app(state_dir=tmp_path,token='a'*64,agent_client=client)
        auth={'Authorization':'Bearer '+'a'*64}

        pending=[asgi_get(app,'/api/live/ios:device-1','session_id=viewer',auth) for _ in range(8)]
        try:
            await until(lambda:len(started)>=8)
            ninth,sent,_=asgi_get(app,'/api/live/ios:device-1','session_id=viewer',auth)
            pending.append((ninth,sent,None))
            await asyncio.wait_for(ninth,.3)
            assert sent[0]['status']==409
            assert json.loads(body(sent))['error']['details']['reason']=='too_many_viewers'
            assert len(started)==await viewer_count(app)==8

            first,_,disconnect=pending[0];disconnect.set()
            await asyncio.wait_for(asyncio.gather(first,return_exceptions=True),1)
            assert await viewer_count(app)==7
            reject.set()
            await asyncio.gather(*(request for request,_,_ in pending[1:]))
            assert await viewer_count(app)==0
        finally:
            reject.set()
            await asyncio.gather(*(request for request,_,_ in pending),return_exceptions=True)


async def test_live_slot_is_released_when_agent_connection_fails(tmp_path):
    from server.app import create_app

    async def unavailable(request):
        raise httpx.ConnectError('Agent is offline',request=request)

    async with httpx.AsyncClient(transport=httpx.MockTransport(unavailable),base_url='http://127.0.0.1:48792') as client:
        app=create_app(state_dir=tmp_path,token='a'*64,agent_client=client)
        auth={'Authorization':'Bearer '+'a'*64}
        task,sent,_=asgi_get(app,'/api/live/ios:device-1','session_id=viewer',auth)
        await asyncio.wait_for(task,1)
        assert sent[0]['status']==502
        assert await viewer_count(app)==0
