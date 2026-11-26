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


def add(name, props, required, capability, policy='O', mutation=False, device=True, *, description, maximum_timeout=30000, **schema_extra):
    props = deepcopy(props)
    if device: props['device_id'] = string(256)
    props['timeout_ms'] = integer(1,maximum_timeout)
    CATALOG[name] = {'name':name,'description':description, 'inputSchema':obj(props, required, **schema_extra), 'capability':capability,'policy':policy,'mutation':mutation,'device_bound':device,'maximum_timeout_ms':maximum_timeout}

add('list_devices', {'include_all_simulators':BOOL}, (), 'device.discover', device=False,
    description="Discover iPhones, iPads, and iOS simulators with readiness and tool availability. Use the returned device ID with select_device to set this client's target.")
add('start_simulator', {'simulator_id':string(256),'device_type_id':string(256),'runtime_id':string(256),'name':string(100),'select':BOOL}, (), 'simulator.boot','C',True,False, oneOf=[{'required':['simulator_id'],'not':{'anyOf':[{'required':['device_type_id']},{'required':['runtime_id']},{'required':['name']}]}},{'required':['device_type_id','runtime_id'],'not':{'required':['simulator_id']}}], maximum_timeout=180000,
    description='Boot an existing iOS simulator by ID, or create one from a device type and installed iOS runtime. Returns the booted device ID and whether it was created.')
add('shut_down_simulator', {}, (), 'simulator.shutdown','C',True,
    description='Shut down a selected simulator while keeping its installed apps and files. A successful result confirms the simulator stopped.')
add('select_device', {'device_id':string(256)}, ('device_id',), 'device.select',device=False,
    description="Set this client's target to a device ID from list_devices. Selection returns readiness and leaves the device in its current power state.")
add('get_device_info', {'refresh':BOOL}, (), 'device.info',
    description="Read the selected device's OS, readiness, and supported operations. Use its capability details to choose an input or capture method.")
add('take_screenshot', {'format':enum('jpeg','png'),'max_width':integer(320,4096),'quality':number(.1,1),'require_fresh':BOOL}, (), 'screen.capture',
    description='Capture the selected device screen without saving an artifact. The returned image and observation ID give you the current coordinates for input.')
add('save_screenshot', {'format':enum('png','jpeg'),'full_resolution':BOOL,**EXPORT}, (), 'screen.capture','S',
    description='Save an image of the selected device to TapMesh or an approved folder. Returns the finalized artifact reference after owner approval.')
add('start_recording', {**EXPORT,'max_duration_seconds':integer(1,3600),'resume_after_reconnect':{'const':False}}, (), 'screen.record','S',True,
    description='Start a bounded video recording on a device that supports screen recording. Get owner approval and keep the returned recording ID for stop_recording.')
add('stop_recording', {'recording_id':string(256)}, ('recording_id',),'screen.record','S',True,
    description='Finalize a recording from this session using its recording ID. The result includes saved segments and reports any gaps or partial files.')
add('get_foreground_app', {}, (), 'apps.foreground',
    description='Identify the app currently in front on the selected device. The result reports unknown if the backend cannot observe it.')
add('list_elements', {'source':enum('auto','accessibility','ocr'),'max_elements':integer(1,1000),'include_invisible':BOOL}, (), 'ui.inspect',
    description='Inspect the selected device using accessibility or screen text recognition. The response provides a snapshot ID and element references for later input.')
add('tap', {'target':TARGET}, ('target',),'input.tap','C',True,
    description='Tap one point or element on the selected device. Use a current take_screenshot observation for coordinates or a list_elements snapshot for a reference.')
add('tap_text', {'match':MATCH,'source':enum('auto','accessibility','ocr')}, ('match',),'input.tap','C',True,
    description='Find unique visible text from a fresh inspection and tap it. Choose a more specific match or an occurrence if the text appears more than once.')
add('double_tap', {'target':TARGET,'interval_ms':integer(40,1000)}, ('target',),'input.double_tap','C',True,
    description='Send two taps to one target with a configurable interval. Obtain coordinates from take_screenshot or an element reference from list_elements.')
