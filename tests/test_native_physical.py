import asyncio
from contextlib import asynccontextmanager
import pytest


def test_device_point_geometry_maps_to_native_uint16_without_clamping():
    from agent.native_physical import to_hid
    from agent.safety import BridgeError
    assert to_hid(220,478,{'width':440,'height':956})==(32768,32768)
    assert to_hid(0,0,{'width':440,'height':956})==(0,0)
    for point in [(440,10),(-1,2),(2,float('nan'))]:
        with pytest.raises(BridgeError):to_hid(*point,{'width':440,'height':956})


async def test_cancelled_multitouch_releases_every_contact_and_reports_no_success():
    from agent.native_physical import TouchController
    class HID:
        def __init__(self):self.frames=[];self.down=asyncio.Event()
        async def send_multitouch(self,contacts):
            self.frames.append(contacts)
            if any(c.touching for c in contacts):self.down.set()
    hid=HID();control=TouchController(hid)
    task=asyncio.create_task(control.gesture([(100,200,500,600),(800,900,700,600)],duration=10,steps=60))
    await hid.down.wait();task.cancel()
    with pytest.raises(asyncio.CancelledError):await task
    assert {c.identity for c in hid.frames[-1]}=={0,1}
    assert all(c.touching is False for c in hid.frames[-1])


async def test_bound_rsd_identity_mismatch_closes_without_hid_entry():
    from agent.native_physical import NativePhysical
    from agent.safety import BridgeError
    closed=[];entered=[]
    @asynccontextmanager
    async def tunnel(serial):
        try:yield type('RSD',(),{'udid':'other-device'})()
        finally:closed.append(True)
    @asynccontextmanager
    async def media(rsd):
        entered.append(True);yield object()
    adapter=NativePhysical(tunnel_factory=tunnel,media_factory=media)
    with pytest.raises(BridgeError) as result:
        await adapter.input('tap','expected',{'logical_size':{'width':440,'height':956},'orientation':'portrait'}, {'target':(10,20,None)}, {})
    assert result.value.code=='connection_changed'
    assert result.value.side_effect_state=='not_sent'
    assert closed==[True] and entered==[]


async def test_observed_landscape_left_uses_display_normalized_points_and_releases():
    from agent.native_physical import NativePhysical
    frames=[]
    @asynccontextmanager
    async def tunnel(serial): yield type('RSD',(),{'udid':serial})()
    class HID:
        async def send_multitouch(self,contacts): frames.append(list(contacts))
    @asynccontextmanager
    async def media(rsd): yield HID()
    result=await NativePhysical(tunnel_factory=tunnel,media_factory=media).input(
        'tap','bound',{'logical_size':{'width':1376,'height':1032},'orientation':'landscapeLeft'},
        {'target':(344,258,None)}, {})
    assert frames[0][0].x==49151 and frames[0][0].y==16384
    assert all(not contact.touching for contact in frames[-1])
    assert result['verified'] is False


async def test_input_orientation_and_dimensions_must_agree_before_media():
    from agent.native_physical import NativePhysical
    from agent.safety import BridgeError
    with pytest.raises(BridgeError) as failed:
        await NativePhysical().input('tap','bound',{'logical_size':{'width':1376,'height':1032},'orientation':'portrait'}, {'target':(75,282,None)}, {})
    assert failed.value.code=='stale_observation' and failed.value.side_effect_state=='not_sent'


async def test_media_auth_failure_never_claims_input_was_sent():
    from agent.native_physical import NativePhysical
    from agent.safety import BridgeError
    @asynccontextmanager
    async def tunnel(serial):yield type('RSD',(),{'udid':serial})()
    @asynccontextmanager
    async def media(rsd):
        raise TimeoutError('private backend detail')
        yield
    adapter=NativePhysical(tunnel_factory=tunnel,media_factory=media)
    with pytest.raises(BridgeError) as result:
        await adapter.input('tap','expected',{'logical_size':{'width':440,'height':956},'orientation':'portrait'}, {'target':(10,20,None)}, {})
    assert result.value.code=='prerequisite_required'
    assert result.value.side_effect_state=='not_sent'
    assert 'private backend detail' not in result.value.message


