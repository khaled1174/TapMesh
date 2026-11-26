"""Physical iOS input and capture through public pymobiledevice3 APIs (11.50.0).

A USB device keeps one persistent RSD session (userspace tunnel) that carries
one DisplayService video stream, HID input and display geometry. The video
stream is both the screen (decoded continuously with VideoToolbox; a
screenshot is the latest decoded frame) and the touch authentication HID needs,
so there is never a second stream. ScreenCaptureService is used only when no
stream is open on the session: with the stream open it stalls after a few
captures. Sessions close after an idle period, on connection change, on
shutdown and on any transport error. An input that may have been transmitted is
never retried automatically. No discovery fallback, proprietary runner or raw
pairing logs.
"""
import asyncio
from collections import deque
from contextlib import AsyncExitStack, asynccontextmanager, suppress
import io
import json
import logging
import math
import os
import struct
import time
from typing import NamedTuple
from .core import settle_cleanup
from .safety import BridgeError

logger=logging.getLogger(__name__)

IDLE_SECONDS=60
READ_TIMEOUT_SECONDS=3
# A warm lock probe must fall back promptly if this device cannot answer it.
LOCK_READ_TIMEOUT_SECONDS=.25
# Live stream: an unusable stream is not retried for screenshots for this long.
STREAM_RETRY_SECONDS=10
STREAM_OPEN_TIMEOUT_SECONDS=8
TUNNEL_OPEN_TIMEOUT_SECONDS=15
STREAM_START_SILENCE_SECONDS=5
DEAD_FRAME_MAX_AGE_SECONDS=5
EMPTY_STREAM_RETRY_SECONDS=(10,30,60)
FIRST_FRAME_SECONDS=2.5
# Without VideoToolbox frames the stream only authorizes touch; it is closed
# this long after the last input so screenshots can use ScreenCaptureService.
DEGRADED_STREAM_LINGER_SECONDS=1.5
AUTH_SETTLE_SECONDS=.5  # pymobiledevice3 TOUCH_AUTH_SETTLE_SECONDS
GESTURE_LOCK_RECHECK_SECONDS=1.0
TEXT_LOCK_CHECK_CHUNK_SIZE=20
# Delta bytes per 0.5 s that count as on-screen motion (6 Mbps cap = 375 KB).
MOTION_BYTES_PER_HALF_SECOND=150_000
# Live video wire format (application/vnd.tapmesh.live): [type u8][length u32 BE][payload].
LIVE_FORMAT,LIVE_PARAMS,LIVE_FRAME,LIVE_HEARTBEAT,LIVE_END=1,2,3,4,5
LIVE_QUEUE_MESSAGES=30
LIVE_QUEUE_BYTES=4*1024*1024
LIVE_HEARTBEAT_SECONDS=1.0
# A viewer whose consumer has not asked for a message this long is gone
# (e.g. its HTTP response never started); it no longer keeps the stream open.
LIVE_STALL_SECONDS=15


def frame_unavailable(content):
    from PIL import Image
    with Image.open(io.BytesIO(content)) as image:
        rgba=image.convert('RGBA')
        return rgba.convert('RGB').getbbox() is None or rgba.getchannel('A').getbbox() is None


def locked_input_error(*, sent=False):
    if sent:
        return BridgeError('protected_action_requires_human','The device locked during input. Unlock it on the device and inspect the result before retrying.',side_effect_state='unknown')
    return BridgeError('protected_action_requires_human','The device is locked. Unlock it on the device, then try again. No input was sent.')


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


class TouchController:
    """Owns contact/key state and always releases it before the input ends."""
    def __init__(self, service, on_send=None, keyboard_id=None):
        self.on_send=on_send
        self.service=service;self.contacts=[];self.keyboard_id=keyboard_id;self.sent=False
    async def frame(self, contacts):
        self.contacts=list(contacts);self.sent=True
        if self.on_send:self.on_send()
        await self.service.send_multitouch(self.contacts)
    async def release(self):
        if self.contacts:
            released=[Contact(c.identity,c.x,c.y,False) for c in self.contacts]
            try: await self.service.send_multitouch(released)
            finally:self.contacts=[]
    async def gesture(self, paths, duration=.3, steps=20, hold=0, *, check_unlocked=None):
        def positions(t,touching=True):
            return [Contact(i,round(x1+(x2-x1)*t),round(y1+(y2-y1)*t),touching) for i,(x1,y1,x2,y2) in enumerate(paths)]
        if check_unlocked:await check_unlocked()
        recheck_at=time.monotonic()+GESTURE_LOCK_RECHECK_SECONDS
        async def pause_until(deadline):
            nonlocal recheck_at
            if not check_unlocked:
                await asyncio.sleep(max(0,deadline-time.monotonic()))
                return
            while True:
                await asyncio.sleep(max(0,min(deadline,recheck_at)-time.monotonic()))
                now=time.monotonic()
                if now>=recheck_at:
                    await check_unlocked()
                    now=time.monotonic()
                    recheck_at=now+GESTURE_LOCK_RECHECK_SECONDS
                if now>=deadline:return
        try:
            await self.frame(positions(0))
            if hold:await pause_until(time.monotonic()+hold)
            movement_started=time.monotonic()
            for step in range(1,steps+1):
                # Keep each position on the original movement schedule so a
                # timer overrun or earlier transport write does not compound.
                await pause_until(movement_started+duration*step/steps)
                await self.frame(positions(step/steps))
            if steps==0:await pause_until(movement_started+duration)
        finally:
            await asyncio.shield(self.release())
    async def keyboard(self):
        # The virtual keyboard lives as long as the persistent HID session, so
        # its registration delay is paid once per session, not per key.
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


async def select_transport(serial):
    """'userspace' for a USB device (own tunnel, safe to keep open); 'native' otherwise.

    The native transport rides Apple's remoted tunnel, where the device keeps a
    single RSD connection; holding it would evict Xcode/devicectl, so native
    sessions are opened per input and never kept.
    """
    preference=os.environ.get('TAPMESH_PHYSICAL_TRANSPORT','auto')
    if preference not in {'auto','native','userspace'}:raise BridgeError('prerequisite_required','Invalid physical transport configuration')
    if preference!='auto':return preference
    from pymobiledevice3.usbmux import list_devices
    devices=await list_devices()
    return 'userspace' if any(d.serial==serial and str(d.connection_type)=='USB' for d in devices) else 'native'


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


def device_tunnel(serial, transport):
    # Transport selection is read-only and occurs before any input. Never retry
    # an action when transport state changes after sending it.
    return userspace_tunnel(serial) if transport=='userspace' else native_tunnel(serial)


_VPS,_SPS,_PPS=32,33,34
_KEY_NALS=frozenset((19,20,21))  # IDR_W_RADL, IDR_N_LP, CRA


def avcc(nals):
    """NAL units, each prefixed by its 4-byte big-endian length."""
    return b''.join(len(n).to_bytes(4,'big')+n for n in nals)


def live_message(kind, payload=b''):
    return struct.pack('!BI',kind,len(payload))+payload


def hevc_sps_size(sps):
    """(pic_width, pic_height) in luma samples from an HEVC SPS NAL unit (with
    its 2-byte header): the coded frame size VideoToolbox decodes to, padding
    included (1320x2868 content arrives as 1328x2896)."""
    rbsp=bytearray();i=2
    while i<len(sps):
        if i+2<len(sps) and sps[i]==0 and sps[i+1]==0 and sps[i+2]==3:rbsp+=sps[i:i+2];i+=3
        else:rbsp.append(sps[i]);i+=1
    pos=0
    def bits(n):
        nonlocal pos
        if pos+n>len(rbsp)*8:raise ValueError('SPS is truncated')
        value=0
        for _ in range(n):value=(value<<1)|((rbsp[pos>>3]>>(7-(pos&7)))&1);pos+=1
        return value
    def ue():
        zeros=0
        while not bits(1):
            zeros+=1
            if zeros>31:raise ValueError('Invalid Exp-Golomb code')
        return (1<<zeros)-1+bits(zeros)
    bits(4);sub_layers=bits(3);bits(1)
    # general_profile_space..general_inbld/reserved (88 bits) + general_level_idc (8)
    bits(96)
    present=[(bits(1),bits(1)) for _ in range(sub_layers)]
    if sub_layers:bits(2*(8-sub_layers))
    for profile,level in present:bits(88*profile+8*level)
    ue()  # sps_seq_parameter_set_id
    if ue()==3:bits(1)  # chroma_format_idc 4:4:4 -> separate_colour_plane_flag
    width,height=ue(),ue()
    if not (0<width<=16384 and 0<height<=16384):raise ValueError('Implausible SPS picture size')
    return width,height


