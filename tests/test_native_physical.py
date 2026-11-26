import asyncio
from contextlib import asynccontextmanager
import io
from types import SimpleNamespace
import pytest

PORTRAIT={'logical_size':{'width':440,'height':956},'orientation':'portrait'}


def png(color, size=(2,2)):
    from PIL import Image
    out=io.BytesIO();Image.new('RGBA',size,color).save(out,format='PNG');return out.getvalue()


async def usb(serial):return 'userspace'


async def network(serial):return 'native'


class FakeStream:
    """Live video stream fake: frames are PIL images, nothing reaches a device."""
    def __init__(self, fakes, decode):
        import time
        self.fakes=fakes;self.decoding=decode and fakes.stream_decodes;self.dead=False;self.started_at=time.monotonic()
    async def frame(self, display_pixels=None):
        self.fakes.frames_served.append(display_pixels)
        if self.fakes.capture_calls_while_streaming:raise AssertionError('unreachable')
        return self.fakes.stream_image if self.decoding else None


class Fakes:
    """Counting tunnel/stream/HID/service fakes; nothing reaches a device."""
    def __init__(self, hid_factory=None):
        self.tunnels_opened=0;self.tunnels_closed=0;self.media_opened=0;self.media_closed=0
        self.streams_opened=0;self.streams_closed=0;self.stream_error=None;self.stream_decodes=True;self.streams=[]
        self.frames=[];self.keys=[];self.hid_factory=hid_factory or (lambda fakes:HID(fakes))
        self.services_opened=[];self.capture_error=None;self.image=png('red');self.captures=0
        self.capture_calls_while_streaming=0;self.frames_served=[]
        self.lock_state_reply=None
        from PIL import Image
        self.stream_image=Image.new('RGB',(1320,2868),'blue')
    @asynccontextmanager
    async def tunnel(self, serial, transport):
        self.tunnels_opened+=1
        try:yield SimpleNamespace(udid=serial)
        finally:self.tunnels_closed+=1
    @asynccontextmanager
    async def stream(self, rsd, decode=True):
        if self.stream_error:raise self.stream_error
        self.streams_opened+=1;stream=FakeStream(self,decode);self.streams.append(stream)
        try:yield stream
        finally:
            stream.dead=True;self.streams_closed+=1
    @asynccontextmanager
    async def media(self, rsd):
        """HID service; touch is authorized by the session's stream."""
        assert any(not s.dead for s in self.streams), 'HID opened without a live stream'
        self.media_opened+=1
        try:yield self.hid_factory(self)
        finally:self.media_closed+=1
    def streaming(self):return any(not s.dead for s in self.streams)
    @asynccontextmanager
    async def service(self, rsd, name):
        self.services_opened.append(name)
        fakes=self
        class Service:
            async def capture_screenshot(self):
                fakes.captures+=1
                if fakes.streaming():fakes.capture_calls_while_streaming+=1
                if fakes.capture_error:raise fakes.capture_error
                return {'image':fakes.image,'imageFormat':'png'}
            async def get_display_info(self):
                return {'displays':[{'primary':True,'pointScale':3,'bounds':[[0,0],[1320,2868]]}],'orientation':{'currentDeviceOrientation':'portrait'}}
            async def get_lockstate(self):
                if isinstance(fakes.lock_state_reply,Exception):raise fakes.lock_state_reply
                if callable(fakes.lock_state_reply):return await fakes.lock_state_reply()
                return fakes.lock_state_reply
        yield Service()
    def adapter(self, probe=usb, **kwargs):
        from agent.native_physical import NativePhysical
        kwargs.setdefault('auth_settle_seconds',0)
        return NativePhysical(tunnel_factory=self.tunnel,stream_factory=self.stream,hid_factory=self.media,service_factory=self.service,transport_probe=probe,**kwargs)


class HID:
    def __init__(self, fakes):self.fakes=fakes
    async def send_multitouch(self, contacts):self.fakes.frames.append(list(contacts))
    async def create_keyboard_service(self, **kwargs):return 9
    async def send_keyboard(self, kid, usages):self.fakes.keys.append(list(usages))


def test_device_point_geometry_maps_to_native_uint16_without_clamping():
    from agent.native_physical import to_hid
    from agent.safety import BridgeError
    assert to_hid(220,478,{'width':440,'height':956})==(32768,32768)
    assert to_hid(0,0,{'width':440,'height':956})==(0,0)
    for point in [(440,10),(-1,2),(2,float('nan'))]:
        with pytest.raises(BridgeError):to_hid(*point,{'width':440,'height':956})


async def test_cancelled_multitouch_releases_every_contact_and_reports_no_success():
    from agent.native_physical import TouchController
    class Recorder:
        def __init__(self):self.frames=[];self.down=asyncio.Event()
        async def send_multitouch(self,contacts):
            self.frames.append(contacts)
            if any(c.touching for c in contacts):self.down.set()
    hid=Recorder();control=TouchController(hid)
    task=asyncio.create_task(control.gesture([(100,200,500,600),(800,900,700,600)],duration=10,steps=60))
    await hid.down.wait();task.cancel()
    with pytest.raises(asyncio.CancelledError):await task
    assert {c.identity for c in hid.frames[-1]}=={0,1}
    assert all(c.touching is False for c in hid.frames[-1])


@pytest.mark.parametrize('write_ms, end_ms', [(2, 252), (10, 453.333333)])
async def test_movement_deadlines_do_not_accumulate_hid_write_time(monkeypatch,write_ms,end_ms):
    from agent import native_physical

    now=0
    async def sleep(seconds):
        nonlocal now
        now+=seconds*1000

    presents=[]
    class Recorder:
        async def send_multitouch(self, contacts):
            nonlocal now
            presents.append((now,list(contacts)))
            now+=write_ms

    monkeypatch.setattr(native_physical,'time',SimpleNamespace(monotonic=lambda:now/1000))
    monkeypatch.setattr(native_physical,'asyncio',SimpleNamespace(sleep=sleep,shield=asyncio.shield))
    control=native_physical.TouchController(Recorder())
    await control.gesture([(100,200,400,500),(800,900,500,600)],duration=.1,steps=30,hold=.15)

    assert len(presents)==32
    assert [contact.x for _,frame in presents[:31] for contact in frame]==[
        coordinate for step in range(31) for coordinate in (100+10*step,800-10*step)
    ]
    assert [contact.y for contact in presents[0][1]]==[200,900]
    assert [contact.y for contact in presents[-2][1]]==[500,600]
    assert presents[1][0]>=write_ms+150
    assert presents[-2][0]==pytest.approx(end_ms)
    assert presents[-1][0]==pytest.approx(end_ms+write_ms)
    assert all(not contact.touching for contact in presents[-1][1])


async def test_bound_rsd_identity_mismatch_closes_without_hid_entry():
    from agent.native_physical import NativePhysical
    from agent.safety import BridgeError
    closed=[];entered=[]
    @asynccontextmanager
    async def tunnel(serial, transport):
        try:yield SimpleNamespace(udid='other-device')
        finally:closed.append(True)
    @asynccontextmanager
    async def media(rsd):
        entered.append(True);yield object()
    adapter=NativePhysical(tunnel_factory=tunnel,stream_factory=Fakes().stream,hid_factory=media,transport_probe=usb,auth_settle_seconds=0)
    with pytest.raises(BridgeError) as result:
        await adapter.input('tap','expected',PORTRAIT,{'target':(10,20,None)},{})
    assert result.value.code=='connection_changed'
    assert result.value.side_effect_state=='not_sent'
    assert closed==[True] and entered==[] and not adapter.sessions