add('long_press', {'target':TARGET,'duration_ms':integer(100,10000)}, ('target',),'input.long_press','C',True,
    description='Hold and release a touch at one target for the requested duration. Anchor the target to take_screenshot or list_elements before sending it.')
add('swipe', {'start':TARGET,'end':TARGET,'duration_ms':integer(50,10000)}, ('start','end'),'input.swipe','C',True,
    description='Move one touch from start to end over a specified duration. Both endpoints must come from the same take_screenshot or list_elements observation.')
add('scroll', {'direction':enum('up','down','left','right'),'distance_points':number(1,10000),'container_ref':string(),'snapshot_id':string()}, ('direction','distance_points'),'input.scroll','C',True, dependentRequired={'container_ref':['snapshot_id'],'snapshot_id':['container_ref']},
    description='Scroll in one direction by a bounded distance. For a specific container, call list_elements first and pass its reference and snapshot ID.')
add('scroll_to', {'match':MATCH,'selector':SELECTOR,'direction':enum('up','down','left','right'),'max_steps':integer(1,25)}, ('direction',),'input.scroll','C',True,oneOf=[{'required':['match'],'not':{'required':['selector']}},{'required':['selector'],'not':{'required':['match']}}],maximum_timeout=120000,
    description='Inspect and scroll in bounded steps until matching text or an element comes into view. Supply one text match or element selector and a direction.')
add('drag', {'start':TARGET,'end':TARGET,'hold_ms':integer(100,10000),'duration_ms':integer(50,10000)}, ('start','end'),'input.drag','C',True,
    description='Press, hold, then move and release a touch between two points. Obtain both endpoints from the same take_screenshot or list_elements observation.')
add('pinch', {'center':TARGET,'scale':number(.25,4),'duration_ms':integer(50,10000)}, ('center','scale'),'input.pinch','C',True,
    description='Send a two-finger scale gesture around a target on a supported device. Use take_screenshot for the center coordinates or list_elements for a reference.')
TEXT_OPTIONS={'input_method':{**enum('keyboard','clipboard'),'default':'keyboard'},'keyboard_layout':enum('us'),'field_context':enum('ordinary_app')}
TEXT_RULES=[{'if':{'properties':{'input_method':{'const':'clipboard'}},'required':['input_method']},'then':{'not':{'required':['keyboard_layout']}}}]
add('type_text', {'text':TEXT,'target':TARGET,**TEXT_OPTIONS}, ('text',),'input.text','C',True,allOf=TEXT_RULES,
    description='Insert text at the focused field or at a target from take_screenshot or list_elements. Physical keyboard input needs the US layout. Clipboard paste requires approval.')
FRAME_RULE={'if':{'properties':{'frame_validation':{'const':'text_target'}},'required':['frame_validation']},
            'then':{'properties':{'field_context':{'const':'ordinary_app'},'submit':{'const':False}},'required':['field_context']}}
add('fill', {'target':TARGET,'text':TEXT,'submit':BOOL,**TEXT_OPTIONS,
             'frame_validation':{**enum('exact','text_target'),'default':'exact',
              'description':'Compatibility declaration. Physical input never compares screen frames before sending, so both values behave the same on a physical device. text_target requires ordinary_app and no submit. Simulator supports exact only.'}},
    ('target','text'),'input.fill','C',True,allOf=[*TEXT_RULES,FRAME_RULE],
    description='Replace text at a target from take_screenshot or list_elements, with optional submit. Check the actual field value after sending. Clipboard paste requires approval.')
add('press_key', {'key':enum('return','delete','escape','tab','space','up','down','left','right','select_all'),'modifiers':{'type':'array','items':enum('shift','control','alt','command'),'uniqueItems':True,'maxItems':4}}, ('key',),'input.key','C',True,
    description='Send a supported key or modifier chord to the focused app on the selected device. Observe the screen afterward to check how the app handled it.')