def test_unlocated_ax_captions_do_not_gain_fabricated_frames_or_roles():
    from agent.native_physical import normalize_audit
    item=normalize_audit('Save','Button',None,'save')
    assert item['bounds'] is None
    assert item['role']=='Button'
    assert item['source']=='accessibility'
    assert item['actionable'] is False


def test_frame_hash_ignores_png_metadata_but_detects_content_change():
    from agent.native_physical import frame_hash
    from PIL import Image,PngImagePlugin
    import io
    def encoded(color,note):
        im=Image.new('RGBA',(2,1),color);out=io.BytesIO();meta=PngImagePlugin.PngInfo();meta.add_text('note',note);im.save(out,format='PNG',pnginfo=meta);return out.getvalue()
    assert frame_hash(encoded('red','one'))==frame_hash(encoded('red','two'))
    assert frame_hash(encoded('red','one'))!=frame_hash(encoded('blue','one'))


async def test_literal_text_requires_explicit_confirmed_layout_before_focus(monkeypatch):
    from agent.native_physical import NativePhysical
    from agent.safety import BridgeError
    monkeypatch.delenv('TAPMESH_PHYSICAL_KEYBOARD_LAYOUT',raising=False)
    entered=[]
    @asynccontextmanager
    async def never_open(serial):
        entered.append(True);raise AssertionError('Should not open transport');yield
    with pytest.raises(BridgeError) as result:
        await NativePhysical(tunnel_factory=never_open).input('type_text','not-opened',{'logical_size':{'width':440,'height':956},'orientation':'portrait'},{'target':(10,20,None)},{'text':'test'})
    assert result.value.code=='prerequisite_required'
    assert result.value.side_effect_state=='not_sent'


@pytest.mark.parametrize('lock,labels,expected',[(True,['TapMesh test device'],'protected'),(None,['TapMesh test device'],'unknown'),(False,[],'unknown'),(False,['Password, secure text field'],'protected'),(False,['Unqualified app caption'],'unqualified'),(False,['TapMesh test device','Enter name, text field'],'clear')])
def test_sensitive_state_fails_closed_for_lock_protection_or_unknown(lock,labels,expected):
    from agent.native_physical import classify_sensitivity
    assert classify_sensitivity(lock,labels)['state']==expected


async def test_changed_content_rejects_native_target_before_transport(tmp_path):
    from agent.backends import MacBackend
    from agent.native_physical import frame_hash
    from agent.safety import BridgeError
    from PIL import Image
    import io
    def png(color):
        b=io.BytesIO();Image.new('RGB',(2,2),color).save(b,format='PNG');return b.getvalue()
    backend=MacBackend(tmp_path);backend.native_serials['ios:fixture']='bound'
    async def geometry(device):return {'logical_size':{'width':440,'height':956},'orientation':'portrait'}
    async def screenshot(device):return png('blue')
    async def dev(*args,**kwargs):return {'passcodeRequired':False}
    backend._dev=dev;backend.geometry=geometry;backend._raw_screenshot=screenshot
    try:
        with pytest.raises(BridgeError) as error:
            await backend._native_input('tap',{'device_id':'ios:fixture'}, {}, {'targets':{'target':(10,20,{'public':{'frame_pixel_sha256':frame_hash(png('red'))}})}})
        assert error.value.code=='stale_observation'
        assert error.value.side_effect_state=='not_sent'
    finally:await backend.close()


@pytest.mark.parametrize('state,expected',[('protected','protected_action_requires_human'),('unknown','prerequisite_required')])
async def test_router_physical_text_guard_never_sends_for_unsafe_state(tmp_path,state,expected):
    from test_router import DeviceFixture
    from agent.core import Router
    class PhysicalFixture(DeviceFixture):
        async def discover(self,include_all=False):
            values=await super().discover(include_all)
            values[0]['backend']='physical'
            return values
        async def sensitive_state(self,device):return {'state':state,'reason':'Guard result'}
    backend=PhysicalFixture();router=Router(backend,state_dir=tmp_path)
    try:
        args={'device_id':'sim:fixture','text':'test'}
        pending=await router.call('s','type_text',args)
        router.approvals.decide(pending['error']['details']['approval_id'],'allow')
        result=await router.call('s','type_text',args)
        assert result['error']['code']==expected
        assert result['error']['side_effect_state']=='not_sent'
        assert backend.calls==[]
    finally:await router.close()