async def test_observed_landscape_left_uses_display_normalized_points_and_releases():
    fakes=Fakes();adapter=fakes.adapter()
    try:
        result=await adapter.input('tap','bound',{'logical_size':{'width':1376,'height':1032},'orientation':'landscapeLeft'},{'target':(344,258,None)},{})
        assert fakes.frames[0][0].x==49151 and fakes.frames[0][0].y==16384
        assert all(not contact.touching for contact in fakes.frames[-1])
        assert result['verified'] is False
    finally:await adapter.aclose()


async def test_input_orientation_and_dimensions_must_agree_before_media():
    from agent.native_physical import NativePhysical
    from agent.safety import BridgeError
    with pytest.raises(BridgeError) as failed:
        await NativePhysical(transport_probe=usb).input('tap','bound',{'logical_size':{'width':1376,'height':1032},'orientation':'portrait'},{'target':(75,282,None)},{})
    assert failed.value.code=='stale_observation' and failed.value.side_effect_state=='not_sent'


async def test_media_auth_failure_never_claims_input_was_sent():
    from agent.native_physical import NativePhysical
    from agent.safety import BridgeError
    opened=[]
    @asynccontextmanager
    async def tunnel(serial, transport):
        opened.append(True);yield SimpleNamespace(udid=serial)
    @asynccontextmanager
    async def media(rsd):
        raise TimeoutError('private backend detail')
        yield
    adapter=NativePhysical(tunnel_factory=tunnel,stream_factory=Fakes().stream,hid_factory=media,transport_probe=usb,auth_settle_seconds=0)
    with pytest.raises(BridgeError) as result:
        await adapter.input('tap','expected',PORTRAIT,{'target':(10,20,None)},{})
    assert result.value.code=='prerequisite_required'
    assert result.value.side_effect_state=='not_sent'
    assert 'private backend detail' not in result.value.message
    # A fresh session is never retried: only one tunnel was opened.
    assert opened==[True] and not adapter.sessions


async def test_literal_text_requires_explicit_confirmed_layout_before_focus(monkeypatch):
    from agent.native_physical import NativePhysical
    from agent.safety import BridgeError
    monkeypatch.delenv('TAPMESH_PHYSICAL_KEYBOARD_LAYOUT',raising=False)
    entered=[]
    @asynccontextmanager
    async def never_open(serial, transport):
        entered.append(True);raise AssertionError('Should not open transport');yield
    with pytest.raises(BridgeError) as result:
        await NativePhysical(tunnel_factory=never_open,transport_probe=usb).input('type_text','not-opened',PORTRAIT,{'target':(10,20,None)},{'text':'test'})
    assert result.value.code=='prerequisite_required'
    assert result.value.side_effect_state=='not_sent'
    assert entered==[]


@pytest.mark.parametrize(('lock_after','expected_keys','side_effect'),[(25,40,'unknown'),(0,0,'not_sent')])
async def test_locked_phone_stops_keyboard_input_between_chunks(lock_after,expected_keys,side_effect):
    from agent.safety import BridgeError
    fakes=Fakes();adapter=fakes.adapter()

    async def still_unlocked():
        return fakes.keys.count([4])<lock_after

    try:
        with pytest.raises(BridgeError) as error:
            await adapter.input('type_text','bound',PORTRAIT,{},{'text':'a'*100,'keyboard_layout':'us'},still_unlocked=still_unlocked)
        assert fakes.keys==[[4],[]]*expected_keys
        assert error.value.code=='protected_action_requires_human'
        assert error.value.side_effect_state==side_effect
        assert fakes.tunnels_opened==1
    finally:await adapter.aclose()


async def test_lock_between_double_tap_contacts_prevents_second_tap():
    from agent.safety import BridgeError
    fakes=Fakes();adapter=fakes.adapter()

    async def still_unlocked():
        return not fakes.frames

    try:
        with pytest.raises(BridgeError) as error:
            await adapter.input('double_tap','bound',PORTRAIT,{'target':(10,20,None)},{'interval_ms':40},still_unlocked=still_unlocked)
        assert [frame[0].touching for frame in fakes.frames]==[True,False]
        assert error.value.code=='protected_action_requires_human'
        assert error.value.side_effect_state=='unknown'
    finally:await adapter.aclose()


async def test_lock_during_long_press_releases_the_contact(monkeypatch):
    from agent import native_physical
    from agent.safety import BridgeError
    fakes=Fakes();adapter=fakes.adapter()

    async def still_unlocked():
        return not fakes.frames

    monkeypatch.setattr(native_physical,'GESTURE_LOCK_RECHECK_SECONDS',.02,raising=False)
    try:
        with pytest.raises(BridgeError) as error:
            await adapter.input('long_press','bound',PORTRAIT,{'target':(10,20,None)},{'duration_ms':1000},still_unlocked=still_unlocked)
        assert [frame[0].touching for frame in fakes.frames]==[True,False]
        assert error.value.code=='protected_action_requires_human'
        assert error.value.side_effect_state=='unknown'
    finally:await adapter.aclose()


async def test_modifier_is_sent_before_key_and_released_after_key():
    from agent.native_physical import TouchController
    class Keys:
        def __init__(self):self.frames=[]
        async def create_keyboard_service(self,**kwargs):return 77
        async def send_keyboard(self,kid,usages):self.frames.append((kid,list(usages)))
    hid=Keys();control=TouchController(hid)
    await control.key([225,23])
    assert hid.frames==[(77,[225]),(77,[225,23]),(77,[225]),(77,[])]


async def test_explicit_clipboard_method_is_unicode_and_never_a_keyboard_fallback(monkeypatch):
    from agent.native_physical import NativePhysical
    events=[]
    @asynccontextmanager
    async def tunnel(serial, transport):yield SimpleNamespace(udid=serial)
    class Keys:
        async def create_keyboard_service(self,**kwargs):return 4
        async def send_keyboard(self,kid,usages):events.append(('keys',list(usages)))
    @asynccontextmanager
    async def media(rsd):yield Keys()
    class Clipboard:
        async def set_text(self,text):events.append(('clipboard',text))
    @asynccontextmanager
    async def clipboard(rsd):yield Clipboard()
    monkeypatch.delenv('TAPMESH_PHYSICAL_KEYBOARD_LAYOUT',raising=False)
    adapter=NativePhysical(tunnel_factory=tunnel,stream_factory=Fakes().stream,hid_factory=media,clipboard_factory=clipboard,transport_probe=network,auth_settle_seconds=0)
    result=await adapter.input('type_text','bound',PORTRAIT,{},{'text':'TapMesh تجربة','input_method':'clipboard'})
    assert result['input_method']=='clipboard' and result['clipboard_changed'] is True
    assert result['verified'] is False
    assert events[0]==('clipboard','TapMesh تجربة')
    assert ('keys',[227]) in events
    assert ('keys',[227,25]) in events


async def test_lock_after_unicode_clipboard_write_prevents_paste():
    from agent.safety import BridgeError
    writes=[]
    class Clipboard:
        async def set_text(self,text):writes.append(text)
    @asynccontextmanager
    async def clipboard(rsd):yield Clipboard()

    async def still_unlocked():
        return not writes

    fakes=Fakes();adapter=fakes.adapter(clipboard_factory=clipboard)
    try:
        with pytest.raises(BridgeError) as error:
            await adapter.input('type_text','bound',PORTRAIT,{},{'text':'TapMesh تجربة','input_method':'clipboard'},still_unlocked=still_unlocked)
        assert writes==['TapMesh تجربة'] and not fakes.keys
        assert error.value.code=='protected_action_requires_human'
        assert error.value.side_effect_state=='unknown'
        assert error.value.details['clipboard_changed'] is True
    finally:await adapter.aclose()