add('hide_keyboard', {}, (), 'input.keyboard_dismiss','C',True,
    description='Ask the selected device to dismiss its on-screen keyboard using a supported input method. Check the next image to confirm the keyboard closed.')
add('press_button', {'button':enum('home','lock','side_button','siri','volume_up','volume_down','apple_pay')}, ('button',),'system.button','C',True,
    description='Send a named hardware or system button action supported by the selected device. The device may require a person to handle a protected button.')
add('system_gesture', {'gesture':enum('home','app_switcher','control_center','notification_center')}, ('gesture',),'system.gesture','C',True,
    description='Invoke a supported system gesture, such as Home, Control Center, or the app switcher. Inspect the device afterward to confirm it opened.')
add('go_back', {'strategy':enum('auto','navigation_element','edge_gesture')}, (), 'navigation.back','C',True,
    description='Go back using a supported navigation element or edge gesture. The result identifies the strategy used or reports that no supported target exists.')
add('rotate', {'orientation':enum('portrait','portrait_upside_down','landscape_left','landscape_right')}, ('orientation',),'display.rotate','C',True,
    description='Request a supported device orientation and read the effective orientation when available. Refresh the screen before using coordinates after rotation.')
add('set_appearance', {'appearance':enum('light','dark','system')}, ('appearance',),'settings.appearance','C',True,
    description='Set light, dark, or system appearance on a supported device. Inspect its screen if you need to confirm how the active app applied the change.')
add('set_accessibility', {'content_size_category':enum('extra-small','small','medium','large','extra-large','extra-extra-large','extra-extra-extra-large','accessibility-medium','accessibility-large','accessibility-extra-large','accessibility-extra-extra-large','accessibility-extra-extra-extra-large'),'reduce_motion':BOOL,'bold_text':BOOL}, (), 'settings.accessibility','C',True,anyOf=[{'required':[p]} for p in ('content_size_category','reduce_motion','bold_text')],
    description='Update supported accessibility settings on the selected device. Supply at least one option, such as Reduce Motion or a text-size category.')
add('list_apps', {'include_system':BOOL,'query':string(255)}, (), 'apps.list',
    description='List apps on the selected device, with an optional query or inclusion of system apps. Use the returned bundle IDs to target later app operations.')
add('launch_app', {'bundle_id':BUNDLE,'arguments':{'type':'array','items':string(4096),'maxItems':64},'environment':obj({'LANG':string(128),'LC_ALL':string(128),'TAPMESH_FIXTURE_MODE':string(128)})}, ('bundle_id',),'apps.launch','C',True,
    description='Open an installed app by its bundle ID from list_apps. Providing launch arguments or environment requires approval. Check the foreground app afterward.')
add('terminate_app', {'bundle_id':BUNDLE}, ('bundle_id',),'apps.terminate','C',True,
    description='Stop the installed app with the given bundle ID. Use list_apps to identify the app and read the result to see whether stopping was confirmed.')
add('install_app', {'artifact_path':string(4096),'replace':BOOL}, ('artifact_path',),'apps.install','S',True,
    description='Install an app from an approved local file path. Obtain approval for installation or destructive confirmation if you request replacement.')
add('uninstall_app', {'bundle_id':BUNDLE}, ('bundle_id',),'apps.uninstall','D',True,
    description='Remove the app identified by bundle ID and its device data. Confirm the correct app with list_apps and obtain destructive approval first.')
add('open_settings', {'section':enum('root')}, (), 'system.open_settings','C',True,
    description='Open the Settings app on a supported device. Request the root screen and observe the device afterward to verify what opened.')
add('open_url', {'url':string(8192)}, ('url',),'apps.open_url','C',True,
    description='Send a URL to the selected device for its default app or registered handler. Non-HTTP links need approval. Observe the device to confirm the destination.')
add('build_and_run', {'project_path':string(4096),'scheme':string(255),'configuration':enum('Debug','Release'),'launch':BOOL}, ('project_path','scheme'),'build.xcode','H',True,maximum_timeout=1800000,
    description='Build an Xcode project or workspace from an approved folder and install the resulting app on the selected device. Review the stage results and build log.')