def rtcp_receiver_report(local_ssrc, remote_ssrc, highest_seq):
    """RR + empty-CNAME SDES, as Xcode sends. The device reaps a video stream
    that receives no RTCP for RTCPTimeoutInterval (20 s)."""
    rr=struct.pack('!BBHII BBBB IIII',0x81,0xC9,7,local_ssrc&0xFFFFFFFF,remote_ssrc&0xFFFFFFFF,0,0,0,0,highest_seq&0xFFFFFFFF,0,0,0)
    return rr+struct.pack('!BBHI BBBB',0x81,0xCA,2,local_ssrc&0xFFFFFFFF,1,0,0,0)


def rtcp_fir(local_ssrc, remote_ssrc, sequence):
    """RFC 5104 Full Intra Request (PT=206 FMT=4). The device ignores PLI and
    honors FIR when the offer sets allowRTCPFB."""
    return struct.pack('!BBHII IB3x',0x84,0xCE,4,local_ssrc&0xFFFFFFFF,0,remote_ssrc&0xFFFFFFFF,sequence&0xFF)


def display_pixel_size(info):
    """Primary display framebuffer size in pixels from DeviceInfoService."""
    displays=info.get('displays',[]) if isinstance(info,dict) else []
    if isinstance(displays,dict):displays=list(displays.values())
    for display in sorted((d for d in displays if isinstance(d,dict)),key=lambda d:not d.get('primary',False)):
        for size in ((display.get('bounds') or [None,None])[1],display.get('nativeSize'),(display.get('currentMode') or {}).get('size')):
            if isinstance(size,(list,tuple)) and len(size)==2 and all(isinstance(v,(int,float)) and v>0 for v in size):
                return round(size[0]),round(size[1])
    return None


def frame_content_box(frame_size, display_pixels):
    """Region of a decoded frame that holds the screen. The encoder pads the
    framebuffer right/bottom to its coded size (1320x2868 -> 1328x2896); the
    content is never scaled in that case. A frame in the other orientation is
    matched against the rotated display size."""
    fw,fh=frame_size
    if not display_pixels:return None
    w,h=display_pixels
    if (fw>fh)!=(w>h):w,h=h,w
    if fw>=w and fh>=h:return (0,0,w,h)
    if abs(fw/fh-w/h)<=.01*(w/h):return (0,0,fw,fh)
    # Smaller than the display with another aspect: keep the top-left region
    # with the display's aspect (padding is right/bottom).
    return (0,0,min(fw,round(fh*w/h)),min(fh,round(fw*h/w)))


def _gray(pixel):
    return all(abs(c-128)<6 for c in pixel[:3])


def collapsed_extent(image):
    """Rate-control resolution collapse: under heavy motion the encoder draws a
    shrunk screen top-left on mid-gray padding. Returns the content (w, h) when
    the frame looks collapsed, else None. Cheap rejection first."""
    width,height=image.size
    if width<16 or height<16:return None
    probe=[(round(width*fx),round(height*fy)) for fx in (.95,.97,.99) for fy in (.95,.97,.99)]
    if not all(_gray(image.getpixel(p)) for p in probe):return None
    rows=344;cols=max(8,round(rows*width/height))
    small=image.convert('RGB').resize((cols,rows))
    data=small.load()
    right=0
    for x in range(cols):
        samples=range(0,rows,4)
        if sum(_gray(data[x,y]) for y in samples)/len(samples)<.6:right=x
    bottom=0
    for y in range(rows):
        samples=range(0,cols,4)
        if sum(_gray(data[x,y]) for x in samples)/len(samples)<.6:bottom=y
    cw=round((right+1)/cols*width);ch=round((bottom+1)/rows*height)
    if .2*width<cw<.92*width and .2*height<ch<.92*height:return cw,ch
    return None


class VTFrameDecoder:
    """HEVC -> retained CVPixelBuffer (BGRA) through VideoToolbox (macOS).

    Frames stay in VideoToolbox buffers; only a requested frame is copied into a
    PIL image, so continuous decoding costs no per-frame pixel copies."""
    def __init__(self, vps, sps, pps):
        from pymobiledevice3.remote.core_device.vt_jpeg import HEVCDecoder
        self._decoder=HEVCDecoder(vps,sps,pps,bgra_output=True)
        self.width,self.height=self._decoder.width,self._decoder.height

    def feed(self, nals):
        import ctypes
        from ctypes import byref,c_size_t,c_uint32,c_void_p
        from pymobiledevice3.remote.core_device.vt_jpeg import cm,vt,cf,kVTDecodeFrame_EnableAsynchronousDecompression,kVTDecodeFrame_1xRealTimePlayback
        decoder=self._decoder
        data=avcc(nals)
        if not data:return
        block=c_void_p(0)
        status=cm.CMBlockBufferCreateWithMemoryBlock(None,None,len(data),None,None,0,len(data),0,byref(block))
        if status!=0:raise RuntimeError(f'CMBlockBufferCreateWithMemoryBlock {status}')
        try:
            scratch=ctypes.create_string_buffer(data,len(data))
            cm.CMBlockBufferReplaceDataBytes(scratch,block,0,len(data))
            sizes=(c_size_t*1)(len(data));sample=c_void_p(0)
            status=cm.CMSampleBufferCreateReady(None,block,decoder._fmt,1,0,None,1,sizes,byref(sample))
            if status!=0:raise RuntimeError(f'CMSampleBufferCreateReady {status}')
            try:
                info=c_uint32(0)
                status=vt.VTDecompressionSessionDecodeFrame(decoder._session,sample,kVTDecodeFrame_EnableAsynchronousDecompression|kVTDecodeFrame_1xRealTimePlayback,None,byref(info))
            finally:cf.CFRelease(sample)
        finally:cf.CFRelease(block)
        if status!=0:decoder._outputs.put((status,None))

    def drain(self):
        """(status, handle) for every finished frame; handles are retained."""
        return list(self._decoder.drain())

    @staticmethod
    def image(handle):
        import ctypes
        from PIL import Image
        from pymobiledevice3.remote.core_device.vt_jpeg import cv,kCVPixelBufferLock_ReadOnly
        if cv.CVPixelBufferLockBaseAddress(handle,kCVPixelBufferLock_ReadOnly)!=0:return None
        try:
            width=int(cv.CVPixelBufferGetWidth(handle));height=int(cv.CVPixelBufferGetHeight(handle))
            stride=int(cv.CVPixelBufferGetBytesPerRow(handle));base=cv.CVPixelBufferGetBaseAddress(handle)
            if not base or width<=0 or height<=0:return None
            data=ctypes.string_at(base,height*stride)
        finally:cv.CVPixelBufferUnlockBaseAddress(handle,kCVPixelBufferLock_ReadOnly)
        # VideoToolbox leaves alpha undefined; read the buffer as opaque RGB.
        return Image.frombuffer('RGB',(width,height),data,'raw','BGRX',stride,1)

    @staticmethod
    def release(handle):
        from pymobiledevice3.remote.core_device.vt_jpeg import cf
        if handle:cf.CFRelease(handle)

    def close(self):
        with suppress(Exception):self._decoder.close()