async def test_modifier_is_sent_before_key_and_released_after_key():
    from agent.native_physical import TouchController
    class HID:
        def __init__(self):self.frames=[]
        async def create_keyboard_service(self,**kwargs):return 77
        async def send_keyboard(self,kid,usages):self.frames.append((kid,list(usages)))
    hid=HID();control=TouchController(hid)
    await control.key([225,23])
    assert hid.frames==[(77,[225]),(77,[225,23]),(77,[225]),(77,[])]


async def test_explicit_clipboard_method_is_unicode_and_never_a_keyboard_fallback(monkeypatch):
    from agent.native_physical import NativePhysical
    events=[]
    @asynccontextmanager
    async def tunnel(serial):yield type('RSD',(),{'udid':serial})()
    class HID:
        async def create_keyboard_service(self,**kwargs):return 4
        async def send_keyboard(self,kid,usages):events.append(('keys',list(usages)))
    @asynccontextmanager
    async def media(rsd):yield HID()
    class Clipboard:
        async def set_text(self,text):events.append(('clipboard',text))
    @asynccontextmanager
    async def clipboard(rsd):yield Clipboard()
    monkeypatch.delenv('TAPMESH_PHYSICAL_KEYBOARD_LAYOUT',raising=False)
    result=await NativePhysical(tunnel_factory=tunnel,media_factory=media,clipboard_factory=clipboard).input('type_text','bound',{'logical_size':{'width':440,'height':956},'orientation':'portrait'},{},{'text':'TapMesh تجربة','input_method':'clipboard'})
    assert result['input_method']=='clipboard' and result['clipboard_changed'] is True
    assert result['verified'] is False
    assert events[0]==('clipboard','TapMesh تجربة')
    assert ('keys',[227]) in events
    assert ('keys',[227,25]) in events


def test_black_or_transparent_capture_is_not_an_observable_input_surface():
    from agent.native_physical import frame_unavailable
    from PIL import Image
    import io
    def png(color):
        out=io.BytesIO();Image.new('RGBA',(2,2),color).save(out,format='PNG');return out.getvalue()
    assert frame_unavailable(png((0,0,0,255))) is True
    assert frame_unavailable(png((255,0,0,0))) is True
    assert frame_unavailable(png((255,255,255,255))) is False


async def test_send_callback_marks_the_first_actual_report_only():
    from agent.native_physical import NativePhysical
    called=[]
    @asynccontextmanager
    async def tunnel(serial):yield type('RSD',(),{'udid':serial})()
    class HID:
        async def send_multitouch(self,contacts):assert called
    @asynccontextmanager
    async def media(rsd):yield HID()
    result=await NativePhysical(tunnel_factory=tunnel,media_factory=media).input('tap','bound',{'logical_size':{'width':440,'height':956},'orientation':'portrait'},{'target':(20,20,None)},{},on_send=lambda:called.append(True))
    assert result['delivery']=='sent' and called


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


@pytest.mark.parametrize('locked,labels,expected',[(True,['Name'],'protected_action_requires_human'),(None,['Name'],'prerequisite_required'),(False,[],'prerequisite_required'),(False,['Password, secure text field'],'protected_action_requires_human'),(False,['Apple account password'],'protected_action_requires_human')])
def test_ordinary_declaration_never_overrides_protected_or_unknown(locked,labels,expected):
    from agent.native_physical import classify_sensitivity,validate_text_context
    from agent.safety import BridgeError
    with pytest.raises(BridgeError) as failure:
        validate_text_context(classify_sensitivity(locked,labels),{'field_context':'ordinary_app'})
    assert failure.value.code==expected
    assert failure.value.side_effect_state=='not_sent'


def test_only_observed_unqualified_context_can_use_owner_ordinary_declaration():
    from agent.native_physical import classify_sensitivity,validate_text_context
    from agent.safety import BridgeError
    advisory=classify_sensitivity(False,['Ordinary search field'])
    assert advisory['state']=='unqualified' and advisory['confirmation_required'] is True
    with pytest.raises(BridgeError):validate_text_context(advisory,{})
    with pytest.raises(BridgeError):validate_text_context({'state':'unqualified'},{'field_context':'ordinary_app'})
    result=validate_text_context(advisory,{'field_context':'ordinary_app'})
    assert result=={'field_context':'ordinary_app','qualification_source':'owner_confirmed_advisory'}


