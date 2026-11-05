"""Opt-in tests act only on a named disposable qualification simulator + owned app."""
import asyncio
import os
import pytest

pytestmark=pytest.mark.skipif(not os.environ.get('TAPMESH_QUALIFICATION_DEVICE'),reason='Requires the project-owned disposable simulator and fixture app')


async def test_live_fixture_capture_ax_unicode_input_recording_and_workflows(tmp_path):
    from agent.backends import MacBackend
    from agent.core import Router
    did=os.environ['TAPMESH_QUALIFICATION_DEVICE']
    router=Router(MacBackend(tmp_path),state_dir=tmp_path)
    async def invoke(tool,args=None):
        arguments={'device_id':did,**(args or {})}
        result=await router.call('qualification',tool,arguments)
        if result.get('error',{}).get('code')=='approval_required':
            router.approvals.decide(result['error']['details']['approval_id'],'allow')
            result=await router.call('qualification',tool,arguments)
        return result
    async def ok(tool,args=None):
        result=await invoke(tool,args)
        assert result['status']=='success',(tool,result)
        return result['data']
    async def target(identifier):
        for attempt in range(8):
            result=await invoke('list_elements',{'source':'accessibility','max_elements':1000})
            if result['status']=='success': break
            await asyncio.sleep(.3)
        assert result['status']=='success',result
        snapshot=result['data']
        elements=[e for e in snapshot['elements'] if e.get('identifier')==identifier]
        assert len(elements)==1,(identifier,elements)
        return {'ref':elements[0]['ref'],'snapshot_id':snapshot['snapshot_id']}
    try:
        devices=await router.devices(True)
        device=next(d for d in devices if d['device_id']==did)
        assert device['backend']=='simulator' and 'Qualification' in device['name']
        await ok('launch_app',{'bundle_id':'io.tapmesh.fixture'})
        await asyncio.sleep(.4)
        screenshot=await ok('take_screenshot',{'max_width':640})
        assert screenshot['image_size']['width']==640
        assert screenshot['logical_size']['width']==402
        saved=await ok('save_screenshot')
        assert router.artifacts.path(saved['artifact']['artifact_id']).is_file()
        apps=await ok('list_apps',{'query':'io.tapmesh.fixture'})
        assert apps['apps'][0]['bundle_id']=='io.tapmesh.fixture'
        await ok('tap',{'target':await target('increment-button')})
        # References issued before the preceding mutation cannot be reused.
        old=await target('increment-button')
        await ok('tap',{'target':old})
        assert (await invoke('tap',{'target':old}))['error']['code']=='stale_element_reference'
        fill=await ok('fill',{'target':await target('replace-field'),'text':'TapMesh تجربة'})
        assert fill['value_verified'] is True
        await ok('tap',{'target':await target('name-field')})
        await ok('type_text',{'text':'Hello'})
        await ok('press_key',{'key':'delete'})
        await ok('hide_keyboard')
        await ok('terminate_app',{'bundle_id':'io.tapmesh.fixture'})
        await ok('launch_app',{'bundle_id':'io.tapmesh.fixture'})
        await asyncio.sleep(1.5)
        duplicate=await invoke('tap_text',{'match':{'text':'Duplicate','match':'exact'}})
        assert duplicate['error']['code']=='ambiguous_text_match'
        await ok('double_tap',{'target':await target('increment-button')})
        await ok('long_press',{'target':await target('increment-button'),'duration_ms':150})
        await ok('pinch',{'center':await target('gesture-surface'),'scale':1.2,'duration_ms':300})
        snapshot=await ok('list_elements')
        def coord(x,y): return {'x':x,'y':y,'coordinate_space':'device_points','observation_id':snapshot['observation_id']}
        await ok('drag',{'start':coord(80,680),'end':coord(180,680),'hold_ms':150,'duration_ms':300})
        await ok('set_appearance',{'appearance':'dark'})
        await ok('set_accessibility',{'content_size_category':'large'})
        rejected=await invoke('set_accessibility',{'content_size_category':'small','bold_text':True})
        assert rejected['error']['code']=='unsupported_capability'
        await ok('set_location',{'latitude':24.7136,'longitude':46.6753})
        await ok('clear_location')
        await ok('set_clipboard',{'text':'TapMesh fixture clipboard'})
        assert (await ok('get_clipboard'))['text']=='TapMesh fixture clipboard'
        rec=await ok('start_recording',{'max_duration_seconds':10})
        await ok('tap',{'target':await target('increment-button')})
        await asyncio.sleep(.8)
        stopped=await ok('stop_recording',{'recording_id':rec['recording_id']})
        assert len(stopped['segments'])==1
        assert stopped['segments'][0]['duration_seconds']>0
        await ok('wait_for',{'condition':{'type':'element_present','selector':{'identifier':'increment-button'}},'timeout_ms':1000})
        batch=await ok('batch_commands',{'commands':[{'tool':'set_appearance','arguments':{'appearance':'light'}},{'tool':'list_apps','arguments':{'query':'io.tapmesh.fixture'}}]})
        assert batch['executed_steps']==2
        exported=await ok('download_app_container',{'bundle_id':'io.tapmesh.fixture'})
        assert exported['artifact']['size_bytes']>0
    finally: await router.close()