class LiveSubscriber:
    """One viewer of a LiveScreen: a bounded queue of encoded wire messages.

    Publishing never waits for the viewer. A viewer starts at a keyframe with
    FORMAT and PARAMS before it; one that falls behind loses its queued messages
    and resumes the same way at the next keyframe. Read-only: it never sends
    input and needs no HID."""
    def __init__(self, screen, *, on_close=None, max_messages=LIVE_QUEUE_MESSAGES, max_bytes=LIVE_QUEUE_BYTES):
        self.screen=screen;self.on_close=on_close;self.max_messages=max_messages;self.max_bytes=max_bytes
        self._queue=deque();self._bytes=0;self._ready=asyncio.Event()
        self.format=None;self.params=None;self.waiting_key=True;self.closed=False;self.end_reason=None
        self.frames=0;self.dropped=0;self.overflows=0;self.reading=False;self.read_at=screen.clock()

    def _put(self, message):
        self._queue.append(message);self._bytes+=len(message);self._ready.set()

    def publish(self, key, frame, format_message, params_message):
        if self.closed:return
        if self.waiting_key and not key:
            self.dropped+=1;return
        messages=[m for m,sent in ((format_message,self.format),(params_message,self.params)) if m!=sent]+[frame]
        if len(self._queue)+len(messages)>self.max_messages or self._bytes+sum(map(len,messages))>self.max_bytes:
            # Behind: discard everything queued and restart at a keyframe.
            self._queue.clear();self._bytes=0;self.format=self.params=None;self.overflows+=1
            messages=[format_message,params_message,frame]
            if not key or sum(map(len,messages))>self.max_bytes:
                self.waiting_key=True;self.dropped+=1;self.screen.request_keyframe();return
        for message in messages:self._put(message)
        self.format,self.params,self.waiting_key=format_message,params_message,False;self.frames+=1

    def close(self, reason=None):
        """End the subscription; with a reason, an END message is queued last."""
        if self.closed:return
        self.closed=True;self.end_reason=reason
        if reason is not None:self._put(live_message(LIVE_END,json.dumps({'reason':reason}).encode()))
        self._ready.set()
        self.screen._unsubscribe(self)
        if self.on_close:
            with suppress(Exception):self.on_close(self)

    async def messages(self, *, heartbeat=LIVE_HEARTBEAT_SECONDS):
        """Wire messages until the stream ends or the consumer stops; a
        HEARTBEAT whenever no FRAME was sent for `heartbeat` seconds."""
        beat=time.monotonic()
        try:
            while True:
                self.read_at=self.screen.clock()
                if not self._queue:
                    if self.closed:return
                    self._ready.clear();self.reading=True
                    try:await asyncio.wait_for(self._ready.wait(),max(0,beat+heartbeat-time.monotonic()))
                    except TimeoutError:pass
                    finally:self.reading=False;self.read_at=self.screen.clock()
                    if not self._queue:
                        if self.closed:return
                        beat=time.monotonic();yield live_message(LIVE_HEARTBEAT);continue
                message=self._queue.popleft();self._bytes-=len(message)
                if message[0]==LIVE_FRAME:beat=time.monotonic()
                yield message
                if message[0]==LIVE_END:return
        finally:self.close()