def test_black_or_transparent_capture_is_not_an_observable_input_surface():
    from agent.native_physical import frame_unavailable
    assert frame_unavailable(png((0,0,0,255))) is True
    assert frame_unavailable(png((255,0,0,0))) is True
    assert frame_unavailable(png((255,255,255,255))) is False


async def test_send_callback_marks_the_first_actual_report_only():
    called=[]
    class Checked(HID):
        async def send_multitouch(self,contacts):assert called
    fakes=Fakes(lambda f:Checked(f));adapter=fakes.adapter()
    try:
        result=await adapter.input('tap','bound',PORTRAIT,{'target':(20,20,None)},{},on_send=lambda:called.append(True))
        assert result['delivery']=='sent' and called
    finally:await adapter.aclose()


async def test_simulator_never_silently_substitutes_keyboard_for_explicit_clipboard(tmp_path):
    from agent.backends import MacBackend
    from agent.safety import BridgeError
    backend=MacBackend(tmp_path);context={'sent':True,'targets':{}}
    try:
        with pytest.raises(BridgeError) as error:
            await backend._input('type_text',{'backend':'simulator','native_id':'fixture'},{'text':'test','input_method':'clipboard'},context)
        assert error.value.code=='unsupported_capability'
        assert error.value.side_effect_state=='not_sent'
        assert context['sent'] is False
        assert not backend.companions and not backend.runner.processes
    finally:await backend.close()


async def test_physical_text_frame_mode_is_not_silently_ignored_by_simulator(tmp_path):
    from agent.backends import MacBackend
    from agent.safety import BridgeError
    backend=MacBackend(tmp_path)
    context={'sent':True,'targets':{'target':(10,20,None)}}
    async def never_send(*args,**kwargs):raise AssertionError('Unsupported mode reached simulator input')
    backend._idb=never_send
    try:
        with pytest.raises(BridgeError) as error:
            await backend._input('fill',{'backend':'simulator','native_id':'fixture'},
                                 {'text':'test','frame_validation':'text_target'},context)
        assert error.value.code=='unsupported_capability'
        assert error.value.side_effect_state=='not_sent' and context['sent'] is False
    finally:await backend.close()


@pytest.mark.parametrize('environment,arguments', [('us',{}),(None,{'keyboard_layout':'us'})])
async def test_confirmed_us_layout_can_be_local_or_per_action(monkeypatch,environment,arguments):
    if environment:monkeypatch.setenv('TAPMESH_PHYSICAL_KEYBOARD_LAYOUT',environment)
    else:monkeypatch.delenv('TAPMESH_PHYSICAL_KEYBOARD_LAYOUT',raising=False)
    fakes=Fakes();adapter=fakes.adapter()
    try:
        result=await adapter.input('type_text','bound',PORTRAIT,{},{'text':'a',**arguments})
        assert result['delivery']=='sent' and fakes.keys
        assert result['verified'] is False
    finally:await adapter.aclose()


async def test_text_entry_needs_no_accessibility_preflight_or_field_declaration(monkeypatch):
    # The ordinary_app declaration is accepted but no longer required, and no
    # accessibility traversal or advisory is consulted before typing.
    monkeypatch.delenv('TAPMESH_PHYSICAL_KEYBOARD_LAYOUT',raising=False)
    fakes=Fakes();adapter=fakes.adapter()
    try:
        for arguments in ({'text':'ok','keyboard_layout':'us'},{'text':'ok','keyboard_layout':'us','field_context':'ordinary_app'}):
            result=await adapter.input('type_text','bound',PORTRAIT,{'target':(10,20,None)},arguments)
            assert result['delivery']=='sent'
        assert not hasattr(adapter,'sensitive_state') and not hasattr(adapter,'audit')
    finally:await adapter.aclose()


async def test_persistent_session_is_reused_across_inputs():
    fakes=Fakes();adapter=fakes.adapter()
    try:
        first=await adapter.input('tap','bound',PORTRAIT,{'target':(10,20,None)},{})
        second=await adapter.input('swipe','bound',PORTRAIT,{'start':(10,20,None),'end':(10,400,None)},{'duration_ms':50})
        assert first['session']=='opened' and second['session']=='reused'
        assert fakes.tunnels_opened==1 and fakes.media_opened==1
        assert fakes.tunnels_closed==0 and fakes.media_closed==0
        await adapter.input('press_key','bound',PORTRAIT,{},{'key':'return'})
        await adapter.input('press_key','bound',PORTRAIT,{},{'key':'delete'})
        assert fakes.tunnels_opened==1 and fakes.media_opened==1
    finally:await adapter.aclose()
    assert fakes.tunnels_closed==1 and fakes.media_closed==1 and not adapter.sessions


async def test_network_transport_never_keeps_remoted_tunnel_open():
    fakes=Fakes();adapter=fakes.adapter(probe=network)
    try:
        await adapter.input('tap','bound',PORTRAIT,{'target':(10,20,None)},{})
        await adapter.input('tap','bound',PORTRAIT,{'target':(10,20,None)},{})
        assert fakes.tunnels_opened==2 and fakes.tunnels_closed==2 and not adapter.sessions
        # Reads have no fast path on the shared remoted link.
        assert await adapter.screenshot('bound') is None and fakes.tunnels_opened==2
    finally:await adapter.aclose()


async def test_native_lock_state_needs_a_warm_tunnel_and_reads_boolean_without_caching():
    fakes=Fakes();adapter=fakes.adapter()
    try:
        assert await adapter.lock_state('bound') is None and fakes.tunnels_opened==0
        assert await adapter.display_info('bound') is not None
        for observed,expected in ((False,False),(True,True),('false',None),(0,None),(None,None)):
            fakes.lock_state_reply={'passcodeRequired':observed}
            assert await adapter.lock_state('bound') is expected
        assert fakes.services_opened==['info','lock']
        assert fakes.tunnels_opened==1 and fakes.tunnels_closed==0
    finally:await adapter.aclose()


@pytest.mark.parametrize('stalled',[False,True])
async def test_native_lock_failure_only_replaces_its_own_channel(monkeypatch,stalled):
    from agent import native_physical

    async def hang():
        await asyncio.Future()

    fakes=Fakes();adapter=fakes.adapter()
    monkeypatch.setattr(native_physical,'LOCK_READ_TIMEOUT_SECONDS',.01)
    try:
        assert await adapter.screenshot('bound') is not None
        fakes.lock_state_reply=hang if stalled else RuntimeError('Unavailable')
        async with asyncio.timeout(1):
            assert await adapter.lock_state('bound') is None
        assert fakes.streaming() and fakes.tunnels_closed==0
        fakes.lock_state_reply={'passcodeRequired':True}
        assert await adapter.lock_state('bound') is True
        assert fakes.services_opened==['info','lock','lock']
    finally:await adapter.aclose()