async def test_live_fixture_navigation_alert_lifecycle_and_install(tmp_path):
    from agent.backends import MacBackend
    from agent.core import Router
    did=os.environ['TAPMESH_QUALIFICATION_DEVICE']; project=os.environ.get('TAPMESH_QUALIFICATION_PROJECT'); app_path=os.environ.get('TAPMESH_QUALIFICATION_APP')
    roots=[str(__import__('pathlib').Path(p).parent) for p in (project,app_path) if p]
    router=Router(MacBackend(tmp_path),state_dir=tmp_path,build_roots=roots,export_roots=roots)
    async def invoke(tool,args=None,device=True):
        a=({'device_id':did} if device else {})|dict(args or {})
        result=await router.call('qualification2',tool,a)
        if result.get('error',{}).get('code')=='approval_required':
            router.approvals.decide(result['error']['details']['approval_id'],'allow')
            result=await router.call('qualification2',tool,a)
        assert result['status']=='success',(tool,result)
        return result['data']
    async def elements():
        for _ in range(10):
            r=await router.call('qualification2','list_elements',{'device_id':did,'max_elements':1000})
            if r['status']=='success' and any(e.get('identifier')=='increment-button' for e in r['data']['elements']): return r['data']
            await asyncio.sleep(.2)
        raise AssertionError(r)
    try:
        device=next(d for d in await router.devices(True) if d['device_id']==did)
        assert device['backend']=='simulator' and 'Qualification' in device['name']
        await invoke('list_devices',{},False)
        await invoke('select_device',{'device_id':did},False)
        await invoke('get_device_info')
        await invoke('wait',{'duration_ms':5},False)
        await invoke('terminate_app',{'bundle_id':'io.tapmesh.fixture'})
        await invoke('launch_app',{'bundle_id':'io.tapmesh.fixture'})
        await elements()
        foreground=await invoke('get_foreground_app')
        assert foreground['bundle_id']=='io.tapmesh.fixture'
        await invoke('tap_text',{'match':{'text':'Increment','match':'exact'}})
        snapshot=await elements()
        def coord(x,y): return {'x':x,'y':y,'coordinate_space':'device_points','observation_id':snapshot['observation_id']}
        await invoke('swipe',{'start':coord(201,700),'end':coord(201,400),'duration_ms':300})
        await invoke('scroll',{'direction':'up','distance_points':250})
        await invoke('scroll_to',{'selector':{'identifier':'increment-button'},'direction':'up','max_steps':8})
        await invoke('press_button',{'button':'home'})
        await invoke('system_gesture',{'gesture':'home'})
        await invoke('rotate',{'orientation':'landscape_left'})
        await invoke('rotate',{'orientation':'portrait'})
        await invoke('open_settings',{'section':'root'})
        await invoke('open_url',{'url':'https://example.com'})
        await invoke('launch_app',{'bundle_id':'io.tapmesh.fixture'})
        # Human handoff is acknowledged only via separate local authority.
        pending=await router.call('qualification2','request_human_action',{'device_id':did,'reason':'Fixture handoff','instructions':'Acknowledge this project-owned test action.'})
        assert pending['error']['code']=='protected_action_requires_human'
        router.approvals.decide(pending['error']['details']['approval_id'],'allow')
        await invoke('request_human_action',{'reason':'Fixture handoff','instructions':'Acknowledge this project-owned test action.'})
        await invoke('list_crashes',{'bundle_id':'io.tapmesh.fixture'})
        if app_path:
            await invoke('uninstall_app',{'bundle_id':'io.tapmesh.fixture'})
            installed=await invoke('install_app',{'artifact_path':app_path})
            assert installed['bundle_id']=='io.tapmesh.fixture'
        if project:
            await invoke('uninstall_app',{'bundle_id':'io.tapmesh.fixture'})
            built=await invoke('build_and_run',{'project_path':project,'scheme':'TapMeshFixture','timeout_ms':180000})
            assert built['bundle_id']=='io.tapmesh.fixture'
        await invoke('shut_down_simulator')
        await invoke('start_simulator',{'simulator_id':did,'select':True,'timeout_ms':180000},False)
        await invoke('launch_app',{'bundle_id':'io.tapmesh.fixture'})
    finally: await router.close()