async def test_per_action_us_layout_and_ordinary_context_are_reported_without_verified_claim(monkeypatch):
    from agent.native_physical import NativePhysical,classify_sensitivity
    monkeypatch.delenv('TAPMESH_PHYSICAL_KEYBOARD_LAYOUT',raising=False)
    events=[]
    @asynccontextmanager
    async def tunnel(serial):yield type('RSD',(),{'udid':serial})()
    class HID:
        async def create_keyboard_service(self,**kwargs):return 8
        async def send_keyboard(self,kid,usages):events.append(list(usages))
    @asynccontextmanager
    async def media(rsd):yield HID()
    result=await NativePhysical(tunnel_factory=tunnel,media_factory=media).input('type_text','bound',{'logical_size':{'width':440,'height':956},'orientation':'portrait'},{},{'text':'test','keyboard_layout':'us','field_context':'ordinary_app'},text_context=classify_sensitivity(False,['Search field']))
    assert events
    assert result['field_context']=='ordinary_app'
    assert result['qualification_source']=='owner_confirmed_advisory'
    assert result['verified'] is False


async def test_native_preflight_keeps_protected_context_blocked_despite_both_declarations(monkeypatch):
    from agent.native_physical import NativePhysical,classify_sensitivity
    from agent.safety import BridgeError
    opened=[]
    @asynccontextmanager
    async def never_open(serial):opened.append(True);yield object()
    with pytest.raises(BridgeError) as failure:
        await NativePhysical(tunnel_factory=never_open).input('type_text','bound',{'logical_size':{'width':440,'height':956},'orientation':'portrait'},{},{'text':'test','keyboard_layout':'us','field_context':'ordinary_app'},text_context=classify_sensitivity(False,['Secure text password']))
    assert failure.value.code=='protected_action_requires_human'
    assert opened==[]


@pytest.mark.parametrize('environment,arguments', [('us',{}),(None,{'keyboard_layout':'us'})])
async def test_confirmed_us_layout_can_be_local_or_per_action(monkeypatch,environment,arguments):
    from agent.native_physical import NativePhysical,classify_sensitivity
    if environment:monkeypatch.setenv('TAPMESH_PHYSICAL_KEYBOARD_LAYOUT',environment)
    else:monkeypatch.delenv('TAPMESH_PHYSICAL_KEYBOARD_LAYOUT',raising=False)
    reports=[]
    @asynccontextmanager
    async def tunnel(serial):yield type('RSD',(),{'udid':serial})()
    class HID:
        async def create_keyboard_service(self,**kwargs):return 8
        async def send_keyboard(self,kid,usages):reports.append(list(usages))
    @asynccontextmanager
    async def media(rsd):yield HID()
    result=await NativePhysical(tunnel_factory=tunnel,media_factory=media).input('type_text','bound',{'logical_size':{'width':440,'height':956},'orientation':'portrait'},{},{'text':'a',**arguments},text_context=classify_sensitivity(False,['TapMesh test device']))
    assert result['delivery']=='sent' and reports
    assert result['verified'] is False


async def test_ordinary_declaration_without_advisory_cannot_open_transport():
    from agent.native_physical import NativePhysical
    from agent.safety import BridgeError
    opened=[]
    @asynccontextmanager
    async def never_open(serial):opened.append(True);yield object()
    with pytest.raises(BridgeError) as failure:
        await NativePhysical(tunnel_factory=never_open).input('type_text','bound',{'logical_size':{'width':440,'height':956},'orientation':'portrait'},{},{'text':'test','input_method':'clipboard','field_context':'ordinary_app'})
    assert failure.value.code=='prerequisite_required'
    assert opened==[]


@pytest.mark.parametrize('label',['API key','Stored secret value','Passphrase','Recovery key','One-time code'])
def test_stored_secret_markers_cannot_be_reclassified_as_ordinary(label):
    from agent.native_physical import classify_sensitivity,validate_text_context
    from agent.safety import BridgeError
    with pytest.raises(BridgeError) as failure:
        validate_text_context(classify_sensitivity(False,[label]),{'field_context':'ordinary_app'})
    assert failure.value.code=='protected_action_requires_human'
