"""Bound physical iOS input through public pymobiledevice3 APIs (11.50.0).

No discovery fallback, auto input retry, proprietary runner, or raw pairing logs.
"""
import asyncio
from contextlib import AsyncExitStack, asynccontextmanager, suppress
import math
import hashlib
import io
import os
import re
from typing import NamedTuple
from .safety import BridgeError, utcnow


def frame_hash(content):
    from PIL import Image
    with Image.open(io.BytesIO(content)) as image:
        image.load()
        return hashlib.sha256((str(image.width)+'x'+str(image.height)+':RGBA:').encode()+image.convert('RGBA').tobytes()).hexdigest()


def frame_unavailable(content):
    from PIL import Image
    with Image.open(io.BytesIO(content)) as image:
        rgba=image.convert('RGBA')
        return rgba.convert('RGB').getbbox() is None or rgba.getchannel('A').getbbox() is None


def classify_sensitivity(locked, labels):
    observed={'locked':locked,'ax_observed':bool(labels),'source':'native_accessibility_audit','observed_at':utcnow()}
    if locked is True:return {**observed,'state':'protected','reason':'Device requires passcode or unlock handoff'}
    if locked is not False or not labels:return {**observed,'state':'unknown','reason':'Lock or foreground accessibility state is unavailable'}
    text=' '.join(labels).casefold()
    protected=('passcode','password','passphrase','secure text','securetext','apple account','apple id','trust this','face id','touch id','purchase','payment','swipe up to unlock','private key','privatekey','secret key','stored secret','secret value','api key','apikey','seed phrase','recovery phrase','recovery key','verification code','one-time code','two-factor','رمز الدخول','كلمة السر','كلمة المرور')
    if any(word in text for word in protected):return {**observed,'state':'protected','reason':'Native accessibility reports a protected or secure state'}
    if 'TapMesh test device' not in labels:
        return {**observed,'state':'unqualified','confirmation_required':True,'reason':'Observed accessibility has no recognized protected marker, but ordinary field semantics require owner confirmation'}
    return {**observed,'state':'clear','scope':'project_fixture_qualified','confirmation_required':False}


def validate_text_context(advisory, arguments):
    """Check advisory state; this helper never supplies runtime authorization.

    The router separately requires the canonical C/S action approval. An
    ordinary-app declaration is evidence of the owner's reviewed task context,
    not proof of generic AX semantics and never an override of protection.
    """
    state=advisory.get('state')
    if state=='protected':raise BridgeError('protected_action_requires_human',advisory.get('reason','Complete protected text entry on the device'))
    if state not in {'clear','unqualified'}:raise BridgeError('prerequisite_required',advisory.get('reason','Physical sensitive state is unknown'))
    declaration=arguments.get('field_context')
    if declaration not in {None,'ordinary_app'}:raise BridgeError('invalid_arguments','Unsupported field context declaration')
    if state=='unqualified':
        if advisory.get('locked') is not False or advisory.get('ax_observed') is not True:
            raise BridgeError('prerequisite_required','Ordinary-field confirmation cannot replace a lock or accessibility observation')
        if declaration!='ordinary_app':raise BridgeError('prerequisite_required','Review and confirm the ordinary app field in the owner-approved action')
    return {'field_context':declaration,'qualification_source':'owner_confirmed_advisory' if declaration=='ordinary_app' else advisory.get('source','native_accessibility_audit')}


class Contact(NamedTuple):
    identity: int
    x: int
    y: int
    touching: bool = True


def to_hid(x, y, logical_size, orientation='portrait'):
    width,height=logical_size['width'],logical_size['height']
    if not all(isinstance(v,(int,float)) and math.isfinite(v) for v in (x,y,width,height)) or width<=0 or height<=0 or not (0<=x<width and 0<=y<height):
        raise BridgeError('invalid_arguments','Input point must be finite and inside the observed logical display')
    u,v=x/width,y/height
    if orientation in {'landscapeLeft','landscape_left'}:
        # The native digitizer remains in portrait axes. With the device's
        # bottom edge on the right, displayed (u,v) maps to native (1-v,u).
        u,v=1-v,u
    elif orientation!='portrait':
        raise BridgeError('unsupported_capability','This native input orientation is not qualified')
    return round(u*65535),round(v*65535)