async def test_cancelled_native_lock_query_closes_before_next_query():
    fakes=Fakes();entered=asyncio.Event();closing=asyncio.Event();allow_close=asyncio.Event()
    next_started=asyncio.Event()
    channels=[]

    @asynccontextmanager
    async def service(rsd,name):
        if name!='lock':
            async with fakes.service(rsd,name) as instance:yield instance
            return
        channel=len(channels)+1;channels.append(channel)
        class LockInfo:
            async def get_lockstate(self):
                if channel==1:
                    entered.set()
                    await asyncio.Future()
                return {'passcodeRequired':True}
        try:yield LockInfo()
        finally:
            if channel==1:
                closing.set()
                await allow_close.wait()

    adapter=fakes.adapter()
    adapter.service_factory=service
    try:
        await adapter.display_info('bound')
        async def next_query():
            next_started.set()
            return await adapter.lock_state('bound')
        async with asyncio.timeout(2),asyncio.TaskGroup() as group:
            first=group.create_task(adapter.lock_state('bound'))
            await entered.wait()
            first.cancel()
            await closing.wait()
            first.cancel()
            second=group.create_task(next_query())
            try:
                await next_started.wait()
                assert not first.done()
                assert channels==[1]
            finally:allow_close.set()
        assert second.result() is True and channels==[1,2]
        assert first.cancelled() and fakes.tunnels_opened==1
    finally:await adapter.aclose()


async def test_idle_session_closes_media_and_tunnel():
    fakes=Fakes();adapter=fakes.adapter(idle_seconds=.05)
    try:
        await adapter.input('tap','bound',PORTRAIT,{'target':(10,20,None)},{})
        assert adapter.sessions
        for _ in range(100):
            if not adapter.sessions:break
            await asyncio.sleep(.01)
        assert not adapter.sessions and fakes.media_closed==1 and fakes.tunnels_closed==1
        result=await adapter.input('tap','bound',PORTRAIT,{'target':(10,20,None)},{})
        assert result['session']=='opened' and fakes.tunnels_opened==2
    finally:await adapter.aclose()


async def test_idle_hid_closes_while_screenshots_keep_the_stream_and_tunnel():
    fakes=Fakes();adapter=fakes.adapter(idle_seconds=.15)
    try:
        await adapter.input('tap','bound',PORTRAIT,{'target':(10,20,None)},{})
        for _ in range(30):
            assert await adapter.screenshot('bound') is fakes.stream_image
            await asyncio.sleep(.02)
            if fakes.media_closed:break
        assert fakes.media_closed==1 and adapter.sessions and fakes.tunnels_closed==0
        assert fakes.streams_opened==1 and fakes.streams_closed==0 and fakes.captures==0
    finally:await adapter.aclose()
    assert fakes.streams_closed==1


async def test_error_after_send_is_unknown_never_resent_and_next_input_reopens():
    from agent.safety import BridgeError
    class Failing(HID):
        async def send_multitouch(self,contacts):
            self.fakes.frames.append(list(contacts))
            if self.fakes.media_opened==1 and contacts[0].touching:raise ConnectionResetError('transport lost')
    fakes=Fakes(lambda f:Failing(f));adapter=fakes.adapter()
    try:
        with pytest.raises(BridgeError) as error:
            await adapter.input('tap','bound',PORTRAIT,{'target':(10,20,None)},{})
        assert error.value.code=='delivery_outcome_unknown' and error.value.side_effect_state=='unknown'
        touches=[frame for frame in fakes.frames if frame[0].touching]
        assert len(touches)==1  # never automatically retransmitted
        assert not adapter.sessions and fakes.tunnels_closed==1
        result=await adapter.input('tap','bound',PORTRAIT,{'target':(10,20,None)},{})
        assert result['session']=='opened' and fakes.tunnels_opened==2
    finally:await adapter.aclose()


async def test_pre_send_failure_on_reused_session_reopens_once_and_sends_once():
    class FlakyKeyboard(HID):
        async def create_keyboard_service(self,**kwargs):
            if self.fakes.media_opened==1:raise ConnectionResetError('stale tunnel')
            return 9
    fakes=Fakes(lambda f:FlakyKeyboard(f));adapter=fakes.adapter()
    try:
        await adapter.input('tap','bound',PORTRAIT,{'target':(10,20,None)},{})
        result=await adapter.input('press_key','bound',PORTRAIT,{},{'key':'return'})
        assert result['delivery']=='sent' and result['session']=='opened'
        assert fakes.tunnels_opened==2 and fakes.keys.count([40])==1
    finally:await adapter.aclose()


async def test_cancelled_input_discards_the_session():
    started=asyncio.Event()
    class Slow(HID):
        async def send_multitouch(self,contacts):
            self.fakes.frames.append(list(contacts));started.set()
    fakes=Fakes(lambda f:Slow(f));adapter=fakes.adapter()
    try:
        task=asyncio.create_task(adapter.input('long_press','bound',PORTRAIT,{'target':(10,20,None)},{'duration_ms':5000}))
        await started.wait();task.cancel()
        with pytest.raises(asyncio.CancelledError):await task
        assert not adapter.sessions and fakes.tunnels_closed==1
        assert not fakes.frames[-1][0].touching
    finally:await adapter.aclose()


async def test_inputs_are_serialized_per_device_but_not_behind_screenshots():
    order=[];release=asyncio.Event()
    class Blocking(HID):
        async def send_multitouch(self,contacts):
            order.append('touch')
            if contacts[0].touching and len(order)==1:await release.wait()
    fakes=Fakes(lambda f:Blocking(f));adapter=fakes.adapter()
    try:
        first=asyncio.create_task(adapter.input('tap','bound',PORTRAIT,{'target':(10,20,None)},{}))
        while not order:await asyncio.sleep(0)
        second=asyncio.create_task(adapter.input('tap','bound',PORTRAIT,{'target':(10,20,None)},{}))
        # A screenshot completes while an input holds the device input lock.
        assert await asyncio.wait_for(adapter.screenshot('bound'),1) is fakes.stream_image
        await asyncio.sleep(.01);assert len(order)==1
        release.set();await first;await second
        assert fakes.tunnels_opened==1
    finally:await adapter.aclose()


async def test_screenshot_is_the_latest_stream_frame_and_never_uses_capture_service():
    fakes=Fakes();adapter=fakes.adapter()
    try:
        assert await adapter.screenshot('bound') is fakes.stream_image
        assert await adapter.screenshot('bound') is fakes.stream_image
        assert fakes.tunnels_opened==1 and fakes.streams_opened==1
        # Display pixels are read once to crop the padded video frame.
        assert fakes.services_opened==['info'] and fakes.frames_served==[(1320,2868)]*2
        assert (await adapter.display_info('bound'))['displays'][0]['pointScale']==3
        # Input reuses the same stream for touch authentication.
        await adapter.input('tap','bound',PORTRAIT,{'target':(10,20,None)},{})
        assert await adapter.screenshot('bound') is fakes.stream_image
        assert fakes.streams_opened==1 and fakes.media_opened==1
        assert fakes.captures==0 and fakes.capture_calls_while_streaming==0
    finally:await adapter.aclose()
    assert fakes.streams_closed==1 and fakes.media_closed==1


async def test_input_first_opens_the_stream_that_screenshots_then_use():
    fakes=Fakes();adapter=fakes.adapter()
    try:
        await adapter.input('tap','bound',PORTRAIT,{'target':(10,20,None)},{})
        assert fakes.streams_opened==1 and fakes.media_opened==1
        assert await adapter.screenshot('bound') is fakes.stream_image
        assert fakes.streams_opened==1 and fakes.captures==0
    finally:await adapter.aclose()


async def test_stream_unavailable_falls_back_to_capture_service_without_a_stream():
    import agent.native_physical as native
    fakes=Fakes();fakes.stream_error=RuntimeError('iOS too old');adapter=fakes.adapter()
    try:
        assert await adapter.screenshot('bound')==fakes.image
        assert await adapter.screenshot('bound')==fakes.image
        assert fakes.captures==2 and fakes.capture_calls_while_streaming==0
        # Not retried within the retry window.
        assert adapter.sessions['bound'].stream_retry_at>native.time.monotonic()
        fakes.capture_error=ConnectionResetError('lost')
        assert await adapter.screenshot('bound') is None
        assert not adapter.sessions and fakes.tunnels_closed==1
        fakes.capture_error=None
        assert await adapter.screenshot('bound')==fakes.image and fakes.tunnels_opened==2
    finally:await adapter.aclose()


