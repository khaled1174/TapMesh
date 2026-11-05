"""The single authoritative, closed input catalog for HTTP, MCP and native UI."""
from copy import deepcopy
import json
import math
from jsonschema import Draft202012Validator, ValidationError


def obj(properties=None, required=(), **extra):
    return {'type': 'object', 'properties': properties or {}, 'required': list(required), 'additionalProperties': False, **extra}


def string(maximum=1024, **extra):
    return {'type': 'string', 'minLength': 1, 'maxLength': maximum, **extra}


def enum(*values):
    return {'type': 'string', 'enum': list(values)}


def number(low, high):
    return {'type': 'number', 'minimum': low, 'maximum': high}


def integer(low, high):
    return {'type': 'integer', 'minimum': low, 'maximum': high}

BOOL = {'type': 'boolean'}
TEXT = {'type': 'string', 'maxLength': 65536}
BUNDLE = string(255, pattern=r'^[A-Za-z0-9][A-Za-z0-9._-]+$')
TARGET = {'oneOf': [obj({'ref': string(), 'snapshot_id': string()}, ('ref','snapshot_id')), obj({'x': number(0,16384),'y': number(0,16384),'coordinate_space':enum('device_points'),'observation_id': string()}, ('x','y','coordinate_space','observation_id'))]}
MATCH = obj({'text': string(4096), 'match': enum('exact','contains'),'case_sensitive':BOOL,'occurrence':integer(1,1000)}, ('text','match'))
SELECTOR = obj({'label': string(4096), 'role':string(128),'identifier':string(1024)}, (), minProperties=1)
EXPORT = {'destination':string(4096),'filename':string(255),'overwrite':{'const':False}}
CONDITIONS = {'oneOf': [
    obj({'type':{'const':kind},'match':MATCH,'source':enum('auto','accessibility','ocr')}, ('type','match')) for kind in ('text_present','text_absent')
] + [obj({'type':{'const':'element_present'},'selector':SELECTOR,'source':enum('auto','accessibility')}, ('type','selector')), obj({'type':{'const':'foreground_app'},'bundle_id':BUNDLE}, ('type','bundle_id')),obj({'type':{'const':'device_ready'}}, ('type',))]}
CATALOG = {}


def add(name, props, required, capability, policy='O', mutation=False, device=True, description='', maximum_timeout=30000, **schema_extra):
    props = deepcopy(props)
    if device: props['device_id'] = string(256)
    props['timeout_ms'] = integer(1,maximum_timeout)
    CATALOG[name] = {'name':name,'description':description or name.replace('_',' ') + '. Results distinguish delivery from verified application success.', 'inputSchema':obj(props, required, **schema_extra), 'capability':capability,'policy':policy,'mutation':mutation,'device_bound':device,'maximum_timeout_ms':maximum_timeout}