def normalize_audit(label, role, frame, identifier=None, value=None):
    bounds=None
    if isinstance(frame,dict) and all(isinstance(frame.get(k),(int,float)) and math.isfinite(frame[k]) for k in ('x','y','width','height')):
        if frame['width']>0 and frame['height']>0: bounds={k:frame[k] for k in ('x','y','width','height')}
    elif isinstance(frame,str):
        numbers=re.fullmatch(r'\{\{\s*(-?[\d.]+),\s*(-?[\d.]+)\},\s*\{\s*([\d.]+),\s*([\d.]+)\}\}',frame)
        if numbers:
            x,y,w,h=map(float,numbers.groups())
            if w>0 and h>0: bounds={'x':x,'y':y,'width':w,'height':h}
    return {'label':label or '', 'role':role if isinstance(role,str) and role else 'unknown','bounds':bounds,'identifier':identifier if isinstance(identifier,str) else None,'value':value if isinstance(value,(str,int,float,bool)) else None,'source':'accessibility','actionable':bounds is not None}


class TouchController:
    """Owns contact/key state and always releases it before ending its media lease."""
    def __init__(self, service, on_send=None):
        self.on_send=on_send
        self.service=service;self.contacts=[];self.keyboard_id=None;self.sent=False
    async def frame(self, contacts):
        self.contacts=list(contacts);self.sent=True
        if self.on_send:self.on_send()
        await self.service.send_multitouch(self.contacts)
    async def release(self):
        if self.contacts:
            released=[Contact(c.identity,c.x,c.y,False) for c in self.contacts]
            try: await self.service.send_multitouch(released)
            finally:self.contacts=[]
        if self.keyboard_id is not None:
            await self.service.send_keyboard(self.keyboard_id,[])
    async def gesture(self, paths, duration=.3, steps=20, hold=0):
        def positions(t,touching=True):
            return [Contact(i,round(x1+(x2-x1)*t),round(y1+(y2-y1)*t),touching) for i,(x1,y1,x2,y2) in enumerate(paths)]
        try:
            await self.frame(positions(0))
            if hold:await asyncio.sleep(hold)
            for step in range(1,steps+1):
                await asyncio.sleep(duration/steps)
                await self.frame(positions(step/steps))
            if steps==0:await asyncio.sleep(duration)
        finally:
            await asyncio.shield(self.release())
    async def keyboard(self):
        if self.keyboard_id is None:self.keyboard_id=await self.service.create_keyboard_service(product='TapMesh virtual keyboard',manufacturer='TapMesh')
        return self.keyboard_id
    async def key(self, usages):
        kid=await self.keyboard()
        modifiers=[u for u in usages if 224<=u<=231]
        try:
            self.sent=True
            if self.on_send:self.on_send()
            if modifiers:
                await self.service.send_keyboard(kid,modifiers);await asyncio.sleep(.03)
            await self.service.send_keyboard(kid,usages);await asyncio.sleep(.03)
            if modifiers:
                await self.service.send_keyboard(kid,modifiers);await asyncio.sleep(.03)
        finally:await asyncio.shield(self.service.send_keyboard(kid,[]))


@asynccontextmanager
async def native_tunnel(serial):
    from pymobiledevice3.remote.native_tunnel import NativeRemotedTunnel
    if not serial:raise BridgeError('device_not_found','Physical serial binding is required')
    handle=NativeRemotedTunnel(serial=serial)
    try:yield await handle.aopen()
    finally:await asyncio.shield(handle.aclose())


@asynccontextmanager
async def userspace_tunnel(serial):
    from pymobiledevice3.remote.userspace_tunnel import UserspaceRsdTunnel
    handle=UserspaceRsdTunnel(serial=serial,autopair=False,remotepairing_fallback=False)
    try:yield await handle.aopen()
    finally:await asyncio.shield(handle.aclose())


@asynccontextmanager
async def device_tunnel(serial):
    # Transport selection is read-only and occurs before any input. Never retry
    # an action when transport state changes after sending it.
    preference=os.environ.get('TAPMESH_PHYSICAL_TRANSPORT','auto')
    if preference not in {'auto','native','userspace'}:raise BridgeError('prerequisite_required','Invalid physical transport configuration')
    if preference=='auto':
        from pymobiledevice3.usbmux import list_devices
        devices=await list_devices()
        use_usb=any(d.serial==serial and str(d.connection_type)=='USB' for d in devices)
    else:use_usb=preference=='userspace'
    async with (userspace_tunnel(serial) if use_usb else native_tunnel(serial)) as rsd:yield rsd