async def test_stream_without_frames_only_authorizes_touch_then_closes_for_capture(monkeypatch):
    import agent.native_physical as native
    monkeypatch.setattr(native,'DEGRADED_STREAM_LINGER_SECONDS',.1)
    fakes=Fakes();fakes.stream_decodes=False;adapter=fakes.adapter()
    try:
        await adapter.input('tap','bound',PORTRAIT,{'target':(10,20,None)},{})
        assert fakes.streams_opened==1 and fakes.media_opened==1
        # Right after input the stream still authorizes touch: devicectl fallback.
        assert await adapter.screenshot('bound') is None and fakes.captures==0
        for _ in range(100):
            if fakes.streams_closed:break
            await asyncio.sleep(.01)
        assert fakes.streams_closed==1 and fakes.media_closed==1
        assert await adapter.screenshot('bound')==fakes.image
        # Screenshots never reopen a stream that cannot decode.
        assert fakes.streams_opened==1 and fakes.capture_calls_while_streaming==0
        await adapter.input('tap','bound',PORTRAIT,{'target':(10,20,None)},{})
        assert fakes.streams_opened==2 and fakes.media_opened==2
    finally:await adapter.aclose()


async def test_idle_stream_closes_and_reopens_on_next_screenshot():
    fakes=Fakes();adapter=fakes.adapter(idle_seconds=.05)
    try:
        assert await adapter.screenshot('bound') is fakes.stream_image
        for _ in range(100):
            if fakes.streams_closed:break
            await asyncio.sleep(.01)
        assert fakes.streams_closed==1 and fakes.tunnels_closed==1 and not adapter.sessions
        assert await adapter.screenshot('bound') is fakes.stream_image and fakes.streams_opened==2
    finally:await adapter.aclose()


async def test_dead_stream_is_reopened_and_its_hid_is_not_reused():
    fakes=Fakes();adapter=fakes.adapter()
    try:
        await adapter.input('tap','bound',PORTRAIT,{'target':(10,20,None)},{})
        fakes.streams[0].dead=True
        assert await adapter.screenshot('bound') is fakes.stream_image
        assert fakes.streams_opened==2 and fakes.media_closed==1
        result=await adapter.input('tap','bound',PORTRAIT,{'target':(10,20,None)},{})
        assert result['delivery']=='sent' and fakes.media_opened==2 and fakes.streams_opened==2
    finally:await adapter.aclose()


async def test_silent_stream_is_dropped_and_screenshot_falls_back():
    fakes=Fakes();adapter=fakes.adapter()
    try:
        fakes.stream_image=None
        assert await adapter.screenshot('bound') is None
        assert fakes.streams_closed==1 and fakes.captures==0
        # With no stream open, the capture service is safe again.
        assert await adapter.screenshot('bound')==fakes.image and fakes.capture_calls_while_streaming==0
    finally:await adapter.aclose()


async def test_epoch_change_closes_the_stream():
    fakes=Fakes();adapter=fakes.adapter()
    try:
        assert await adapter.screenshot('bound') is fakes.stream_image
        await adapter.close_device('bound')
        assert fakes.streams_closed==1 and not adapter.sessions
    finally:await adapter.aclose()


@pytest.mark.parametrize('resource',['stream','hid','info'])
async def test_resource_opened_for_a_discarded_session_is_closed(resource):
    from agent.native_physical import _NoFastPath
    entered,finish,closed=asyncio.Event(),asyncio.Event(),asyncio.Event()

    @asynccontextmanager
    async def blocked(*args,**kwargs):
        entered.set()
        await finish.wait()
        try:yield SimpleNamespace(dead=False)
        finally:closed.set()

    adapter=Fakes().adapter()
    if resource=='stream':adapter.stream_factory=blocked
    elif resource=='hid':adapter.hid_factory=blocked
    else:adapter.service_factory=blocked
    async with adapter._lease('bound') as session:
        operation=adapter._stream(session) if resource=='stream' else adapter._hid(session) if resource=='hid' else adapter._service(session,'info')
        opening=asyncio.create_task(operation)
        try:
            await asyncio.wait_for(entered.wait(),1)
            await adapter.close_device('bound')
            finish.set()
            with pytest.raises(_NoFastPath):await opening
            assert closed.is_set()
            assert session.stream is None and session.hid is None and not session.services
        finally:
            finish.set()
            await asyncio.gather(opening,return_exceptions=True)
            await adapter._drop_stream(session)
            await adapter._drop_service(session,'info')
            await adapter.aclose()


async def test_tunnel_open_times_out_and_releases_the_open_lock(monkeypatch):
    from agent import native_physical
    closed=asyncio.Event()

    @asynccontextmanager
    async def stalled(serial,transport):
        try:
            await asyncio.Event().wait()
            yield
        finally:closed.set()

    fakes=Fakes();adapter=fakes.adapter()
    adapter.tunnel_factory=stalled
    monkeypatch.setattr(native_physical,'TUNNEL_OPEN_TIMEOUT_SECONDS',.02,raising=False)
    try:
        assert await asyncio.wait_for(adapter.screenshot('bound'),.5) is None
        assert closed.is_set() and not adapter.sessions
        adapter.tunnel_factory=fakes.tunnel
        assert await adapter.screenshot('bound') is fakes.stream_image
    finally:await adapter.aclose()


async def test_one_userspace_tunnel_per_process_reads_never_evict_input_does():
    fakes=Fakes();adapter=fakes.adapter()
    try:
        assert await adapter.screenshot('first') is fakes.stream_image
        assert await adapter.screenshot('second') is None  # devicectl fallback
        assert set(adapter.sessions)=={'first'}
        await adapter.input('tap','second',PORTRAIT,{'target':(10,20,None)},{})
        assert set(adapter.sessions)=={'second'} and fakes.tunnels_closed==1
    finally:await adapter.aclose()


class LockRunner:
    """devicectl fixture for lock state; counts every call."""
    def __init__(self, locked=False):self.locked=locked;self.calls=[]
    async def run(self, argv, **kwargs):
        import json
        from agent.process import Output
        self.calls.append(argv[2:5])
        if argv[2:5]==['device','info','lockState']:
            return Output(json.dumps({'result':{'passcodeRequired':self.locked}}).encode(),b'',0)
        if argv[2:5]==['device','info','displays']:
            return Output(json.dumps({'result':{'displays':[{'primary':True,'pointScale':2,'bounds':[[0,0],[880,1912]]}],'orientation':{'currentDeviceOrientation':'portrait'}}}).encode(),b'',0)
        raise AssertionError('Unexpected CLI operation: '+str(argv[:5]))
    async def close(self):pass


def physical_backend(tmp_path, runner, inputs):
    from agent.backends import MacBackend
    backend=MacBackend(tmp_path,runner=runner);backend.native_serials['ios:owned']='bound'
    class Native:
        async def input(self,tool,serial,geometry,targets,args,*,guard=None,on_send=None,still_unlocked=None):
            if guard:await guard()
            on_send();inputs.append((tool,geometry));return {'delivery':'sent','verified':False}
        async def display_info(self,serial):return None
        async def lock_state(self,serial):return None
        async def screenshot(self,serial):raise AssertionError('Input must not capture the screen')
        async def close_device(self,serial):inputs.append(('closed',serial))
        async def aclose(self):pass
    backend.native_physical=Native()
    return backend