class LiveScreen:
    """One DisplayService video stream for a device.

    Receives RTP/HEVC, depacketizes (RFC 7798) and, when a decoder is
    available, decodes every access unit, keeping only the latest frame. It
    sends RTCP receiver reports (the device reaps a silent receiver) and Full
    Intra Requests: on start, after loss or decode errors, when motion settles,
    when a collapsed frame is about to be served and when a viewer subscribes.
    Viewers receive every complete access unit as encoded HEVC, independent
    of decoding. The same stream holds backboardd's touch-authentication gate
    for HID input."""
    def __init__(self, transport, rtcp_dest, local_ssrc, remote_ssrc, *, decoder_factory=VTFrameDecoder, decode=True, clock=time.monotonic):
        self.transport=transport;self.rtcp_dest=rtcp_dest;self.local_ssrc=local_ssrc;self.remote_ssrc=remote_ssrc
        self.decoder_factory=decoder_factory;self.decoding=bool(decode and decoder_factory);self.clock=clock
        self.started_at=clock();self.last_packet=None;self.ended_at=None;self.dead=False;self.decoder=None
        self.params={};self.needs_key=True;self.need_key_since=self.started_at
        self.latest=None;self.latest_at=None;self.latest_key_epoch=-1;self.key_epoch=0;self.frames=0;self.decode_errors=0
        self.aus=0;self.keys=0;self.lost_aus=0;self.fir_sent=0;self._fir_seq=0;self._last_fir=None
        self._highest_seq=0;self._last_seq=None;self._fu=bytearray();self._au=[];self._au_key=False;self._au_corrupt=False
        self._motion=[];self._moving=False;self._quiet_since=None;self._last_key_fed=None;self._last_key_seen=None
        self._tasks=[]
        # Live viewers; display_pixels crops their FORMAT like screenshots.
        self.subscribers=[];self.display_pixels=None;self._live_key=None;self._format_message=None;self._params_message=None

    # -- lifecycle --------------------------------------------------------
    def run(self):
        self._tasks=[asyncio.create_task(self._receive()),asyncio.create_task(self._feedback())]
        self.request_keyframe(force=True)

    def _mark_dead(self):
        if self.dead:return
        self.dead=True;self.ended_at=self.clock()
        self.end_subscribers('stream_ended')
        current=asyncio.current_task()
        for task in self._tasks:
            if task is not current:task.cancel()

    @property
    def has_frames(self):
        return bool(self.frames or self._format_message is not None and self.keys)

    async def aclose(self):
        self.dead=True;self.end_subscribers('stream_closed')
        tasks,self._tasks=list(self._tasks),[]
        for task in tasks:task.cancel()
        for task in tasks:
            with suppress(asyncio.CancelledError,Exception):await task
        self._set_latest(None,None)
        for status,handle in self._drain_raw():self._release(handle)
        if self.decoder is not None:
            self.decoder.close();self.decoder=None

    # -- RTCP -------------------------------------------------------------
    async def _send(self, packet):
        if self.dead or self.rtcp_dest is None or not (self.local_ssrc and self.remote_ssrc):return
        try:await self.transport.sendto(packet,*self.rtcp_dest)
        except Exception:self._mark_dead()

    def request_keyframe(self, *, force=False):
        if self.dead or not self.decoding and not self.subscribers:return
        now=self.clock()
        if not force and self._last_fir is not None and now-self._last_fir<.5:return
        self._last_fir=now;self._fir_seq+=1;self.fir_sent+=1
        task=asyncio.create_task(self._send(rtcp_fir(self.local_ssrc,self.remote_ssrc,self._fir_seq)))
        self._tasks.append(task);task.add_done_callback(lambda t:t in self._tasks and self._tasks.remove(t))

    async def _feedback(self):
        tick=0
        while not self.dead:
            await asyncio.sleep(.25);tick+=1
            now=self.clock()
            # A working stream can stop sending frames on a static screen.
            # The watchdog applies only before the first valid RTP packet.
            if self.last_packet is None and now-self.started_at>=STREAM_START_SILENCE_SECONDS:
                self._mark_dead();return
            if tick%4==0:await self._send(rtcp_receiver_report(self.local_ssrc,self.remote_ssrc,self._highest_seq))
            if self.dead:return
            if self.needs_key and now-self.need_key_since>1 and (self._last_fir is None or now-self._last_fir>1):
                self.request_keyframe()
            if any(s.waiting_key for s in self.subscribers) and (self._last_fir is None or now-self._last_fir>1):
                self.request_keyframe()
            for sub in list(self.subscribers):
                if not sub.reading and now-sub.read_at>LIVE_STALL_SECONDS:sub.close('stalled')
            # One keyframe when motion settles: rate control may have shrunk the
            # picture while moving; the static screen is then served crisp.
            cutoff=now-.5;self._motion=[(t,n) for t,n in self._motion if t>=cutoff]
            moving=sum(n for _,n in self._motion)>=MOTION_BYTES_PER_HALF_SECOND
            if moving:self._moving=True;self._quiet_since=None
            elif self._moving:
                if self._quiet_since is None:self._quiet_since=now
                elif now-self._quiet_since>=.25:
                    self._moving=False;self._quiet_since=None;self.request_keyframe()

    # -- RTP --------------------------------------------------------------
    async def _receive(self):
        try:
            while not self.dead:
                self.packet(await self.transport.recv(65535))
        except asyncio.CancelledError:raise
        except Exception as exc:
            logger.debug('live screen receive ended: %s',type(exc).__name__)
        self._mark_dead()

    def packet(self, data):
        from pymobiledevice3.remote.core_device.screen_stream import depacketize_hevc
        if self.dead or len(data)<12 or data[0]>>6!=2 or 64<=(data[1]&0x7F)<=95:return
        header=12+(data[0]&0x0F)*4
        if data[0]&0x10:
            if len(data)<header+4:return
            header+=4+int.from_bytes(data[header+2:header+4],'big')*4
        if header>len(data):return
        self.last_packet=self.clock()
        seq=int.from_bytes(data[2:4],'big')
        cycles=(self._highest_seq>>16)&0xFFFF;low=self._highest_seq&0xFFFF
        if seq<low and low-seq>0x8000:cycles=(cycles+1)&0xFFFF
        extended=(cycles<<16)|seq
        if self._highest_seq==0 or ((extended-self._highest_seq)&0xFFFFFFFF)<0x80000000:self._highest_seq=extended
        if self._last_seq is not None and seq!=(self._last_seq+1)&0xFFFF:
            self._fu.clear();self._au_corrupt=True
        if self._last_seq is None or ((seq-self._last_seq)&0xFFFF)<0x8000:self._last_seq=seq
        nals=[]
        depacketize_hevc(data[header:],self._fu,nals)
        for nal in nals:
            if not nal:continue
            kind=(nal[0]>>1)&0x3F
            if kind in (_VPS,_SPS,_PPS):self.params[kind]=bytes(nal)
            elif kind in _KEY_NALS:self._au_key=True
            self._au.append(bytes(nal))
        if data[1]>>7:
            au,key,corrupt=self._au,self._au_key,self._au_corrupt
            self._au=[];self._au_key=False;self._au_corrupt=False
            if au:self._access_unit(au,key,corrupt)

    def _access_unit(self, nals, key, corrupt):
        now=self.clock();self.aus+=1
        if key:self.keys+=1;self._last_key_seen=now
        # Each keyframe is followed by ~0.5 s of growing quality-refinement
        # deltas on a static screen; counting them as motion would answer every
        # keyframe with another (a FIR loop), so they are not motion.
        elif self._last_key_seen is None or now-self._last_key_seen>1:self._motion.append((now,sum(len(n) for n in nals)))
        if self.subscribers:self._publish(nals,key,corrupt,now)
        if not self.decoding:return
        if corrupt:
            # Deltas after a lost AU reference a picture we never decoded and
            # render as mosaic; hold the last good frame until a keyframe.
            self.lost_aus+=1;self._need_key();return
        if key and (self.decoder is None or self.needs_key):
            if not all(k in self.params for k in (_VPS,_SPS,_PPS)):return
            if self.decoder is not None:
                self._collect();self.decoder.close();self.decoder=None
            try:self.decoder=self.decoder_factory(self.params[_VPS],self.params[_SPS],self.params[_PPS])
            except Exception as exc:
                logger.warning('live screen decoder unavailable: %s',type(exc).__name__)
                self.decoding=False;return
            self.needs_key=False
        if self.decoder is None or self.needs_key:return
        try:self.decoder.feed(nals)
        except Exception:
            self.decode_errors+=1;self._need_key();return
        if key:self.key_epoch+=1;self._last_key_fed=now
        self._collect()

    def _need_key(self):
        if not self.needs_key:self.need_key_since=self.clock()
        self.needs_key=True;self.request_keyframe()

    # -- live viewers -----------------------------------------------------
    def subscribe(self, **kwargs):
        """A new viewer; its first FRAME is the keyframe requested here."""
        if self.dead:raise BridgeError('live_unavailable','The live stream has ended')
        subscriber=LiveSubscriber(self,**kwargs);self.subscribers.append(subscriber)
        self.request_keyframe(force=True)
        return subscriber

    def _unsubscribe(self, subscriber):
        with suppress(ValueError):self.subscribers.remove(subscriber)

    def end_subscribers(self, reason):
        for subscriber in list(self.subscribers):subscriber.close(reason)

    def _live_header(self):
        """Refresh FORMAT/PARAMS for the current parameter sets and display;
        False while they are incomplete or the SPS cannot be read."""
        params=tuple(self.params.get(k) for k in (_VPS,_SPS,_PPS))
        if None in params:return False
        key=(params,self.display_pixels)
        if key!=self._live_key:
            try:width,height=hevc_sps_size(params[1])
            except (ValueError,IndexError):
                if self._live_key!=(params,None,'invalid'):logger.warning('live screen SPS is unreadable; viewers wait for a keyframe')
                self._live_key=(params,None,'invalid');return False
            x0,y0,x1,y1=frame_content_box((width,height),self.display_pixels) or (0,0,width,height)
            info={'version':1,'codec':'hevc','coded_width':width,'coded_height':height,'crop':{'x':x0,'y':y0,'width':x1-x0,'height':y1-y0}}
            self._format_message=live_message(LIVE_FORMAT,json.dumps(info,separators=(',',':')).encode())
            self._params_message=live_message(LIVE_PARAMS,avcc(params))
            self._live_key=key
        return True

    def _publish(self, nals, key, corrupt, now):
        """Fan one complete access unit out to viewers without waiting for any."""
        if corrupt:
            # Later deltas reference the lost picture: every viewer waits for
            # the keyframe this requests.
            for subscriber in self.subscribers:subscriber.waiting_key=True
            self.request_keyframe();return
        frame=[n for n in nals if (n[0]>>1)&0x3F not in (_VPS,_SPS,_PPS)]
        if not frame:return
        if key or len(frame)!=len(nals) or self._format_message is None:
            if not self._live_header():return
        message=live_message(LIVE_FRAME,struct.pack('!BQ',1 if key else 0,max(0,int(now*1_000_000)))+avcc(frame))
        for subscriber in list(self.subscribers):subscriber.publish(key,message,self._format_message,self._params_message)

    # -- frames -----------------------------------------------------------
    def _drain_raw(self):
        return self.decoder.drain() if self.decoder is not None else []

    def _release(self, handle):
        if handle is not None:
            with suppress(Exception):self.decoder_factory.release(handle)

    def _set_latest(self, handle, at):
        old,self.latest=self.latest,handle
        self.latest_at=at;self.latest_key_epoch=self.key_epoch
        self._release(old)

    def _collect(self):
        for status,handle in self._drain_raw():
            if status!=0 or not handle:
                self._release(handle);self.decode_errors+=1
                # Errors right after a keyframe are queued pre-key deltas.
                if self._last_key_fed is None or self.clock()-self._last_key_fed>.5:self._need_key()
                continue
            self.frames+=1;self._set_latest(handle,self.clock())

    async def _until(self, predicate, seconds):
        deadline=self.clock()+seconds
        while True:
            self._collect()
            if predicate() or self.dead or self.clock()>=deadline:return predicate()
            await asyncio.sleep(.004)

    async def frame(self, display_pixels=None, *, first_frame_seconds=FIRST_FRAME_SECONDS, newer_within=.034):
        """Latest decoded frame as a PIL image cropped to the screen, or None.

        Waits for the first frame after open, briefly for a frame received
        after this call (so a screenshot never predates the request by more than
        the stream latency), and for a keyframe while the picture is held after
        loss or looks collapsed."""
        if not self.decoding:return None
        requested=self.clock()
        if self.latest is None:
            if not await self._until(lambda:self.latest is not None,max(0,self.started_at+first_frame_seconds-requested)):return None
        elif self.needs_key:
            await self._until(lambda:not self.needs_key and self.latest_at>requested,.4)
        else:
            await self._until(lambda:self.latest_at>requested,newer_within)
        latest_at=self.latest_at
        image=self._image(display_pixels)
        if image is None or collapsed_extent(image) is None:return image
        epoch=self.key_epoch;self.request_keyframe(force=True)
        if await self._until(lambda:self.latest_key_epoch>epoch,.6):
            return self._image(display_pixels)
        if self.dead and self.clock()-latest_at>DEAD_FRAME_MAX_AGE_SECONDS:return None
        extent=collapsed_extent(image)
        if extent is None:return image
        from PIL import Image
        return image.crop((0,0,*extent)).resize(image.size,Image.BILINEAR)

    def _image(self, display_pixels):
        if self.latest is None or self.dead and self.clock()-self.latest_at>DEAD_FRAME_MAX_AGE_SECONDS:return None
        image=self.decoder_factory.image(self.latest)
        if image is None:return None
        box=frame_content_box(image.size,display_pixels)
        return image.crop(box) if box and box!=(0,0,*image.size) else image