@asynccontextmanager
async def authenticated_media(rsd):
    """Public display/HID services with cleanup even if media authentication fails."""
    from pymobiledevice3.remote.core_device.display_service import DisplayService
    from pymobiledevice3.remote.core_device.screen_stream import open_media_receiver
    from pymobiledevice3.remote.core_device.hid_service import UniversalHIDServiceService,TOUCH_AUTH_SETTLE_SECONDS
    async with AsyncExitStack() as stack:
        display=await stack.enter_async_context(DisplayService(rsd))
        transport,receiver_ip=open_media_receiver(display,(1024*1024,))
        stack.callback(transport.close)
        async def stop():
            with suppress(Exception):await display.stop_media_stream(stop_all=True)
        stack.push_async_callback(stop)
        await asyncio.wait_for(display.start_video_stream(receiver_ip=receiver_ip,receiver_port=transport.port,sender_ip=rsd.service.address[0],display_id=1),10)
        async def drain():
            with suppress(asyncio.CancelledError,OSError):
                while True:await transport.recv()
        task=asyncio.create_task(drain())
        async def cancel_drain():
            task.cancel()
            with suppress(asyncio.CancelledError):await task
        stack.push_async_callback(cancel_drain)
        await asyncio.sleep(TOUCH_AUTH_SETTLE_SECONDS)
        yield await stack.enter_async_context(UniversalHIDServiceService(rsd))


@asynccontextmanager
async def clipboard_service(rsd):
    from pymobiledevice3.remote.core_device.pasteboard_service import PasteboardService
    async with PasteboardService(rsd) as service:yield service


