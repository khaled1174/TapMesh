import pytest


async def test_physical_launch_keeps_backend_options_before_app_argument_separator(tmp_path):
    from agent.backends import MacBackend
    from agent.process import Output
    class CaptureRunner:
        async def run(self, argv, **kwargs):
            delimiter = argv.index('--')
            assert argv[delimiter + 1:] == ['--device', 'literal-app-value']
            backend_options = argv[:delimiter]
            assert backend_options[backend_options.index('--device') + 1] == 'owned-coredevice'
            assert backend_options.index('--device') < backend_options.index('io.tapmesh.fixture')
            assert backend_options.index('--json-output') < backend_options.index('io.tapmesh.fixture')
            assert '--json-output' in backend_options
            assert '--timeout' in backend_options
            return Output(b'{"result":{"process":{"processIdentifier":123}}}', b'', 0)
        async def close(self): pass
    backend = MacBackend(tmp_path, runner=CaptureRunner())
    try:
        result = await backend.invoke('launch_app', {'backend': 'physical', 'native_id': 'owned-coredevice', 'device_id': 'ios:owned-coredevice', 'ready': True},
                                      {'bundle_id': 'io.tapmesh.fixture', 'arguments': ['--device', 'literal-app-value']}, {'router': object()})
        assert result['process_id'] == 123 and result['verified'] is True
    finally:
        await backend.close()


async def test_physical_read_only_inspection_never_walks_accessibility_focus(tmp_path):
    from agent.backends import MacBackend
    from agent.safety import BridgeError
    backend = MacBackend(tmp_path)
    calls = []
    async def forbidden(*args, **kwargs):
        calls.append(True)
        raise AssertionError('Read-only inspection must not move device focus')
    backend.native_physical.audit = forbidden
    backend.geometry = forbidden
    try:
        with pytest.raises(BridgeError) as failed:
            await backend.invoke('list_elements', {'backend': 'physical', 'native_id': 'owned', 'ready': True}, {'source': 'accessibility'}, {'router': object()})
        assert failed.value.code == 'unsupported_capability'
        assert failed.value.side_effect_state == 'not_sent'
        assert calls == []
    finally:
        await backend.close()


async def test_physical_epoch_ignores_lazy_transport_dates_but_tracks_availability(tmp_path):
    from agent.backends import MacBackend
    from agent.process import Output
    backend = MacBackend(tmp_path)
    samples = iter([
        ('disconnected', 1, 'booted'), ('connected', 2, 'booted'),
        ('unavailable', 2, 'booted'), ('disconnected', 3, 'booted'),
        ('disconnected', 3, 'shutdown'),
    ])
    async def sim(*args, **kwargs): return Output(b'{"devices":{}}', b'', 0)
    async def dev(*args, **kwargs):
        state, date, boot = next(samples)
        return {'devices': [{'identifier':'owned', 'properties': {
            'hardware': {'reality':'physical', 'platform':'iOS', 'udid':'bound'},
            'connection': {'pairingState':'paired', 'state':state, 'lastConnectionDate':date},
            'state': {'name':'Fixture', 'bootState':boot},
        }}]}
    backend._sim = sim
    backend._dev = dev
    try:
        epochs = [(await backend.discover(True))[0]['connection_epoch'] for _ in range(5)]
        assert epochs == [1, 1, 2, 3, 4]
    finally:
        await backend.close()


def test_device_normalization_does_not_leak_pairing_identifiers(tmp_path):
    from agent.backends import normalize_physical
    device=normalize_physical({'identifier':'UUID','properties':{'hardware':{'reality':'physical','platform':'iOS','productType':'iPhone19,7','serialNumber':'SECRET','udid':'SECRET-UDID'},'connection':{'pairingState':'paired','state':'disconnected','lastConnectionDate':1},'state':{'name':'Fixture','bootState':'booted'}}},1)
    assert device['device_id']=='ios:UUID'
    assert 'SECRET' not in str(device)
    assert device['backend']=='physical'


def test_ax_elements_preserve_source_roles_and_no_ocr_fabrication():
    from agent.backends import normalize_ax
    raw=[{'AXLabel':'Save','type':'Button','role':'AXButton','frame':{'x':1,'y':2,'width':3,'height':4},'pid':8,'AXUniqueId':'save'}]
    result=normalize_ax(raw,False)
    assert result[0]['source']=='accessibility'
    assert result[0]['role']=='AXButton'
    assert result[0]['bounds']=={'x':1,'y':2,'width':3,'height':4}


async def test_simulator_create_requires_explicit_creation_fields(tmp_path):
    from agent.backends import MacBackend
    from agent.safety import BridgeError
    backend=MacBackend(tmp_path)
    with pytest.raises(BridgeError):
        await backend.invoke('start_simulator',None,{'name':'not-enough'}, {})
    await backend.close()


def test_physical_explicit_point_scale_derives_qualified_geometry():
    from agent.backends import display_geometry
    assert display_geometry({'displays':[{'primary':True,'bounds':[[0,0],[1320,2868]],'pointScale':3}],'orientation':{'currentDeviceOrientation':'portrait'}})=={'logical_size':{'width':440,'height':956},'orientation':'portrait','density':3}


def test_ax_invisible_filter_removes_elements_outside_display():
    from agent.backends import normalize_ax
    raw=[{'AXLabel':'Offscreen','role':'AXButton','frame':{'x':10,'y':300,'width':30,'height':30}}]
    assert normalize_ax(raw,False,logical_size={'width':100,'height':200})==[]
    assert len(normalize_ax(raw,True,logical_size={'width':100,'height':200}))==1


@pytest.mark.parametrize('state,bounds,expected', [
    ({'currentDeviceOrientation':'faceUp','currentDeviceNonFlatOrientation':'portrait'}, [2752,2064], {'width':1032,'height':1376}),
    ({'currentDeviceOrientation':'landscapeLeft'}, [2064,2752], {'width':1376,'height':1032}),
])
def test_physical_geometry_aligns_framebuffer_axes_with_explicit_orientation(state,bounds,expected):
    from agent.backends import display_geometry
    result=display_geometry({'displays':[{'primary':True,'bounds':[[0,0],bounds],'pointScale':2}],'orientation':state})
    assert result['logical_size']==expected
    assert result['orientation']==state.get('currentDeviceNonFlatOrientation',state['currentDeviceOrientation'])


def test_flat_device_without_nonflat_metadata_never_invents_input_orientation():
    from agent.backends import display_geometry
    result=display_geometry({'displays':[{'bounds':[[0,0],[2752,2064]],'pointScale':2}],'orientation':{'currentDeviceOrientation':'faceUp'}})
    assert result['orientation']=='faceUp'


async def test_unicode_hid_rejection_precedes_focus_or_any_backend_call(tmp_path):
    from agent.backends import MacBackend
    from agent.safety import BridgeError
    backend=MacBackend(tmp_path)
    try:
        with pytest.raises(BridgeError) as failure:
            await backend._input('type_text',{'backend':'simulator','native_id':'fixture'},{'text':'تجربة'},{'targets':{'target':(10,20,None)}})
        assert failure.value.code=='unsupported_capability'
        assert not backend.runner.processes and not backend.companions
    finally:await backend.close()


def test_complete_idb_schema_preserves_canonical_element_identity_and_value():
    from agent.backends import normalize_ax
    result=normalize_ax({'elements':[{'label':'Name','type':'TextField','identifier':'name-field','value':'Hello','frame':{'x':1,'y':2,'width':30,'height':20}}]})
    assert result[0]['label']=='Name'
    assert result[0]['identifier']=='name-field'
    assert result[0]['value']=='Hello'