@asynccontextmanager
async def live_screen(rsd, *, decode=True):
    """Start the device's video stream on this RSD session and yield a running
    LiveScreen; the stream is stopped on exit, even after a failed start."""
    from pymobiledevice3.remote.core_device.display_service import DisplayService
    from pymobiledevice3.remote.core_device.screen_stream import open_media_receiver
    async with AsyncExitStack() as stack:
        display=await stack.enter_async_context(DisplayService(rsd))
        transport,receiver_ip=open_media_receiver(display,(4*1024*1024,1024*1024))
        stack.callback(transport.close)
        tokens=[]
        async def stop():
            # Stop only this stream so another client's session (Xcode) is kept.
            with suppress(Exception):
                await asyncio.wait_for(display.stop_media_stream(stop_all=not tokens,identifiers=tokens or None),3)
        stack.push_async_callback(stop)
        answer=await asyncio.wait_for(display.start_video_stream(receiver_ip=receiver_ip,receiver_port=transport.port,sender_ip=rsd.service.address[0],display_id=1,allow_rtcp_fb=True),10)
        config=(answer.get('connection') or {}).get('streamConfig') or {}
        # streamConfig names are the device's: RemoteSSRC is ours and is the
        # stream token stopmediastream reports.
        local_ssrc=int(config.get('RemoteSSRC',0));remote_ssrc=int(config.get('LocalSSRC',0));port=int(config.get('SourcePort',0))
        if local_ssrc:tokens.append(local_ssrc)
        if decode:
            try:
                from pymobiledevice3.remote.core_device import vt_jpeg
                decode=vt_jpeg._IS_DARWIN
            except Exception as exc:
                logger.warning('VideoToolbox unavailable for the live screen: %s',type(exc).__name__);decode=False
        screen=LiveScreen(transport,(rsd.service.address[0],port) if port else None,local_ssrc,remote_ssrc,decode=decode)
        stack.push_async_callback(screen.aclose)
        screen.run()
        yield screen


@asynccontextmanager
async def hid_service(rsd):
    """UniversalHIDService; touch is authorized by the session's live stream."""
    from pymobiledevice3.remote.core_device.hid_service import UniversalHIDServiceService
    async with UniversalHIDServiceService(rsd) as service:yield service


@asynccontextmanager
async def clipboard_service(rsd):
    from pymobiledevice3.remote.core_device.pasteboard_service import PasteboardService
    async with PasteboardService(rsd) as service:yield service


@asynccontextmanager
async def core_device_service(rsd, name):
    """Read-only CoreDevice services kept open on the persistent session."""
    if name=='capture':
        from pymobiledevice3.remote.core_device.screen_capture_service import ScreenCaptureService as Service
    elif name in {'info','lock'}:
        from pymobiledevice3.remote.core_device.device_info import DeviceInfoService as Service
    else:raise ValueError('Unknown read-only service')
    async with Service(rsd) as service:yield service


class _NoFastPath(Exception):
    pass


class _Session:
    def __init__(self, serial, persistent):
        self.serial=serial;self.persistent=persistent;self.stack=AsyncExitStack();self.rsd=None
        self.hid=None;self.hid_stack=None;self.hid_used=0.0;self.keyboard_id=None
        self.stream=None;self.stream_stack=None;self.stream_used=0.0;self.stream_retry_at=0.0;self.stream_lock=asyncio.Lock()
        self.display_pixels=None
        self.services={};self.service_lock=asyncio.Lock();self.lock_query=asyncio.Lock()
        self.users=0;self.leases=0;self.last_used=time.monotonic();self.closed=False;self.reaper=None