DEVICE={'device_id':'ios:owned','native_id':'owned','backend':'physical','ready':True,'connection_epoch':1}


def test_lock_cache_is_one_second():
    from agent.backends import LOCK_STATE_TTL_SECONDS
    assert LOCK_STATE_TTL_SECONDS==1.0


async def test_each_native_batch_gesture_checks_the_lock_without_the_cache(tmp_path):
    from agent.safety import BridgeError
    runner=LockRunner();fakes=Fakes()
    backend=physical_backend(tmp_path,runner,[])
    backend.native_physical=fakes.adapter()
    try:
        for locked in (False,True):
            runner.locked=locked
            context={'in_batch':True,'targets':{'target':(10,20,None)}}
            if not locked:
                await backend._native_input('tap',dict(DEVICE),{},context)
                assert context['sent'] is True
            else:
                with pytest.raises(BridgeError) as error:
                    await backend._native_input('tap',dict(DEVICE),{},context)
                assert error.value.code=='protected_action_requires_human'
                assert error.value.side_effect_state=='not_sent' and context['sent'] is False
        assert [frame[0].touching for frame in fakes.frames]==[True,False]
    finally:await backend.close()


async def test_lock_check_is_cached_briefly_and_never_captures_the_screen(tmp_path):
    runner=LockRunner();inputs=[]
    backend=physical_backend(tmp_path,runner,inputs)
    try:
        for _ in range(3):
            context={'targets':{'target':(10,20,None)}}
            await backend._native_input('tap',dict(DEVICE),{},context)
            assert context['sent'] is True
        assert runner.calls.count(['device','info','lockState'])==1
        assert len(inputs)==3 and inputs[0][1]['logical_size']=={'width':440,'height':956}
        epoch,checked_at,serial=backend.lock_states['ios:owned']
        backend.lock_states['ios:owned']=(epoch,checked_at-10,serial)
        await backend._native_input('tap',dict(DEVICE),{},{'targets':{'target':(10,20,None)}})
        assert runner.calls.count(['device','info','lockState'])==2
    finally:await backend.close()


async def test_locked_device_refuses_input_and_is_never_cached(tmp_path):
    from agent.safety import BridgeError
    runner=LockRunner(locked=True);inputs=[]
    backend=physical_backend(tmp_path,runner,inputs)
    try:
        for _ in range(2):
            context={'targets':{'target':(10,20,None)}}
            with pytest.raises(BridgeError) as error:
                await backend._native_input('tap',dict(DEVICE),{},context)
            assert error.value.code=='protected_action_requires_human'
            assert error.value.side_effect_state=='not_sent' and context['sent'] is False
        assert runner.calls.count(['device','info','lockState'])==2 and inputs==[]
        runner.locked=False
        await backend._native_input('tap',dict(DEVICE),{},{'targets':{'target':(10,20,None)}})
        assert len(inputs)==1
    finally:await backend.close()


async def test_changed_pixels_never_refuse_input(tmp_path):
    # The removed frame guard compared device pixels with the observed frame.
    # Input now proceeds whatever the screen shows, with no recapture.
    runner=LockRunner();inputs=[]
    backend=physical_backend(tmp_path,runner,inputs)
    observed={'public':{'frame_pixel_sha256':'0'*64,'observation_id':'old'},'at':0}
    try:
        for tool,targets in (('tap',{'target':(10,20,observed)}),('fill',{'target':(10,20,observed)}),('swipe',{'start':(10,20,observed),'end':(10,300,observed)})):
            result=await backend._native_input(tool,dict(DEVICE),{'text':'x','frame_validation':'text_target'},{'targets':targets})
            assert result['delivery']=='sent'
        assert [tool for tool,_ in inputs]==['tap','fill','swipe']
        assert ['device','capture','screenshot'] not in runner.calls
    finally:await backend.close()


async def test_connection_epoch_change_closes_the_input_session(tmp_path):
    from agent.safety import BridgeError

    runner=LockRunner();inputs=[]
    backend=physical_backend(tmp_path,runner,inputs)
    try:
        await backend._native_input('tap',dict(DEVICE),{},{'targets':{'target':(10,20,None)}})
        await backend._native_input('tap',{**DEVICE,'connection_epoch':2},{},{'targets':{'target':(10,20,None)}})
        assert ('closed','bound') in inputs
        # A late request bound to the old epoch never tears down the new session.
        inputs.clear()
        with pytest.raises(BridgeError) as failure:
            await backend._native_input('tap',dict(DEVICE),{},{'targets':{'target':(10,20,None)}})
        assert failure.value.code=='connection_changed' and failure.value.side_effect_state=='not_sent'
        assert inputs==[]
    finally:await backend.close()


async def test_physical_screenshot_uses_fast_path_then_devicectl_fallback(tmp_path):
    from agent.backends import MacBackend
    from agent.safety import Observations
    import base64
    from PIL import Image
    calls=[]
    backend=MacBackend(tmp_path);backend.native_serials['ios:owned']='bound'
    frame=png('blue',(1320,2868))
    class Native:
        fast=True
        async def screenshot(self,serial):return frame if self.fast else None
        async def display_info(self,serial):return {'displays':[{'primary':True,'pointScale':3,'bounds':[[0,0],[1320,2868]]}],'orientation':{'currentDeviceOrientation':'portrait'}}
        async def close_device(self,serial):pass
        async def aclose(self):pass
    native=backend.native_physical=Native()
    async def dev(args,device=None,**kwargs):
        calls.append(args[:3])
        if args[:3]==['device','capture','screenshot']:
            open(args[args.index('--destination')+1],'wb').write(frame)
            return {}
        raise AssertionError('Unexpected devicectl call '+str(args))
    backend._dev=dev
    router=SimpleNamespace(observations=Observations())
    try:
        result=await backend._screenshot('take_screenshot',dict(DEVICE),{},{'router':router,'session_id':'s'})
        assert result['source']=='coredevice' and calls==[]
        assert result['logical_size']=={'width':440,'height':956} and result['orientation']=='portrait'
        assert result['image_size']=={'width':1280,'height':2781}
        assert 'frame_pixel_sha256' not in result
        image=Image.open(io.BytesIO(base64.b64decode(result['image']['data'])))
        assert image.size==(1280,2781)
        native.fast=False
        result=await backend._screenshot('take_screenshot',dict(DEVICE),{},{'router':router,'session_id':'s'})
        assert result['source']=='devicectl' and calls==[['device','capture','screenshot']]
        assert result['logical_size']=={'width':440,'height':956}
    finally:await backend.close()