add('set_location', {'latitude':number(-90,90),'longitude':number(-180,180),'altitude':number(-1000,100000)}, ('latitude','longitude'),'location.simulate','S',True,
    description="Set the selected device's simulated location on a supported backend. Obtain sensitive-action approval before applying the device-wide override.")
add('clear_location', {}, (), 'location.simulate','C',True,
    description='Remove an existing simulated location override on a supported device. Use this when the location test ends to restore normal location behavior.')
add('get_clipboard', {'format':enum('text')}, (), 'clipboard.read','S',
    description="Read text from the selected device's clipboard with owner approval. The clipboard may contain content synchronized through Universal Clipboard.")
add('set_clipboard', {'text':TEXT}, ('text',),'clipboard.write','S',True,
    description="Replace text in the selected device's clipboard with owner approval. The new text may sync to other nearby Apple devices.")
add('list_crashes', {'bundle_id':BUNDLE,'since':string(40,format='date-time'),'limit':integer(1,200),'cursor':string(256)}, (), 'diagnostics.crash_list','S',
    description='List crash report metadata with owner approval. Filter by app or date and use a returned report ID with get_crash to retrieve it.')
add('get_crash', {'crash_id':string(256),'symbolicate':BOOL,'symbols_path':string(4096),'destination':string(4096)}, ('crash_id',),'diagnostics.crash_read','S',
    description='Retrieve a crash report by its ID from list_crashes. Owner approval is required. Optional symbolication needs matching symbols in an approved location.')
add('download_app_container', {'bundle_id':BUNDLE,'destination':string(4096),'filename':string(255)}, ('bundle_id',),'apps.container_export','S',
    description='Archive the accessible data container for a development app. Supply its bundle ID and obtain owner approval before writing the export.')
add('request_human_action', {'reason':string(300),'instructions':string(2000),'resume_condition':CONDITIONS}, ('reason','instructions'),'workflow.handoff','U',False,maximum_timeout=120000,
    description='Ask the owner to complete a step on the device, such as handling a protected prompt. Provide instructions without credentials or other secrets.')
add('handle_alert', {'snapshot_id':string(),'button_ref':string(),'button_label':string(4096)}, ('snapshot_id',),'ui.alert','C',True,oneOf=[{'required':['button_ref'],'not':{'required':['button_label']}},{'required':['button_label'],'not':{'required':['button_ref']}}],
    description='Choose a specific button on an ordinary alert using a current list_elements snapshot. Ask the owner to handle protected prompts.')
add('wait_for', {'condition':CONDITIONS,'poll_interval_ms':integer(100,10000)}, ('condition',),'workflow.observe',maximum_timeout=120000,
    description='Observe a selected device until a supported condition is met or the timeout expires. Conditions can check visible text, an element, foreground app, or readiness.')
add('wait', {'duration_ms':integer(0,120000)}, ('duration_ms',),'workflow.delay',device=False,maximum_timeout=120000,
    description='Pause this call for a bounded duration. Returns the elapsed time and leaves device selection and device state alone.')
BATCH_EXCLUDED = {'batch_commands','build_and_run','request_human_action','list_devices','start_simulator','shut_down_simulator','select_device','start_recording','stop_recording'}
BATCH_ALLOWED = set(CATALOG) - BATCH_EXCLUDED
children = [obj({'tool':{'const':n}, 'arguments':deepcopy(CATALOG[n]['inputSchema'])},('tool','arguments')) for n in sorted(BATCH_ALLOWED)]
add('batch_commands', {'commands':{'type':'array','minItems':1,'maxItems':25,'items':{'oneOf':children}},'stop_on_error':BOOL}, ('commands',),'workflow.batch','C',True,maximum_timeout=120000,
    description='Run up to 25 allowed commands in order on one device. The batch uses the highest approval policy among its steps and returns each outcome without rollback.')


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