class NativePhysical:
    """Per-device RSD sessions. Inputs are serialized per device by a lock that is
    independent of screenshots and of the devicectl CLI."""
    def __init__(self, *, tunnel_factory=device_tunnel, stream_factory=live_screen, hid_factory=hid_service, clipboard_factory=clipboard_service,
                 service_factory=core_device_service, transport_probe=select_transport, idle_seconds=IDLE_SECONDS, auth_settle_seconds=AUTH_SETTLE_SECONDS,
                 clock=time.monotonic):
        self.tunnel_factory=tunnel_factory;self.stream_factory=stream_factory;self.hid_factory=hid_factory;self.clipboard_factory=clipboard_factory
        self.service_factory=service_factory;self.transport_probe=transport_probe;self.idle_seconds=idle_seconds;self.auth_settle_seconds=auth_settle_seconds
        self.clock=clock;self._empty_stream_retries={}
        self.sessions={};self._open_locks={};self._input_locks={};self._userspace_lock=asyncio.Lock();self._closing=False

    def input_lock(self, serial):
        return self._input_locks.setdefault(serial,asyncio.Lock())

    def _busy(self, session):
        return session.users>0 or self.input_lock(session.serial).locked()

    async def _open(self, serial, transport):
        session=_Session(serial,transport=='userspace')
        try:
            async with asyncio.timeout(TUNNEL_OPEN_TIMEOUT_SECONDS):
                rsd=await session.stack.enter_async_context(self.tunnel_factory(serial,transport))
            if self._closing:raise BridgeError('prerequisite_required','Native connection is shutting down')
            if rsd.udid!=serial:raise BridgeError('connection_changed','Native RSD identity did not match the explicitly selected device')
        except (BridgeError,asyncio.CancelledError):
            await asyncio.shield(session.stack.aclose());raise
        except Exception as exc:
            await asyncio.shield(session.stack.aclose())
            raise BridgeError('prerequisite_required','Native connection requires an available trusted device, Developer Mode and supported developer services',details={'backend_exception':type(exc).__name__}) from exc
        session.rsd=rsd
        return session

    async def _open_userspace(self, serial, *, fast_only):
        """Only one userspace tunnel can exist per process. Reads never evict another
        device's session (they fall back to devicectl); input evicts it once no lease
        holds it. Waiting never holds the shared lock, so two devices cannot deadlock."""
        while True:
            async with self._userspace_lock:
                others=[s for key,s in self.sessions.items() if key!=serial]
                if not others or not fast_only and not any(s.users for s in others):
                    for other in others:await self._discard(other,'replaced')
                    return await self._open(serial,'userspace')
            if fast_only:raise _NoFastPath()
            await asyncio.sleep(.05)

    @asynccontextmanager
    async def _lease(self, serial, *, fast_only=False, reuse_only=False):
        async with self._open_locks.setdefault(serial,asyncio.Lock()):
            session=self.sessions.get(serial)
            if session is None:
                if reuse_only:raise _NoFastPath()
                if self._closing:raise _NoFastPath() if fast_only else BridgeError('prerequisite_required','Native input is shutting down; no input was sent')
                try:transport=await self.transport_probe(serial)
                except BridgeError:
                    if fast_only:raise _NoFastPath()
                    raise
                except Exception as exc:
                    if fast_only:raise _NoFastPath() from exc
                    raise BridgeError('prerequisite_required','Native connection requires an available trusted device, Developer Mode and supported developer services',details={'backend_exception':type(exc).__name__}) from exc
                if fast_only and transport!='userspace':raise _NoFastPath()
                session=await (self._open_userspace(serial,fast_only=fast_only) if transport=='userspace' else self._open(serial,transport))
                if session.persistent:
                    self.sessions[serial]=session
                    session.reaper=asyncio.create_task(self._reap(session))
            session.users+=1;session.leases+=1
        try:yield session
        finally:
            session.users-=1;session.last_used=time.monotonic()
            if not session.persistent and session.users==0:await self._discard(session)

    @staticmethod
    def _viewed(session):
        """A connected live viewer is continuous use of the stream."""
        return bool(getattr(session.stream,'subscribers',None))

    def _stream_deadline(self, session):
        """When the stream may close: after idleness of screenshots, input and
        live viewers, or shortly after input when it yields no frames and nobody
        views it (so screenshots can use ScreenCaptureService, which stalls
        while a stream is open)."""
        stream=session.stream
        if stream is None:return None
        if getattr(stream,'decoding',True) or self._viewed(session):return max(session.stream_used,session.hid_used)+self.idle_seconds
        return session.hid_used+DEGRADED_STREAM_LINGER_SECONDS

    async def _reap(self, session):
        """Close HID after input idleness, the stream after idleness of screenshots
        and input, and the whole session after idleness of every use; never while
        an input or read holds it."""
        try:
            while not session.closed:
                now=time.monotonic();idle=self.idle_seconds
                if self._viewed(session):session.stream_used=session.last_used=now
                deadline=session.last_used+idle
                if session.hid is not None:deadline=min(deadline,session.hid_used+idle)
                stream_deadline=self._stream_deadline(session)
                if stream_deadline is not None:deadline=min(deadline,stream_deadline)
                if now<deadline:
                    await asyncio.sleep(deadline-now);continue
                locked=self.input_lock(session.serial).locked()
                if session.hid is not None and now>=session.hid_used+idle and not locked:
                    await self._drop_hid(session)
                stream_deadline=self._stream_deadline(session)
                if stream_deadline is not None and now>=stream_deadline and not locked:
                    await self._drop_stream(session,'idle')
                if not self._busy(session) and now>=session.last_used+idle:
                    await self._discard(session);return
                await asyncio.sleep(min(.25,max(idle,.01)))
        except asyncio.CancelledError:
            pass

    async def _drop_hid(self, session):
        stack,session.hid_stack,session.hid,session.keyboard_id=session.hid_stack,None,None,None
        if stack:
            with suppress(Exception):await asyncio.shield(stack.aclose())

    async def _drop_stream(self, session, reason='stream_closed'):
        # Touch authorization ends with the stream, so HID goes first.
        await self._drop_hid(session)
        stack,stream,session.stream_stack,session.stream=session.stream_stack,session.stream,None,None
        if getattr(stream,'has_frames',False):
            self._empty_stream_retries.pop(session.serial,None)
        elif getattr(stream,'last_packet',False) is None and (reason=='stream_ended' or stream.dead):
            failures,_=self._empty_stream_retries.get(session.serial,(0,0))
            index=min(failures,len(EMPTY_STREAM_RETRY_SECONDS)-1)
            ended_at=getattr(stream,'ended_at',None)
            if ended_at is None:ended_at=self.clock()
            self._empty_stream_retries[session.serial]=(index+1,ended_at+EMPTY_STREAM_RETRY_SECONDS[index])
        # Viewers get END with the cause before the stream stops.
        end=getattr(stream,'end_subscribers',None)
        if end:end(reason)
        if stack:
            with suppress(Exception):await asyncio.shield(stack.aclose())

    async def _drop_service(self, session, name):
        entry=session.services.pop(name,None)
        if entry:
            with suppress(Exception):await asyncio.shield(entry[0].aclose())

    async def _discard(self, session, reason='session_closed'):
        if session.closed:return
        session.closed=True
        if self.sessions.get(session.serial) is session:self.sessions.pop(session.serial)
        if session.reaper and session.reaper is not asyncio.current_task():session.reaper.cancel()
        async def teardown():
            await self._drop_stream(session,reason)
            for name in list(session.services):await self._drop_service(session,name)
            with suppress(Exception):await session.stack.aclose()
        await asyncio.shield(teardown())

    async def close_device(self, serial):
        """Connection epoch change, disconnect or explicit release."""
        session=self.sessions.get(serial)
        if session:await self._discard(session,'device_disconnected')

    async def aclose(self):
        self._closing=True
        for session in list(self.sessions.values()):await self._discard(session,'shutdown')

    @staticmethod
    async def _open_resource(session, manager):
        if session.closed:raise _NoFastPath()
        stack=AsyncExitStack()
        try:
            resource=await stack.enter_async_context(manager)
            if session.closed:raise _NoFastPath()
        except BaseException:
            await asyncio.shield(stack.aclose());raise
        return stack,resource

    async def _stream(self, session):
        """The session's single video stream, opened on first use."""
        async with session.stream_lock:
            if session.closed:raise _NoFastPath()
            if session.stream is not None and session.stream.dead:
                await self._drop_stream(session,'stream_ended')
            if session.stream is None:
                _,retry_at=self._empty_stream_retries.get(session.serial,(0,0))
                if self.clock()<retry_at:raise _NoFastPath()
                # Never leave a ScreenCaptureService connection beside a stream.
                await self._drop_service(session,'capture')
                stack,stream=await self._open_resource(session,self.stream_factory(session.rsd,decode=session.persistent))
                session.stream,session.stream_stack=stream,stack
                if stream.dead:
                    await self._drop_stream(session,'stream_ended')
                    raise _NoFastPath()
            session.stream_used=time.monotonic()
            return session.stream

    async def _hid(self, session):
        if session.closed:raise _NoFastPath()
        if session.hid is not None and (session.stream is None or session.stream.dead):
            await self._drop_hid(session)
        if session.hid is None:
            stream=await self._stream(session)
            # backboardd re-matches HID surfaces to a new stream asynchronously;
            # paid once per stream, not per input.
            settle=stream.started_at+self.auth_settle_seconds-time.monotonic()
            if settle>0:await asyncio.sleep(settle)
            stack,hid=await self._open_resource(session,self.hid_factory(session.rsd))
            if stream.dead or stream is not session.stream:
                await asyncio.shield(stack.aclose())
                raise _NoFastPath()
            session.hid,session.hid_stack,session.keyboard_id=hid,stack,None
        session.hid_used=session.stream_used=time.monotonic()
        return session.hid

    async def _service(self, session, name):
        async with session.service_lock:
            if session.closed:raise _NoFastPath()
            if name not in session.services:
                stack,service=await self._open_resource(session,self.service_factory(session.rsd,name))
                session.services[name]=(stack,service)
            return session.services[name][1]

    async def _read_in(self, session, name, call):
        try:
            async with asyncio.timeout(READ_TIMEOUT_SECONDS):
                return await call(await self._service(session,name))
        except asyncio.CancelledError:
            if name=='lock':
                # A cancelled request may have left its reply on the connection.
                await settle_cleanup(asyncio.create_task(self._drop_service(session,name)))
            raise
        except Exception:
            # A failed read may mean a dead tunnel. Drop the whole session
            # unless an input owns it or only the dedicated lock channel failed.
            if name=='lock':await settle_cleanup(asyncio.create_task(self._drop_service(session,name)))
            elif self.input_lock(session.serial).locked():await self._drop_service(session,name)
            else:await self._discard(session)
            return None

    async def _read(self, serial, name, call):
        """Fast read over the persistent USB session; None means use devicectl."""
        try:
            async with self._lease(serial,fast_only=True) as session:
                return await self._read_in(session,name,call)
        except (_NoFastPath,BridgeError):
            return None

    async def screenshot(self, serial):
        """Latest live-stream frame (PIL image), a ScreenCaptureService PNG when no
        stream can run, or None (use devicectl)."""
        try:
            async with self._lease(serial,fast_only=True) as session:
                now=time.monotonic()
                if session.stream is not None or now>=session.stream_retry_at:
                    try:
                        async with asyncio.timeout(STREAM_OPEN_TIMEOUT_SECONDS):stream=await self._stream(session)
                    except asyncio.CancelledError:raise
                    except _NoFastPath:stream=None
                    except Exception as exc:
                        logger.info('live screen unavailable: %s',type(exc).__name__)
                        session.stream_retry_at=time.monotonic()+STREAM_RETRY_SECONDS;stream=None
                    if stream is not None and getattr(stream,'decoding',True):
                        if not await self._display_pixels(session):return None
                        image=await stream.frame(session.display_pixels)
                        session.stream_used=time.monotonic()
                        if image is not None:return image
                        # Screenshots give up sooner than the RTP watchdog.
                        # Leave a pending handshake or a viewer's stream alive.
                        if not self.input_lock(serial).locked() and (stream.dead or not self._viewed(session) and getattr(stream,'last_packet',False) is not None):
                            async with session.stream_lock:
                                if stream is session.stream:
                                    await self._drop_stream(session,'stream_ended')
                                    if getattr(stream,'last_packet',False) is not None:
                                        session.stream_retry_at=time.monotonic()+STREAM_RETRY_SECONDS
                        return None
                    if stream is not None:
                        # The stream cannot be the screen (no decoder in this runtime).
                        # It authorizes touch until shortly after the last input;
                        # then capture without it. Screenshots never reopen it.
                        session.stream_retry_at=math.inf
                        if self.input_lock(serial).locked() or time.monotonic()<session.hid_used+DEGRADED_STREAM_LINGER_SECONDS or self._viewed(session):return None
                        await self._drop_stream(session)
                if session.stream is not None:return None
                return await self._read_in(session,'capture',self._capture_call)
        except (_NoFastPath,BridgeError):
            return None

    async def _display_pixels(self, session):
        """Display framebuffer size (crops frames), read once per session."""
        if session.display_pixels is None:
            info=await self._read_in(session,'info',self._display_info_call)
            if session.closed:return None
            session.display_pixels=display_pixel_size(info) if info else None
            if session.stream is not None:session.stream.display_pixels=session.display_pixels
        return session.display_pixels

    async def subscribe(self, serial):
        """A viewer of the device's live video (LiveSubscriber), opening the
        persistent session and stream or reusing them; None when the device has
        no persistent USB session or no stream can run. Viewing never opens HID
        and never sends input. The viewer keeps the stream in use until it
        closes; the normal idle rule applies after the last one leaves."""
        try:
            async with self._lease(serial,fast_only=True) as session:
                if session.stream is None and time.monotonic()<session.stream_retry_at<math.inf:return None
                try:
                    async with asyncio.timeout(STREAM_OPEN_TIMEOUT_SECONDS):stream=await self._stream(session)
                except asyncio.CancelledError:raise
                except _NoFastPath:return None
                except Exception as exc:
                    logger.info('live screen unavailable for a viewer: %s',type(exc).__name__)
                    session.stream_retry_at=time.monotonic()+STREAM_RETRY_SECONDS;return None
                if not hasattr(stream,'subscribe'):return None
                # Without the display size the crop is the whole coded frame.
                await self._display_pixels(session)
                if session.closed or stream is not session.stream or stream.dead:return None
                stream.display_pixels=session.display_pixels
                def left(subscriber):session.stream_used=session.last_used=time.monotonic()
                return stream.subscribe(on_close=left)
        except (_NoFastPath,BridgeError):
            return None

    @staticmethod
    async def _capture_call(service):
        output=await service.capture_screenshot()
        image=output.get('image') if isinstance(output,dict) else None
        if not isinstance(image,(bytes,bytearray)) or not bytes(image[:8])==b'\x89PNG\r\n\x1a\n':raise ValueError('Screen capture returned no PNG image')
        return bytes(image)

    @staticmethod
    async def _display_info_call(service):
        result=await service.get_display_info()
        if not isinstance(result,dict):raise ValueError('Display information is not structured')
        return result

    async def display_info(self, serial):
        result=await self._read(serial,'info',self._display_info_call)
        session=self.sessions.get(serial)
        if result is not None and session is not None:
            session.display_pixels=display_pixel_size(result) or session.display_pixels
            if session.stream is not None:session.stream.display_pixels=session.display_pixels
        return result

    @staticmethod
    async def _lock_state_call(service):
        result=await service.get_lockstate()
        required=result.get('passcodeRequired') if isinstance(result,dict) else None
        return required if isinstance(required,bool) else None

    async def lock_state(self, serial) -> bool | None:
        """Return a fresh passcode-required value without opening a new tunnel."""
        if serial not in self.sessions:return None
        try:
            async with asyncio.timeout(LOCK_READ_TIMEOUT_SECONDS),self._lease(serial,reuse_only=True) as session:
                # Cleanup stays under the query lock, so later reads cannot
                # observe a leftover reply on the cancelled channel.
                async with session.lock_query:
                    state=await self._read_in(session,'lock',self._lock_state_call)
                    return state if not session.closed else None
        except (_NoFastPath,TimeoutError):return None

    async def input(self,tool,serial,geometry,targets,args,*,guard=None,on_send=None,still_unlocked=None):
        size=geometry['logical_size']
        orientation=geometry.get('orientation')
        if orientation not in {'portrait','landscapeLeft','landscape_left'}:
            raise BridgeError('unsupported_capability','Native HID mapping requires an observed portrait or landscape-left display')
        if (orientation=='portrait') != (size['width']<=size['height']):
            raise BridgeError('stale_observation','Display dimensions disagree with the observed input orientation')
        def point(name):return to_hid(*targets[name][:2],size,orientation)
        # Preflight the whole operation, including text encoding, before any
        # transport, focus or key state can change.
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
        method=args.get('input_method','keyboard')
        if method not in {'keyboard','clipboard'}:raise BridgeError('invalid_arguments','Unsupported explicit input method')
        if tool in {'type_text','fill'} and method=='keyboard':
            if args.get('keyboard_layout') not in {None,'us'}:raise BridgeError('invalid_arguments','Unsupported keyboard layout declaration')
            if os.environ.get('TAPMESH_PHYSICAL_KEYBOARD_LAYOUT')!='us' and args.get('keyboard_layout')!='us':raise BridgeError('prerequisite_required','Confirm the current US English hardware layout in the owner-approved action or local configuration before literal HID text entry')
            from pymobiledevice3.remote.core_device.hid_service import ASCII_TO_HID
            if any(c not in ASCII_TO_HID for c in args['text']):raise BridgeError('unsupported_capability','Native keyboard insertion is qualified for US-layout ASCII; unsupported Unicode fails before focus/input')
        keys={'return':40,'delete':42,'escape':41,'tab':43,'space':44,'right':79,'left':80,'down':81,'up':82,'select_all':4}
        if tool=='press_key' and args['key'] not in keys:raise BridgeError('unsupported_capability','Semantic key is not supported by native HID')
        async with self.input_lock(serial):
            for attempt in (1,2):
                state={'sent':False,'clipboard_started':False,'clipboard_changed':False,'control':None}
                def sent():
                    state['sent']=True
                    if on_send:on_send()
                async def check_unlocked():
                    if still_unlocked is not None and not await still_unlocked():
                        error=locked_input_error(sent=state['sent'])
                        if state['clipboard_changed']:error.details['clipboard_changed']=True
                        raise error
                async with self._lease(serial) as session:
                    reused=session.leases>1
                    try:
                        service=await self._hid(session)
                        if guard:await guard()
                        if session.closed:raise _NoFastPath()
                        control=state['control']=TouchController(service,on_send=sent,keyboard_id=session.keyboard_id)
                        try:await self._send(tool,session,control,plan,point,targets,args,method,keys,state,sent,check_unlocked if still_unlocked is not None else None)
                        finally:
                            try:await asyncio.shield(control.release())
                            finally:session.keyboard_id=control.keyboard_id;session.hid_used=session.stream_used=time.monotonic()
                    except _NoFastPath as exc:
                        if session.closed:raise BridgeError('connection_changed','The device connection changed before input') from exc
                        raise BridgeError('prerequisite_required','The native stream is unavailable. Wait briefly and try again.') from exc
                    except BridgeError as exc:
                        if state['sent'] or state['clipboard_started']:
                            if exc.side_effect_state=='not_sent':exc.side_effect_state='unknown'
                            await self._discard(session)
                        raise
                    except asyncio.CancelledError:
                        # Interrupted mid-protocol (takeover or deadline): the
                        # connection state is unknown, so never reuse it.
                        await self._discard(session);raise
                    except Exception as exc:
                        await self._discard(session)
                        sent_any=state['sent'] or state['clipboard_started']
                        if not sent_any and reused and attempt==1:continue
                        raise BridgeError('delivery_outcome_unknown' if sent_any else 'prerequisite_required','Native HID was interrupted after transmission; observe before deciding to retry' if sent_any else 'Native media authentication or HID prerequisites are unavailable; no input was transmitted',side_effect_state='unknown' if sent_any else 'not_sent',details={'backend_exception':type(exc).__name__,'clipboard_changed':state['clipboard_changed'] if state['clipboard_changed'] else None if state['clipboard_started'] else False}) from exc
                control=state['control']
                return {'delivery':'sent' if control and control.sent or state['clipboard_changed'] else 'not_sent','verified':False,'completion_condition':'Native HID reports transmitted; app outcome must be observed separately','source':'pymobiledevice3_native_hid','session':'reused' if reused else 'opened','keyboard_state':'unverifiable' if tool=='hide_keyboard' else None,'input_method':method if tool in {'type_text','fill'} else None,'clipboard_changed':state['clipboard_changed'],'propagation_warning':'Explicit device clipboard write may synchronize through Universal Clipboard' if state['clipboard_changed'] else None}

    async def _send(self,tool,session,control,plan,point,targets,args,method,keys,state,sent,check_unlocked):
        async def check_keyboard():
            if check_unlocked:
                await control.keyboard()
                await check_unlocked()

        async def key(usages):
            await check_keyboard()
            await control.key(usages)

        if tool in {'tap','double_tap','long_press'}:
            count=2 if tool=='double_tap' else 1
            for n in range(count):
                await control.gesture(plan,steps=0,duration=args.get('duration_ms',800)/1000 if tool=='long_press' else .06,check_unlocked=check_unlocked)
                if n<count-1:await asyncio.sleep(args.get('interval_ms',120)/1000)
        elif tool in {'swipe','drag','pinch'}:
            await control.gesture(plan,duration=args.get('duration_ms',500 if tool=='pinch' else 700 if tool=='drag' else 300)/1000,steps=30,hold=args.get('hold_ms',300)/1000 if tool=='drag' else 0,check_unlocked=check_unlocked)
        elif tool in {'type_text','fill'}:
            if 'target' in targets:
                p=point('target');await control.gesture([(*p,*p)],duration=.06,steps=0,check_unlocked=check_unlocked)
            if method=='clipboard':
                async with self.clipboard_factory(session.rsd) as clipboard:
                    if check_unlocked:await check_unlocked()
                    state['clipboard_started']=True
                    sent()
                    await clipboard.set_text(args['text']);state['clipboard_changed']=True
            if tool=='fill':
                await key([227,4]);await key([42])
            if method=='clipboard':
                await key([227,25])
            else:
                from pymobiledevice3.remote.core_device.hid_service import ASCII_TO_HID
                text=args['text']
                for start in range(0,len(text),TEXT_LOCK_CHECK_CHUNK_SIZE):
                    await check_keyboard()
                    for char in text[start:start+TEXT_LOCK_CHECK_CHUNK_SIZE]:
                        usage,shift=ASCII_TO_HID[char];await control.key([225,usage] if shift else [usage])
            if tool=='fill' and args.get('submit'):await key([40])
        else:
            usage=41 if tool=='hide_keyboard' else keys[args['key']]
            modifiers=[] if tool=='hide_keyboard' else [dict(shift=225,control=224,alt=226,command=227)[m] for m in args.get('modifiers',[])]
            if tool=='press_key' and args['key']=='select_all':modifiers.append(227)
            await key([*dict.fromkeys(modifiers),usage])