@pytest.mark.parametrize('density,frame_size', [(3,(2868,1320)),(3,(1434,660)),(None,(1434,660))])
async def test_physical_capture_waits_for_display_geometry_before_publishing_an_input_anchor(tmp_path,density,frame_size):
    from agent.backends import MacBackend
    from agent.core import Router
    from PIL import Image
    backend=MacBackend(tmp_path/'backend'); geometry=dict(PORTRAIT); taps=[]
    if density is not None:geometry['density']=density
    device={**DEVICE,'capabilities':{name:{'status':'supported'} for name in ('screen.capture','input.tap')}}
    async def discover(include_all=False):return [device]
    async def display(_):return {**geometry,'logical_size':dict(geometry['logical_size'])}
    async def capture(_):return Image.new('RGB',frame_size,'blue'),'coredevice_stream'
    async def send(tool,device,args,context):
        taps.append((tool,*context['targets']['target'][:2]))
        return {'delivery':'sent'}
    backend._discover=discover;backend.geometry=display;backend._capture=capture;backend._native_input=send
    router=Router(backend,state_dir=tmp_path/'router',fast_control=True)
    router.access.allow_device(DEVICE['device_id'])
    arguments={'device_id':DEVICE['device_id']}
    try:
        stale=await router.call('s','take_screenshot',arguments)
        assert stale['status']=='error'
        assert stale['error']['code']=='stale_observation'
        assert stale['error']['side_effect_state']=='not_sent'
        assert not router.observations.records and not taps

        geometry.update(logical_size={'width':956,'height':440},orientation='landscapeLeft')
        fresh=await router.call('s','take_screenshot',arguments)
        assert fresh['status']=='success'
        assert fresh['data']['logical_size']==geometry['logical_size']
        assert fresh['data']['orientation']=='landscapeLeft'
        target={'x':600,'y':20,'coordinate_space':'device_points','observation_id':fresh['data']['observation_id']}
        tap=await router.call('s','tap',{**arguments,'target':target})
        assert tap['status']=='success' and taps==[('tap',600,20)]

        geometry.update(PORTRAIT)
        changed=await router.call('s','tap',{**arguments,'target':target})
        assert changed['error']['code']=='stale_observation'
        assert changed['error']['side_effect_state']=='not_sent'
        assert taps==[('tap',600,20)]
    finally:await router.close()


async def test_mismatched_physical_screenshot_can_be_saved_without_an_input_anchor(tmp_path):
    from agent.artifacts import Artifacts
    from agent.backends import MacBackend
    from agent.safety import Observations
    from PIL import Image
    backend=MacBackend(tmp_path/'backend')
    async def capture(_):return Image.new('RGB',(1434,660),'blue'),'coredevice_stream'
    async def geometry(_):return PORTRAIT
    backend._capture=capture;backend.geometry=geometry
    router=SimpleNamespace(observations=Observations(),artifacts=Artifacts(tmp_path/'files'))
    try:
        result=await backend._screenshot('save_screenshot',DEVICE,{},{'router':router,'session_id':'s'})
        assert result['image_size']=={'width':1434,'height':660}
        assert result['input_geometry_qualified'] is False
        assert 'observation_id' not in result and 'image_to_device_transform' not in result
        assert not router.observations.records
        with Image.open(router.artifacts.path(result['artifact']['artifact_id'])) as image:
            assert image.size==(1434,660)
    finally:await backend.close()


async def test_read_only_devicectl_queries_do_not_wait_for_native_lock(tmp_path):
    from agent.backends import MacBackend
    runner=LockRunner()
    backend=MacBackend(tmp_path,runner=runner)
    try:
        async with backend.native_lock:
            lock=await asyncio.wait_for(backend._dev(['device','info','lockState'],DEVICE),1)
        assert lock['passcodeRequired'] is False
    finally:await backend.close()


async def test_router_physical_text_never_runs_accessibility_preflight(tmp_path):
    from test_router import DeviceFixture
    from agent.core import Router
    class PhysicalFixture(DeviceFixture):
        async def discover(self,include_all=False):
            values=await super().discover(include_all)
            values[0]['backend']='physical'
            return values
        async def sensitive_state(self,device):raise AssertionError('No accessibility preflight before text entry')
    backend=PhysicalFixture();router=Router(backend,state_dir=tmp_path)
    try:
        args={'device_id':'sim:fixture','text':'test'}
        result=await router.call('s','type_text',args)
        if result['status']=='error' and result['error']['code']=='approval_required':
            router.approvals.decide(result['error']['details']['approval_id'],'allow')
            result=await router.call('s','type_text',args)
        assert result['status']=='success'
        assert backend.calls==[('type_text',args)]
    finally:await router.close()


# ---- Live stream (RTP/HEVC -> latest frame) without a device ----------------

class FakeTransport:
    def __init__(self):self.sent=[];self.queue=asyncio.Queue()
    async def recv(self,size=65535):return await self.queue.get()
    async def sendto(self,data,ip,port):self.sent.append(bytes(data))
    def firs(self):return [p for p in self.sent if p[:2]==b'\x84\xce']


class FakeDecoder:
    """Decoder fake: each fed access unit becomes one frame image."""
    created=[]
    def __init__(self,vps,sps,pps):
        self.params=(vps,sps,pps);self.fed=[];self.closed=False;self.outputs=[];self.next_image=None
        FakeDecoder.created.append(self)
    def feed(self,nals):
        from PIL import Image
        self.fed.append(nals)
        image=self.next_image or Image.new('RGB',(1328,2896),'green')
        self.outputs.append((0,SimpleNamespace(image=image,released=False)))
    def drain(self):
        out,self.outputs=self.outputs,[];return out
    @staticmethod
    def image(handle):return handle.image
    @staticmethod
    def release(handle):handle.released=True
    def close(self):self.closed=True


def nal(kind,body=b'x'):return bytes([kind<<1,1])+body


def rtp(seq,payload,marker=True):
    import struct
    return struct.pack('!BBHII',0x80,(0x80 if marker else 0)|100,seq,seq*400,1234)+payload


def screen():
    from agent.native_physical import LiveScreen
    FakeDecoder.created.clear()
    transport=FakeTransport()
    return LiveScreen(transport,('fd00::1',5000),111,222,decoder_factory=FakeDecoder),transport


def keyframe(live,seq):
    for i,kind in enumerate((32,33,34)):live.packet(rtp(seq+i,nal(kind),marker=False))
    live.packet(rtp(seq+3,nal(19,b'idr')))
    return seq+4


async def test_live_screen_requests_keyframe_on_start_and_sends_receiver_reports():
    live,transport=screen()
    live.run()
    try:
        await asyncio.sleep(.01)
        fir=transport.firs()
        assert len(fir)==1 and len(fir[0])==20
        import struct
        assert struct.unpack('!II',fir[0][4:12])==(111,0) and struct.unpack('!I',fir[0][12:16])==(222,)
        await asyncio.sleep(1.1)
        assert any(p[:2]==b'\x81\xc9' for p in transport.sent)
    finally:await live.aclose()


async def test_live_screen_keeps_only_the_latest_frame_and_crops_padding():
    live,transport=screen()
    try:
        assert live.packet(rtp(1,nal(1,b'delta-before-key'))) is None
        assert not FakeDecoder.created  # no decoder before a keyframe
        seq=keyframe(live,2)
        decoder=FakeDecoder.created[0]
        assert len(decoder.fed)==1 and [n[0]>>1 for n in decoder.fed[0]]==[32,33,34,19]
        live.packet(rtp(seq,nal(1,b'd1')));live.packet(rtp(seq+1,nal(1,b'd2')))
        assert len(decoder.fed)==3 and live.frames==3
        image=await live.frame((1320,2868),newer_within=0)
        assert image.size==(1320,2868)
        # Older frames were released as newer ones arrived.
        assert live.latest is not None and not live.latest.released
    finally:await live.aclose()
    assert FakeDecoder.created[0].closed


async def test_live_screen_holds_last_good_frame_after_loss_until_keyframe():
    live,transport=screen()
    live.run()
    try:
        await asyncio.sleep(0)
        seq=keyframe(live,10)
        decoder=FakeDecoder.created[0]
        good=live.latest
        await asyncio.sleep(.6)  # outside the FIR rate limit
        sent=len(transport.firs())
        live.packet(rtp(seq+1,nal(1,b'after-gap')))  # seq skipped -> corrupt AU
        await asyncio.sleep(0)
        assert live.needs_key and len(transport.firs())==sent+1 and live.lost_aus==1
        live.packet(rtp(seq+2,nal(1,b'delta')))
        assert len(decoder.fed)==1 and live.latest is good  # deltas held
        keyframe(live,seq+3)
        assert not live.needs_key and len(FakeDecoder.created)==2 and decoder.closed
        assert (await live.frame((1320,2868),newer_within=0)).size==(1320,2868)
    finally:await live.aclose()