async def test_live_fixture_alert_and_crash_report(tmp_path):
    from agent.backends import MacBackend
    from agent.core import Router
    did=os.environ['TAPMESH_QUALIFICATION_DEVICE']; router=Router(MacBackend(tmp_path),state_dir=tmp_path)
    async def invoke(tool,args=None):
        a={'device_id':did,**(args or {})};r=await router.call('qualification3',tool,a)
        if r.get('error',{}).get('code')=='approval_required':
            router.approvals.decide(r['error']['details']['approval_id'],'allow');r=await router.call('qualification3',tool,a)
        assert r['status']=='success',(tool,r)
        return r['data']
    try:
        device=next(d for d in await router.devices(True) if d['device_id']==did)
        assert 'Qualification' in device['name'] and device['backend']=='simulator'
        # Existing owned modal is accepted so a prior interrupted test can recover.
        snapshot=await invoke('list_elements',{'max_elements':1000})
        if not any(e.get('label')=='Fixture alert' for e in snapshot['elements']):
            await invoke('tap_text',{'match':{'text':'Show alert','match':'exact'}})
            await asyncio.sleep(.5);snapshot=await invoke('list_elements',{'max_elements':1000})
        await invoke('handle_alert',{'snapshot_id':snapshot['snapshot_id'],'button_label':'Continue'})
        await invoke('scroll_to',{'selector':{'identifier':'crash-fixture'},'direction':'down','max_steps':12})
        snapshot=await invoke('list_elements',{'max_elements':1000})
        crash=next(e for e in snapshot['elements'] if e.get('identifier')=='crash-fixture')
        await invoke('tap',{'target':{'ref':crash['ref'],'snapshot_id':snapshot['snapshot_id']}})
        for _ in range(12):
            reports=await invoke('list_crashes',{'bundle_id':'io.tapmesh.fixture'})
            if reports['reports']: break
            await asyncio.sleep(.5)
        assert reports['reports'],reports
        report=await invoke('get_crash',{'crash_id':reports['reports'][0]['crash_id']})
        assert report['artifact']['size_bytes']>0
        await invoke('launch_app',{'bundle_id':'io.tapmesh.fixture'})
    finally: await router.close()


async def test_live_fixture_platform_back_button_returns_to_root(tmp_path):
    from agent.backends import MacBackend
    from agent.core import Router
    did=os.environ['TAPMESH_QUALIFICATION_DEVICE'];router=Router(MacBackend(tmp_path),state_dir=tmp_path)
    async def invoke(tool,args=None):
        arguments={'device_id':did,**(args or {})};result=await router.call('qualification4',tool,arguments)
        if result.get('error',{}).get('code')=='approval_required':
            router.approvals.decide(result['error']['details']['approval_id'],'allow');result=await router.call('qualification4',tool,arguments)
        assert result['status']=='success',(tool,result)
        return result['data']
    try:
        device=next(d for d in await router.devices(True) if d['device_id']==did)
        assert device['backend']=='simulator' and 'Qualification' in device['name']
        await invoke('tap_text',{'match':{'text':'Open detail','match':'exact'}})
        await asyncio.sleep(.5)
        returned=await invoke('go_back')
        assert returned['strategy']=='navigation_element'
        await asyncio.sleep(.4)
        snapshot=await invoke('list_elements')
        assert any(e.get('identifier')=='increment-button' for e in snapshot['elements'])
    finally:await router.close()