def capability_profile():
    """Operation-level initial profile; limits are qualification boundaries."""
    import importlib.util
    available=importlib.util.find_spec('pymobiledevice3') is not None
    if not available:
        return {cap:{'status':'requires_user_action','reason':'Install pinned pymobiledevice3 Mac dependencies'} for cap in ('input.tap','input.double_tap','input.long_press','input.swipe','input.drag','input.pinch','input.text','input.key','input.keyboard_dismiss','ui.inspect')}
    base={'requires_exclusive_media_session':True,'orientations':['portrait'],'coordinate_space':'device_points','refused_when':['device_locked'],'transport':'persistent USB userspace session; network devices open the native tunnel per input'}
    profiles={}
    for cap in ('input.tap','input.long_press','input.pinch'):
        profiles[cap]={'status':'supported','reason':'Public native HID route produced observed project-fixture changes on the qualified physical device','limits':dict(base)}
    profiles['input.tap']['limits']['orientations']=['portrait','landscapeLeft']
    profiles['input.tap']['reason']='Observed fixture counter change on iPhone portrait and iPad landscape-left; native digitizer axes are transformed from the anchored display'
    for cap in ('input.double_tap','input.swipe','input.drag'):
        profiles[cap]={'status':'degraded','reason':'Native contact route implemented; operation-specific physical repeatability remains under qualification','limits':dict(base)}
    profiles['input.key']={'status':'degraded','reason':'Native hardware keys; Return and Backspace observed, modifier-before-key ordering required','limits':{'keys':['return','delete','escape','tab','space','up','down','left','right','select_all'],'modifiers':['shift','control','alt','command'],'verified_keys':['return','delete','select_all'],'verified_chords':['command+a','command+v','shift+letter'],'orientations':['portrait']}}
    for cap in ('input.text','input.fill'):
        profiles[cap]={'status':'degraded','reason':'US-layout keyboard insertion and explicitly authorized Unicode clipboard-paste are separate methods; no implicit substitution. Input to a locked device is refused.','limits':{'input_methods':{'keyboard':{'character_set':'US-layout ASCII','requires_current_layout_confirmation':True,'per_action_declaration':{'keyboard_layout':'us'},'current_layout_confirmed':os.environ.get('TAPMESH_PHYSICAL_KEYBOARD_LAYOUT')=='us'},'clipboard':{'character_set':'Unicode','policy':'S','clipboard_changed':True,'verified_fixture_readback':'TapMesh تجربة'}},'implicit_clipboard_fallback':False,'orientations':['portrait'],'generic_AX_semantics_verified':False,'fill_verification':'generic target replacement requires readback; combined fixture Cmd+A/Delete and literal insertion observed'}}
    profiles['input.keyboard_dismiss']={'status':'degraded','reason':'Semantic Escape route exists; generic keyboard visibility is unverifiable','limits':{'orientations':['portrait']}}
    profiles['ui.inspect']={'status':'degraded','reason':'Use NAS OCR for non-mutating screen inspection; physical AX focus traversal is excluded from read-only tools','limits':{'sources':['auto','ocr'],'accessibility':'unsupported_for_read_only_inspection','bounds_qualified':False,'semantic_press_verified':False}}
    return profiles