add('list_devices', {'include_all_simulators':BOOL}, (), 'device.discover', device=False)
add('start_simulator', {'simulator_id':string(256),'device_type_id':string(256),'runtime_id':string(256),'name':string(100),'select':BOOL}, (), 'simulator.boot','C',True,False, oneOf=[{'required':['simulator_id'],'not':{'anyOf':[{'required':['device_type_id']},{'required':['runtime_id']},{'required':['name']}]}},{'required':['device_type_id','runtime_id'],'not':{'required':['simulator_id']}}], maximum_timeout=180000)
add('shut_down_simulator', {}, (), 'simulator.shutdown','C',True)
add('select_device', {'device_id':string(256)}, ('device_id',), 'device.select',device=False)
add('get_device_info', {'refresh':BOOL}, (), 'device.info')
add('take_screenshot', {'format':enum('jpeg','png'),'max_width':integer(320,4096),'quality':number(.1,1),'require_fresh':BOOL}, (), 'screen.capture')
add('save_screenshot', {'format':enum('png','jpeg'),'full_resolution':BOOL,**EXPORT}, (), 'screen.capture','S')
add('start_recording', {**EXPORT,'max_duration_seconds':integer(1,3600),'resume_after_reconnect':{'const':False}}, (), 'screen.record','S',True)
add('stop_recording', {'recording_id':string(256)}, ('recording_id',),'screen.record','S',True)
add('get_foreground_app', {}, (), 'apps.foreground')
add('list_elements', {'source':enum('auto','accessibility','ocr'),'max_elements':integer(1,1000),'include_invisible':BOOL}, (), 'ui.inspect')
add('tap', {'target':TARGET}, ('target',),'input.tap','C',True)
add('tap_text', {'match':MATCH,'source':enum('auto','accessibility','ocr')}, ('match',),'input.tap','C',True)
add('double_tap', {'target':TARGET,'interval_ms':integer(40,1000)}, ('target',),'input.double_tap','C',True)
add('long_press', {'target':TARGET,'duration_ms':integer(100,10000)}, ('target',),'input.long_press','C',True)
add('swipe', {'start':TARGET,'end':TARGET,'duration_ms':integer(50,10000)}, ('start','end'),'input.swipe','C',True)
add('scroll', {'direction':enum('up','down','left','right'),'distance_points':number(1,10000),'container_ref':string(),'snapshot_id':string()}, ('direction','distance_points'),'input.scroll','C',True, dependentRequired={'container_ref':['snapshot_id'],'snapshot_id':['container_ref']})
add('scroll_to', {'match':MATCH,'selector':SELECTOR,'direction':enum('up','down','left','right'),'max_steps':integer(1,25)}, ('direction',),'input.scroll','C',True,oneOf=[{'required':['match'],'not':{'required':['selector']}},{'required':['selector'],'not':{'required':['match']}}],maximum_timeout=120000)
add('drag', {'start':TARGET,'end':TARGET,'hold_ms':integer(100,10000),'duration_ms':integer(50,10000)}, ('start','end'),'input.drag','C',True)
add('pinch', {'center':TARGET,'scale':number(.25,4),'duration_ms':integer(50,10000)}, ('center','scale'),'input.pinch','C',True)
TEXT_OPTIONS={'input_method':{**enum('keyboard','clipboard'),'default':'keyboard'},'keyboard_layout':enum('us'),'field_context':enum('ordinary_app')}
TEXT_RULES=[{'if':{'properties':{'input_method':{'const':'clipboard'}},'required':['input_method']},'then':{'not':{'required':['keyboard_layout']}}}]
add('type_text', {'text':TEXT,'target':TARGET,**TEXT_OPTIONS}, ('text',),'input.text','C',True,allOf=TEXT_RULES)
add('fill', {'target':TARGET,'text':TEXT,'submit':BOOL,**TEXT_OPTIONS}, ('target','text'),'input.fill','C',True,allOf=TEXT_RULES)
add('press_key', {'key':enum('return','delete','escape','tab','space','up','down','left','right','select_all'),'modifiers':{'type':'array','items':enum('shift','control','alt','command'),'uniqueItems':True,'maxItems':4}}, ('key',),'input.key','C',True)
add('hide_keyboard', {}, (), 'input.keyboard_dismiss','C',True)
add('press_button', {'button':enum('home','lock','side_button','siri','volume_up','volume_down','apple_pay')}, ('button',),'system.button','C',True)
add('system_gesture', {'gesture':enum('home','app_switcher','control_center','notification_center')}, ('gesture',),'system.gesture','C',True)
add('go_back', {'strategy':enum('auto','navigation_element','edge_gesture')}, (), 'navigation.back','C',True)
add('rotate', {'orientation':enum('portrait','portrait_upside_down','landscape_left','landscape_right')}, ('orientation',),'display.rotate','C',True)
add('set_appearance', {'appearance':enum('light','dark','system')}, ('appearance',),'settings.appearance','C',True)
add('set_accessibility', {'content_size_category':enum('extra-small','small','medium','large','extra-large','extra-extra-large','extra-extra-extra-large','accessibility-medium','accessibility-large','accessibility-extra-large','accessibility-extra-extra-large','accessibility-extra-extra-extra-large'),'reduce_motion':BOOL,'bold_text':BOOL}, (), 'settings.accessibility','C',True,anyOf=[{'required':[p]} for p in ('content_size_category','reduce_motion','bold_text')])
add('list_apps', {'include_system':BOOL,'query':string(255)}, (), 'apps.list')
add('launch_app', {'bundle_id':BUNDLE,'arguments':{'type':'array','items':string(4096),'maxItems':64},'environment':obj({'LANG':string(128),'LC_ALL':string(128),'TAPMESH_FIXTURE_MODE':string(128)})}, ('bundle_id',),'apps.launch','C',True)
add('terminate_app', {'bundle_id':BUNDLE}, ('bundle_id',),'apps.terminate','C',True)
add('install_app', {'artifact_path':string(4096),'replace':BOOL}, ('artifact_path',),'apps.install','S',True)
add('uninstall_app', {'bundle_id':BUNDLE}, ('bundle_id',),'apps.uninstall','D',True)
add('open_settings', {'section':enum('root')}, (), 'system.open_settings','C',True)
add('open_url', {'url':string(8192)}, ('url',),'apps.open_url','C',True)
add('build_and_run', {'project_path':string(4096),'scheme':string(255),'configuration':enum('Debug','Release'),'launch':BOOL}, ('project_path','scheme'),'build.xcode','H',True,maximum_timeout=1800000)
add('set_location', {'latitude':number(-90,90),'longitude':number(-180,180),'altitude':number(-1000,100000)}, ('latitude','longitude'),'location.simulate','C',True)
add('clear_location', {}, (), 'location.simulate','C',True)
add('get_clipboard', {'format':enum('text')}, (), 'clipboard.read','S')
add('set_clipboard', {'text':TEXT}, ('text',),'clipboard.write','S',True)
add('list_crashes', {'bundle_id':BUNDLE,'since':string(40,format='date-time'),'limit':integer(1,200),'cursor':string(256)}, (), 'diagnostics.crash_list','S')
add('get_crash', {'crash_id':string(256),'symbolicate':BOOL,'symbols_path':string(4096),'destination':string(4096)}, ('crash_id',),'diagnostics.crash_read','S')
add('download_app_container', {'bundle_id':BUNDLE,'destination':string(4096),'filename':string(255)}, ('bundle_id',),'apps.container_export','S')
add('request_human_action', {'reason':string(300),'instructions':string(2000),'resume_condition':CONDITIONS}, ('reason','instructions'),'workflow.handoff','U',False,maximum_timeout=120000)
add('handle_alert', {'snapshot_id':string(),'button_ref':string(),'button_label':string(4096)}, ('snapshot_id',),'ui.alert','C',True,oneOf=[{'required':['button_ref'],'not':{'required':['button_label']}},{'required':['button_label'],'not':{'required':['button_ref']}}])
add('wait_for', {'condition':CONDITIONS,'poll_interval_ms':integer(100,10000)}, ('condition',),'workflow.observe',maximum_timeout=120000)
add('wait', {'duration_ms':integer(0,120000)}, ('duration_ms',),'workflow.delay',device=False,maximum_timeout=120000)
BATCH_EXCLUDED = {'batch_commands','build_and_run','request_human_action','list_devices','start_simulator','shut_down_simulator','select_device','start_recording','stop_recording'}
BATCH_ALLOWED = set(CATALOG) - BATCH_EXCLUDED
children = [obj({'tool':{'const':n}, 'arguments':deepcopy(CATALOG[n]['inputSchema'])},('tool','arguments')) for n in sorted(BATCH_ALLOWED)]
add('batch_commands', {'commands':{'type':'array','minItems':1,'maxItems':25,'items':{'oneOf':children}},'stop_on_error':BOOL}, ('commands',),'workflow.batch','C',True,maximum_timeout=120000)


class ContractError(ValueError):
    pass


def validate(name, arguments):
    if name not in CATALOG: raise ContractError('Unknown tool')
    try:
        # JSON's nonfinite extensions are never legal public arguments.
        json.dumps(arguments, allow_nan=False)
        Draft202012Validator(CATALOG[name]['inputSchema'], format_checker=Draft202012Validator.FORMAT_CHECKER).validate(arguments)
    except (ValidationError, ValueError, TypeError) as exc:
        raise ContractError('Arguments do not satisfy the closed tool schema') from exc
    if name=='drag' and arguments.get('hold_ms',300)+arguments.get('duration_ms',700)>10000: raise ContractError('Total drag gesture duration exceeds 10 seconds')
    def check_text(value):
        if isinstance(value,str) and '\x00' in value: raise ContractError('NUL characters are not permitted')
        if isinstance(value,dict):
            for key,item in value.items():
                if key == 'text' and isinstance(item,str) and len(item.encode('utf-8')) > 65536: raise ContractError('Text exceeds 64 KiB UTF-8')
                check_text(item)
        elif isinstance(value,list):
            for item in value: check_text(item)
    check_text(arguments)
    return arguments


def public_catalog():
    return [deepcopy(tool) for tool in CATALOG.values()]