class NativePhysical:
    def __init__(self, *, tunnel_factory=device_tunnel, media_factory=authenticated_media, clipboard_factory=clipboard_service):
        self.tunnel_factory=tunnel_factory;self.media_factory=media_factory;self.clipboard_factory=clipboard_factory
    @asynccontextmanager
    async def bound(self, serial):
        stack=AsyncExitStack()
        try:
            rsd=await stack.enter_async_context(self.tunnel_factory(serial))
            if rsd.udid!=serial:raise BridgeError('connection_changed','Native RSD identity did not match the explicitly selected device')
        except (BridgeError,asyncio.CancelledError):
            await stack.aclose();raise
        except Exception as exc:
            await stack.aclose()
            raise BridgeError('prerequisite_required','Native connection requires an available trusted device, Developer Mode and supported developer services',details={'backend_exception':type(exc).__name__}) from exc
        try:yield rsd
        finally:await asyncio.shield(stack.aclose())
    async def audit(self, serial, *, maximum=200, attributes=True):
        from pymobiledevice3.services.accessibilityaudit import AccessibilityAudit
        items=[];truncated=False
        async with self.bound(serial) as rsd:
            async with AccessibilityAudit(rsd) as audit:
                async with asyncio.timeout(20):
                    async for focus in audit.iter_elements():
                        values=await audit.element_attributes(focus.element.identifier,['Label','Identifier','Role','Frame','Value']) if attributes else {}
                        item=normalize_audit(values.get('Label') or focus.caption,values.get('Role'),values.get('Frame'),values.get('Identifier'),values.get('Value'))
                        item['spoken_description']=focus.spoken_description
                        items.append(item)
                        if len(items)>=maximum:truncated=True;break
        return {'elements':[i for i in items if i['bounds'] is not None],'unlocated_elements':[i for i in items if i['bounds'] is None],'backend_truncated':truncated,'source':'accessibility','observed_at':utcnow(),'freshness':{'state':'fresh'}}
    async def sensitive_state(self,serial,locked=None):
        if locked is True:return classify_sensitivity(True,[])
        result=await self.audit(serial,maximum=200,attributes=False)
        if result['backend_truncated']:return {'state':'unknown','reason':'Sensitive-state inspection was truncated'}
        elements=result['elements']+result['unlocated_elements']
        labels=[str(e.get('label','')) for e in elements]
        labels.extend(str(e.get('spoken_description','')) for e in elements)
        return classify_sensitivity(locked,labels)
    async def input(self,tool,serial,geometry,targets,args,*,guard=None,on_send=None,text_context=None):
        size=geometry['logical_size']
        orientation=geometry.get('orientation')
        if orientation not in {'portrait','landscapeLeft','landscape_left'}:
            raise BridgeError('unsupported_capability','Native HID mapping requires an observed portrait or landscape-left display')
        if (orientation=='portrait') != (size['width']<=size['height']):
            raise BridgeError('stale_observation','Display dimensions disagree with the observed input orientation')
        def point(name):return to_hid(*targets[name][:2],size,orientation)
        # Preflight the whole operation, including text encoding, before media,
        # focus or key state can change.
        plan=None
        if tool in {'tap','double_tap','long_press'}:plan=[(*point('target'),*point('target'))]
        elif tool in {'swipe','drag'}:plan=[(*point('start'),*point('end'))]
        elif tool=='pinch':
            cx,cy=targets['center'][:2];radius=min(40,cx,size['width']-cx-1,cy,size['height']-cy-1)
            if radius<=1:raise BridgeError('invalid_arguments','Pinch center is too close to display edge')
            end_radius=radius*args['scale']
            plan=[(*to_hid(cx-radius,cy,size,orientation),*to_hid(cx-end_radius,cy,size,orientation)),(*to_hid(cx+radius,cy,size,orientation),*to_hid(cx+end_radius,cy,size,orientation))]
        elif tool not in {'type_text','fill','press_key','hide_keyboard'}:raise BridgeError('unsupported_capability','This native input operation is not qualified')
        if tool=='fill' and 'target' not in targets:raise BridgeError('invalid_arguments','Fill requires an explicit target')
        if tool in {'type_text','fill'} and 'target' in targets:point('target')
        context_metadata={'field_context':None,'qualification_source':None}
        if tool in {'type_text','fill'}:
            if text_context is not None:context_metadata=validate_text_context(text_context,args)
            elif args.get('field_context') is not None:raise BridgeError('prerequisite_required','An ordinary-field declaration requires the observed advisory state from the approved router action')
        method=args.get('input_method','keyboard')
        if method not in {'keyboard','clipboard'}:raise BridgeError('invalid_arguments','Unsupported explicit input method')
        if tool in {'type_text','fill'} and method=='keyboard':
            if args.get('keyboard_layout') not in {None,'us'}:raise BridgeError('invalid_arguments','Unsupported keyboard layout declaration')
            if os.environ.get('TAPMESH_PHYSICAL_KEYBOARD_LAYOUT')!='us' and args.get('keyboard_layout')!='us':raise BridgeError('prerequisite_required','Confirm the current US English hardware layout in the owner-approved action or local configuration before literal HID text entry')
            from pymobiledevice3.remote.core_device.hid_service import ASCII_TO_HID
            if any(c not in ASCII_TO_HID for c in args['text']):raise BridgeError('unsupported_capability','Native keyboard insertion is qualified for US-layout ASCII; unsupported Unicode fails before focus/input')
        keys={'return':40,'delete':42,'escape':41,'tab':43,'space':44,'right':79,'left':80,'down':81,'up':82,'select_all':4}
        if tool=='press_key' and args['key'] not in keys:raise BridgeError('unsupported_capability','Semantic key is not supported by native HID')
        control=None;clipboard_started=False;clipboard_changed=False
        try:
            async with self.bound(serial) as rsd:
                async with self.media_factory(rsd) as service:
                    if guard:await guard(rsd)
                    control=TouchController(service,on_send=on_send)
                    try:
                        if tool in {'tap','double_tap','long_press'}:
                            count=2 if tool=='double_tap' else 1
                            for n in range(count):
                                await control.gesture(plan,steps=0,duration=args.get('duration_ms',800)/1000 if tool=='long_press' else .06)
                                if n<count-1:await asyncio.sleep(args.get('interval_ms',120)/1000)
                        elif tool in {'swipe','drag','pinch'}:
                            await control.gesture(plan,duration=args.get('duration_ms',500 if tool=='pinch' else 700 if tool=='drag' else 300)/1000,steps=30,hold=args.get('hold_ms',300)/1000 if tool=='drag' else 0)
                        elif tool in {'type_text','fill'}:
                            if 'target' in targets:
                                p=point('target');await control.gesture([(*p,*p)],duration=.06,steps=0)
                            if method=='clipboard':
                                async with self.clipboard_factory(rsd) as clipboard:
                                    clipboard_started=True
                                    if on_send:on_send()
                                    await clipboard.set_text(args['text']);clipboard_changed=True
                            if tool=='fill':
                                if 'target' not in targets:raise BridgeError('invalid_arguments','Fill requires an explicit target')
                                await control.key([227,4]);await control.key([42])
                            if method=='clipboard':await control.key([227,25])
                            else:
                                from pymobiledevice3.remote.core_device.hid_service import ASCII_TO_HID
                                for char in args['text']:
                                    usage,shift=ASCII_TO_HID[char];await control.key([225,usage] if shift else [usage])
                            if tool=='fill' and args.get('submit'):await control.key([40])
                        else:
                            key=41 if tool=='hide_keyboard' else keys[args['key']]
                            modifiers=[] if tool=='hide_keyboard' else [dict(shift=225,control=224,alt=226,command=227)[m] for m in args.get('modifiers',[])]
                            if tool=='press_key' and args['key']=='select_all':modifiers.append(227)
                            await control.key([*dict.fromkeys(modifiers),key])
                    finally:await asyncio.shield(control.release())
            return {**context_metadata,'delivery':'sent' if control and control.sent or clipboard_changed else 'not_sent','verified':False,'completion_condition':'Native HID reports transmitted; app outcome must be observed separately','source':'pymobiledevice3_native_hid','keyboard_state':'unverifiable' if tool=='hide_keyboard' else None,'input_method':method if tool in {'type_text','fill'} else None,'clipboard_changed':clipboard_changed,'propagation_warning':'Explicit device clipboard write may synchronize through Universal Clipboard' if clipboard_changed else None}
        except (BridgeError,asyncio.CancelledError):raise
        except Exception as exc:
            sent=bool(control and control.sent) or clipboard_started
            raise BridgeError('delivery_outcome_unknown' if sent else 'prerequisite_required','Native HID was interrupted after transmission; observe before deciding to retry' if sent else 'Native media authentication or HID prerequisites are unavailable; no input was transmitted',side_effect_state='unknown' if sent else 'not_sent',details={'backend_exception':type(exc).__name__,'clipboard_changed':clipboard_changed if clipboard_changed else None if clipboard_started else False}) from exc