async def test_live_screen_frame_waits_for_first_frame_then_gives_up():
    live,transport=screen()
    try:
        assert await live.frame((1320,2868),first_frame_seconds=.05) is None
    finally:await live.aclose()


async def test_live_screen_without_decoder_serves_no_frames_and_sends_no_fir():
    from agent.native_physical import LiveScreen
    transport=FakeTransport()
    live=LiveScreen(transport,('fd00::1',5000),111,222,decoder_factory=FakeDecoder,decode=False)
    live.run()
    try:
        keyframe(live,1)
        assert await live.frame((1320,2868)) is None and not transport.firs()
    finally:await live.aclose()


async def test_collapsed_frame_triggers_keyframe_and_serves_the_full_resolution_frame():
    from PIL import Image
    from agent.native_physical import collapsed_extent
    collapsed=Image.new('RGB',(1328,2896),(128,128,128))
    collapsed.paste(Image.new('RGB',(660,1434),'red'),(0,0))
    assert collapsed_extent(collapsed.crop((0,0,1320,2868)))is not None
    assert collapsed_extent(Image.new('RGB',(1320,2868),'red')) is None
    assert collapsed_extent(Image.new('RGB',(1320,2868),(128,128,128))) is None  # uniform gray screen
    live,transport=screen()
    live.run()
    try:
        await asyncio.sleep(0)
        seq=keyframe(live,1)
        FakeDecoder.created[0].next_image=collapsed
        live.packet(rtp(seq,nal(1,b'collapsed')))
        sent=len(transport.firs())
        async def recover():
            await asyncio.sleep(.05)
            live.needs_key=True  # as after the FIR'd keyframe arrives
            keyframe(live,seq+1)
        task=asyncio.create_task(recover())
        image=await live.frame((1320,2868),newer_within=0)
        await task
        assert len(transport.firs())==sent+1
        assert image.getpixel((1300,2800))==(0,128,0)  # the crisp keyframe, not the gray padding
    finally:await live.aclose()


async def test_collapsed_frame_is_compensated_when_no_keyframe_arrives():
    from PIL import Image
    live,transport=screen()
    try:
        seq=keyframe(live,1)
        collapsed=Image.new('RGB',(1328,2896),(128,128,128))
        collapsed.paste(Image.new('RGB',(660,1434),'red'),(0,0))
        FakeDecoder.created[0].next_image=collapsed
        live.packet(rtp(seq,nal(1,b'collapsed')))
        image=await live.frame((1320,2868),newer_within=0)
        assert image.size==(1320,2868) and image.getpixel((1300,2800))==(255,0,0)
    finally:await live.aclose()


def test_frame_content_box_maps_padded_stream_frames_to_the_display():
    from agent.native_physical import frame_content_box,display_pixel_size
    info={'displays':[{'primary':False,'bounds':[[0,0],[0,0]]},{'primary':True,'pointScale':3,'bounds':[[0,0],[1320,2868]]}]}
    assert display_pixel_size(info)==(1320,2868)
    assert display_pixel_size({'displays':[{'primary':True,'nativeSize':[1179,2556]}]})==(1179,2556)
    assert display_pixel_size({})is None
    # Portrait: encoder pads right/bottom; content is never scaled.
    assert frame_content_box((1328,2896),(1320,2868))==(0,0,1320,2868)
    # A landscape frame is matched against the rotated display.
    assert frame_content_box((2896,1328),(1320,2868))==(0,0,2868,1320)
    # A proportionally scaled frame is used whole.
    assert frame_content_box((660,1434),(1320,2868))==(0,0,660,1434)


async def test_physical_stream_frame_keeps_observation_geometry(tmp_path):
    from agent.backends import MacBackend
    from agent.safety import Observations
    from PIL import Image
    backend=MacBackend(tmp_path);backend.native_serials['ios:owned']='bound'
    class Native:
        async def screenshot(self,serial):return Image.new('RGB',(1320,2868),'blue')
        async def display_info(self,serial):return {'displays':[{'primary':True,'pointScale':3,'bounds':[[0,0],[1320,2868]]}],'orientation':{'currentDeviceOrientation':'portrait'}}
        async def close_device(self,serial):pass
        async def aclose(self):pass
    backend.native_physical=Native()
    async def dev(*args,**kwargs):raise AssertionError('No devicectl call when the stream serves the frame')
    backend._dev=dev
    router=SimpleNamespace(observations=Observations(),artifacts=None)
    try:
        result=await backend._screenshot('take_screenshot',dict(DEVICE),{},{'router':router,'session_id':'s'})
        assert result['source']=='coredevice_stream'
        assert result['logical_size']=={'width':440,'height':956} and result['orientation']=='portrait'
        assert result['image_size']=={'width':1280,'height':2781}
        transform=result['image_to_device_transform']
        assert abs(transform['a']*1280-440)<1e-6 and abs(transform['d']*2781-956)<1e-6
    finally:await backend.close()


async def test_screenshot_encoding_does_not_block_the_loop_or_resize_shared_frame(monkeypatch,tmp_path):
    import threading
    from agent.backends import MacBackend
    from agent.safety import Observations
    from PIL import Image
    frame=Image.new('RGB',(440,956),'blue')
    loop=asyncio.get_running_loop();started=asyncio.Event();release=threading.Event()
    original=Image.Image.save
    def slow_save(image,*args,**kwargs):
        loop.call_soon_threadsafe(started.set)
        release.wait(.5)
        return original(image,*args,**kwargs)
    monkeypatch.setattr(Image.Image,'save',slow_save)
    backend=MacBackend(tmp_path)
    async def capture(device):return frame,'coredevice_stream'
    async def geometry(device):return PORTRAIT
    backend._capture=capture;backend.geometry=geometry
    context={'router':SimpleNamespace(observations=Observations()),'session_id':'viewer'}
    task=asyncio.create_task(backend._screenshot('take_screenshot',DEVICE,{'max_width':220},context))
    try:
        await asyncio.wait_for(started.wait(),1)
        assert not task.done()
    finally:
        release.set()
        result=await task
        await backend.close()
    assert result['effective_width']==220
    assert frame.size==(440,956)


async def test_post_keyframe_refinement_is_not_motion_but_real_motion_settles_with_one_fir():
    import agent.native_physical as native
    clock=[100.0]
    FakeDecoder.created.clear()
    transport=FakeTransport()
    live=native.LiveScreen(transport,('fd00::1',5000),111,222,decoder_factory=FakeDecoder,clock=lambda:clock[0])
    live.run()
    try:
        await asyncio.sleep(0)
        seq=keyframe(live,1)
        sent=len(transport.firs())
        # Refinement burst right after the keyframe: large deltas, static screen.
        for i in range(20):
            clock[0]+=.02;live.packet(rtp(seq,nal(1,b'r'*12000)));seq+=1
        clock[0]+=2;await asyncio.sleep(.6)
        assert len(transport.firs())==sent
        # Sustained motion, then quiet: exactly one settle keyframe request.
        for i in range(30):
            clock[0]+=.02;live.packet(rtp(seq,nal(1,b'm'*12000)));seq+=1
        await asyncio.sleep(.3)
        clock[0]+=1;await asyncio.sleep(.3);clock[0]+=.5;await asyncio.sleep(.6)
        assert len(transport.firs())==sent+1
    finally:await live.aclose()