def capability_profile():
    """Operation-level initial profile; limits are qualification boundaries."""
    import importlib.util
    available=importlib.util.find_spec('pymobiledevice3') is not None
    if not available:
        return {cap:{'status':'requires_user_action','reason':'Install pinned pymobiledevice3 Mac dependencies'} for cap in ('input.tap','input.double_tap','input.long_press','input.swipe','input.drag','input.pinch','input.text','input.key','input.keyboard_dismiss','ui.inspect')}
    base={'requires_exclusive_media_session':True,'orientations':['portrait'],'coordinate_space':'device_points','frame_content_fingerprint_required':True,'transport':'bound USB userspace preferred; explicit native fallback before input only'}
    profiles={}
    for cap in ('input.tap','input.long_press','input.pinch'):
        profiles[cap]={'status':'supported','reason':'Public native HID route produced observed project-fixture changes on the qualified physical device','limits':dict(base)}
    profiles['input.tap']['limits']['orientations']=['portrait','landscapeLeft']
    profiles['input.tap']['reason']='Observed fixture counter change on iPhone portrait and iPad landscape-left; native digitizer axes are transformed from the anchored display'
    for cap in ('input.double_tap','input.swipe','input.drag'):
        profiles[cap]={'status':'degraded','reason':'Native contact route implemented; operation-specific physical repeatability remains under qualification','limits':dict(base)}
    profiles['input.key']={'status':'degraded','reason':'Native hardware keys; Return and Backspace observed, modifier-before-key ordering required','limits':{'keys':['return','delete','escape','tab','space','up','down','left','right','select_all'],'modifiers':['shift','control','alt','command'],'verified_keys':['return','delete','select_all'],'verified_chords':['command+a','command+v','shift+letter'],'orientations':['portrait']}}
    for cap in ('input.text','input.fill'):
        profiles[cap]={'status':'degraded','reason':'US-layout keyboard insertion and explicitly authorized Unicode clipboard-paste are separate methods; no implicit substitution. Protected/unknown states remain blocked; observed unqualified app fields require an owner-approved ordinary_app declaration.','limits':{'input_methods':{'keyboard':{'character_set':'US-layout ASCII','requires_current_layout_confirmation':True,'per_action_declaration':{'keyboard_layout':'us'},'current_layout_confirmed':os.environ.get('TAPMESH_PHYSICAL_KEYBOARD_LAYOUT')=='us'},'clipboard':{'character_set':'Unicode','policy':'S','clipboard_changed':True,'verified_fixture_readback':'TapMesh تجربة'}},'implicit_clipboard_fallback':False,'orientations':['portrait'],'sensitive_state_scope':'qualified_fixture_or_owner_confirmed_observed_ordinary_app','field_context_declaration':{'value':'ordinary_app','requires_owner_approved_action':True,'never_overrides':['protected','unknown','locked','missing_accessibility']},'generic_AX_semantics_verified':False,'fill_verification':'generic target replacement requires readback; combined fixture Cmd+A/Delete and literal insertion observed'}}
    profiles['input.keyboard_dismiss']={'status':'degraded','reason':'Semantic Escape route exists; generic keyboard visibility is unverifiable','limits':{'orientations':['portrait']}}
    profiles['ui.inspect']={'status':'degraded','reason':'Use NAS OCR for non-mutating screen inspection; physical AX focus traversal is excluded from read-only tools','limits':{'sources':['auto','ocr'],'accessibility':'unsupported_for_read_only_inspection','bounds_qualified':False,'semantic_press_verified':False}}
    return profiles
