"""Bounded visual evidence for non-submitting text replacement.

This proves a visible text target at the original point, not AX field semantics.
Every unexplained pixel change is rejected. No input or retargeting occurs here.
"""
from collections import OrderedDict
import asyncio
import io
import math
import re
import time
import unicodedata

from PIL import Image, ImageChops, ImageDraw

from .safety import BridgeError
from .native_physical import frame_hash, validate_text_context


class FrameAnchors:
    """Private, short-lived PNGs; never part of responses, history or disk state."""
    def __init__(self, *, max_bytes=16*1024*1024, max_entries=64, max_frame=4*1024*1024,
                 max_age=30, clock=time.monotonic):
        self.max_bytes=max_bytes; self.max_entries=max_entries; self.max_frame=max_frame
        self.max_age=max_age; self.clock=clock; self.frames=OrderedDict(); self.size=0
        self._timer=None

    def _remove(self, key):
        self.size-=len(self.frames.pop(key)[1])

    def _prune(self):
        now=self.clock()
        for key,(at,_) in list(self.frames.items()):
            if now-at>=self.max_age:self._remove(key)

    def _schedule(self):
        if self._timer:self._timer.cancel();self._timer=None
        if not self.frames:return
        try:loop=asyncio.get_running_loop()
        except RuntimeError:return
        oldest=next(iter(self.frames.values()))[0]
        self._timer=loop.call_later(max(.001,oldest+self.max_age-self.clock()),self._expire)

    def _expire(self):
        self._timer=None;self._prune();self._schedule()

    def put(self, scope, device, epoch, fingerprint, content):
        self._prune()
        if len(content)>min(self.max_frame,self.max_bytes):self._schedule();return
        key=(scope,device,epoch,fingerprint)
        if key in self.frames:self._remove(key)
        while self.frames and (len(self.frames)>=self.max_entries or self.size+len(content)>self.max_bytes):
            self._remove(next(iter(self.frames)))
        self.frames[key]=(self.clock(),bytes(content));self.size+=len(content)
        self._schedule()

    def get(self, scope, device, epoch, fingerprint):
        self._prune()
        self._schedule()
        value=self.frames.get((scope,device,epoch,fingerprint))
        return value[1] if value else None

    def clear_device(self, device):
        for key in list(self.frames):
            if key[1]==device:self._remove(key)
        self._schedule()

    def clear(self):
        if self._timer:self._timer.cancel();self._timer=None
        self.frames.clear();self.size=0


def _reject(reason):
    raise BridgeError('stale_observation','The text target or surrounding view could not be revalidated; obtain a fresh observation',
                      details={'frame_guard_reason':reason})


def _number(value):
    return not isinstance(value,bool) and isinstance(value,(int,float)) and math.isfinite(value)


def _rows(result, logical):
    if (not isinstance(result,dict) or result.get('source')!='ocr' or result.get('logical_size')!=logical
            or result.get('backend_truncated',False) is not False or result.get('truncated',False) is not False):
        _reject('incomplete_ocr')
    rows=result.get('elements')
    if not isinstance(rows,list) or not 3<=len(rows)<=1000:_reject('incomplete_ocr')
    normalized=[]
    for item in rows:
        box=item.get('bounds') if isinstance(item,dict) else None
        confidence=item.get('confidence') if isinstance(item,dict) else None
        if (not isinstance(box,dict) or set(box)!={'x','y','width','height'}
                or not all(_number(v) for v in box.values()) or min(box.values())<0
                or box['width']<=0 or box['height']<=0
                or box['x']+box['width']>logical['width']+.01 or box['y']+box['height']>logical['height']+.01
                or not isinstance(item.get('label'),str) or not item['label'].strip()
                or item.get('source')!='ocr' or not _number(confidence) or not 0<=confidence<=1):
            _reject('untrusted_ocr')
        normalized.append({'label':unicodedata.normalize('NFC',' '.join(item['label'].split())),
                           'bounds':dict(box),'confidence':confidence})
    return normalized


def _clock(row, logical):
    box=row['bounds']
    match=re.fullmatch(r'(\d{1,2}):(\d{2})(.*)',row['label'])
    if (not match or int(match[1])>23 or int(match[2])>59 or row['confidence']<.9
            or box['x']>logical['width']/2 or box['y']>64 or box['height']>28
            or box['width']>min(240,logical['width']/2)):
        return None
    suffix=match[3]
    if suffix and not suffix[0].isspace() and not all(unicodedata.category(c)[0] in {'P','S'} for c in suffix):
        return None
    return suffix


def _near(a,b,tolerance=1):
    return all(abs(a[k]-b[k])<=tolerance for k in ('x','y','width','height'))


def _near_edges(a,b,tolerance):
    return (abs(a['x']-b['x'])<=tolerance and abs(a['y']-b['y'])<=tolerance
            and abs(a['x']+a['width']-b['x']-b['width'])<=tolerance
            and abs(a['y']+a['height']-b['y']-b['height'])<=tolerance)


def _inside(point,box):
    return box['x']<=point[0]<box['x']+box['width'] and box['y']<=point[1]<box['y']+box['height']


def _unchanged_glyph_edges(a,b,mask,caret,box,other,sx,sy):
    left=min(box['x'],other['x']);top=min(box['y'],other['y'])
    right=max(box['x']+box['width'],other['x']+other['width'])
    bottom=max(box['y']+box['height'],other['y']+other['height'])
    region=(max(0,math.floor(left*sx)),max(0,math.floor(top*sy)),
            min(a.width,math.ceil(right*sx)),min(a.height,math.ceil(bottom*sy)))
    width,height=region[2]-region[0],region[3]-region[1]
    if bottom-top>48 or width<=0 or height<=0 or width*height>256000:return None
    crops=[image.crop(region) for image in (a,b)]
    colors=[crop.getcolors(1024) for crop in crops]
    if any(not palette for palette in colors):return None
    dominant=[max(palette,key=lambda item:item[0]) for palette in colors]
    background=dominant[0][1]
    if dominant[1][1]!=background or any(count<width*height/2 for count,_ in dominant):return None
    # One frame must show only the unchanged background at every changed
    # caret pixel. Replacing a glyph with a blue stroke cannot satisfy this.
    clean=[]
    for image in (a,b):
        if all(image.getpixel((x,y))==background
               for y in range(caret[1],caret[3]) for x in range(caret[0],caret[2])
               if mask.getpixel((x,y))):
            clean.append(image)
    if not clean:return None
    crop=clean[0].crop(region)
    channels=ImageChops.difference(crop,Image.new('RGB',crop.size,background)).split()
    ink=ImageChops.lighter(ImageChops.lighter(channels[0],channels[1]),channels[2]).getbbox()
    if not ink or (ink[2]-ink[0])/sx<4 or (ink[3]-ink[1])/sy<8:return None
    return (region[0]+ink[0])/sx,(region[0]+ink[2])/sx


def _blue_antialias_transition(background,pixel):
    # A rounded 8-bit blend must admit a foreground satisfying the existing
    # blue thresholds. Solve for inverse opacity, including half-LSB rounding.
    low,high=1.0,math.inf
    delta=[pixel[i]-background[i] for i in range(3)]
    constraints=[]
    for i in range(3):
        constraints.extend([(background[i],delta[i]+.5,0),
                            (255-background[i],-delta[i]+.5,0)])
    constraints.extend([(background[2],delta[2]+.5,120),
                        (255-background[0],-delta[0]+.5,40),
                        (255-background[1],-delta[1]+.5,20),
                        (background[2]-background[0],delta[2]-delta[0]+1,40),
                        (background[2]-background[1],delta[2]-delta[1]+1,20)])
    for offset,slope,minimum in constraints:
        if slope>0:low=max(low,(minimum-offset)/slope)
        elif slope<0:high=min(high,(minimum-offset)/slope)
        elif offset<minimum:return False
        if low>high:return False
    return True


def validate_text_frame(before, after, logical, baseline, current, point, *, expected=None):
    """Prove a fixed text target and unchanged pixels outside system indicators/caret."""
    if (not isinstance(logical,dict) or set(logical)!={'width','height'}
            or not all(_number(v) and v>0 for v in logical.values())
            or len(point)!=2 or not all(_number(v) for v in point)):
        _reject('geometry')
    old=_rows(baseline,logical);new=_rows(current,logical)
    candidates=[i for i,row in enumerate(old) if _inside(point,row['bounds'])]
    if len(candidates)!=1:_reject('ambiguous_target')
    target_index=candidates[0];target=old[target_index]
    if (target['confidence']<.9 or _clock(target,logical) is not None
            or expected is not None and (target['label']!=unicodedata.normalize('NFC',' '.join(expected['label'].split()))
                                         or not _near(target['bounds'],expected['bounds'],.01))):
        _reject('untrusted_target')
    matches=[row for row in new if _inside(point,row['bounds'])]
    if len(matches)!=1:_reject('ambiguous_current_target')
    replacement=matches[0]
    if (replacement['confidence']<.9 or replacement['label']!=target['label']
            # Vision can change glyph-box edges by several points on identical
            # pixels. Only the separately checked standard caret pattern may
            # differ near a text edge; all other pixels remain exact below.
            or not _near_edges(target['bounds'],replacement['bounds'],6)):_reject('target_changed')
    if sum(_clock(row,logical) is None for row in old)<3:_reject('insufficient_context')
    try:
        with Image.open(io.BytesIO(before)) as first,Image.open(io.BytesIO(after)) as second:
            if first.size!=second.size or first.width*first.height>8_000_000:_reject('image_geometry')
            sx,sy=first.width/logical['width'],first.height/logical['height']
            if not 1<=sx<=4 or abs(sx-sy)>.01:_reject('image_geometry')
            a=first.convert('RGBA');b=second.convert('RGBA')
    except (OSError,ValueError,Image.DecompressionBombError):
        _reject('invalid_image')
    if ImageChops.difference(a.getchannel('A'),b.getchannel('A')).getbbox():_reject('alpha_changed')
    a=a.convert('RGB');b=b.convert('RGB')
    channels=ImageChops.difference(a,b).split()
    mask=ImageChops.lighter(ImageChops.lighter(channels[0],channels[1]),channels[2]).point(lambda v:255 if v else 0)
    changed=mask.width*mask.height-mask.histogram()[0]
    if changed>1000*sx*sy:_reject('large_pixel_change')
    remaining=mask.copy();allowed=[]

    def raster(box,pad=0):
        return (max(0,math.floor((box['x']-pad)*sx)),max(0,math.floor((box['y']-pad)*sy)),
                min(mask.width,math.ceil((box['x']+box['width']+pad)*sx)),
                min(mask.height,math.ceil((box['y']+box['height']+pad)*sy)))

    clocks=[row for row in old if _clock(row,logical) is not None]
    clock_matches=[row for row in new if len(clocks)==1 and _clock(row,logical)==_clock(clocks[0],logical)
                   and _near_edges(clocks[0]['bounds'],row['bounds'],4)]
    if len(clocks)==1 and len(clock_matches)==1:
        one=clocks[0]['bounds'];two=clock_matches[0]['bounds']
        band={'x':0,'y':max(0,min(one['y'],two['y'])-6),'width':logical['width'],
              'height':max(one['y']+one['height'],two['y']+two['height'])+6-max(0,min(one['y'],two['y'])-6)}
        if _inside(point,band):_reject('status_target')
        status=Image.new('L',mask.size);draw=ImageDraw.Draw(status)
        # The detected clock and right-hand system indicators are separate
        # regions. An arbitrary change in the middle of the header is unsafe.
        clock_box={'x':min(one['x'],two['x'])-2,'y':band['y'],
                   'width':max(one['x']+one['width'],two['x']+two['width'])-min(one['x'],two['x'])+4,
                   'height':band['height']}
        indicators={**band,'x':logical['width']*.65,'width':logical['width']*.35}
        for region in (clock_box,indicators):
            x1,y1,x2,y2=raster(region);draw.rectangle((x1,y1,x2-1,y2-1),fill=255)
        for rows in (old,new):
            for row in rows:
                row_box=row['bounds']
                indicator_glyph=(row_box['x']>=indicators['x'] and row_box['y']>=indicators['y']
                                 and row_box['y']+row_box['height']<=indicators['y']+indicators['height'])
                if _clock(row,logical) is None and not indicator_glyph:
                    x1,y1,x2,y2=raster(row['bounds'],2);draw.rectangle((x1,y1,x2-1,y2-1),fill=0)
        explained=ImageChops.multiply(mask,status)
        if explained.getbbox():allowed.append('clock_status')
        remaining=ImageChops.subtract(remaining,explained)
    caret=remaining.getbbox();caret_edge_source=None
    if caret:
        left,top,right,bottom=(caret[0]/sx,caret[1]/sy,caret[2]/sx,caret[3]/sy)
        box=target['bounds'];other=replacement['bounds']
        # Vision sometimes includes the blue caret in the text bounding box.
        # Its entire changed column must stay within four points of that edge.
        edge=(left>=min(box['x']+box['width'],other['x']+other['width'])-4
              and right<=max(box['x']+box['width'],other['x']+other['width'])+8
              or right<=max(box['x'],other['x'])+4 and left>=min(box['x'],other['x'])-8)
        if (right-left>4 or not 12<=bottom-top<=40
                or top<min(box['y'],other['y'])-8
                or bottom>max(box['y']+box['height'],other['y']+other['height'])+8):
            _reject('unexplained_pixels')
        caret_edge_source='ocr'
        if not edge:
            glyphs=_unchanged_glyph_edges(a,b,remaining,caret,box,other,sx,sy)
            if glyphs:
                edge=(left>=glyphs[1]-4 and right<=glyphs[1]+8
                      or right<=glyphs[0]+4 and left>=glyphs[0]-8)
                caret_edge_source='unchanged_glyph_pixels'
        if not edge:_reject('unexplained_pixels')
        count=blue=0
        for y in range(caret[1],caret[3]):
            for x in range(caret[0],caret[2]):
                if remaining.getpixel((x,y)):
                    count+=1
                    first,second=a.getpixel((x,y)),b.getpixel((x,y))
                    if any(rgb[2]>=120 and rgb[2]-rgb[0]>=40 and rgb[2]-rgb[1]>=20
                           for rgb in (first,second)):blue+=1
                    elif not (first[0]==first[1]==first[2] and second[0]==second[1]==second[2]
                              or _blue_antialias_transition(first,second)
                              or _blue_antialias_transition(second,first)):
                        _reject('unqualified_caret_color')
        if count<.65*(caret[2]-caret[0])*(caret[3]-caret[1]) or blue<.9*count:
            _reject('unqualified_caret')
        allowed.append('blue_caret')
    return {'mode':'stable_text_target','changed_pixels':changed,'allowed_changes':allowed,
            **({'caret_edge_source':caret_edge_source} if caret else {})}


class TextFrameCheck:
    """Revalidate an issued point; never choose a different point or send input."""
    def __init__(self, anchors, tool, device, geometry, arguments, context):
        self.anchors=anchors;self.device=device;self.logical=geometry['logical_size']
        self.targets=context.get('targets',{});self.advisory=context.get('sensitive_state') or {}
        self.record=None;self.reference=None;self.expected=None;self.rows={};self.proofs={}
        self.scope='session:'+str(context.get('session_id',''))
        self.ocr=getattr(context.get('router'),'ocr',None)
        if self.targets:
            hashes={t[2]['public'].get('frame_pixel_sha256') for t in self.targets.values()}
            if len(hashes)!=1 or None in hashes:_reject('missing_frame_fingerprint')
            self.reference=next(iter(hashes))
        self.soft=(tool=='fill' and arguments.get('frame_validation','exact')=='text_target'
                   and arguments.get('field_context')=='ordinary_app'
                   and not arguments.get('submit',False) and set(self.targets)=={'target'}
                   and geometry.get('orientation')=='portrait' and isinstance(context.get('session_id'),str)
                   and hasattr(self.ocr,'inspect_frame'))
        if self.soft:
            validate_text_context(self.advisory,arguments)
            self.record=self.targets['target'][2]
            if self.record['public'].get('orientation')!='portrait':self.soft=False
            reference=arguments.get('target',{}).get('ref')
            if reference:
                matches=[e for e in self.record['elements'] if e.get('ref')==reference]
                if len(matches)!=1:_reject('missing_target_reference')
                self.expected=matches[0]

    async def _scene(self, content, fingerprint):
        if fingerprint not in self.rows:
            if self.record and fingerprint==self.reference and self.record['public'].get('source')=='ocr':
                if not self.record.get('elements_complete'):_reject('incomplete_baseline')
                self.rows[fingerprint]={'source':'ocr','logical_size':self.record['public']['logical_size'],
                                        'elements':self.record['elements'],'backend_truncated':False}
            else:
                self.rows[fingerprint]=await self.ocr.inspect_frame(content,self.logical)
        return self.rows[fingerprint]

    async def _against(self, expected_hash, scope, content, fingerprint):
        if fingerprint==expected_hash:return {'mode':'exact_frame'}
        if not self.soft:_reject('frame_changed')
        before=self.anchors.get(scope,self.device['device_id'],self.device['connection_epoch'],expected_hash)
        if before is None:_reject('frame_anchor_expired')
        baseline=await self._scene(before,expected_hash)
        current=await self._scene(content,fingerprint)
        return validate_text_frame(before,content,self.logical,baseline,current,self.targets['target'][:2],
                                   expected=self.expected if expected_hash==self.reference else None)

    async def verify(self, content):
        if not self.targets:return {'mode':'focused_input'}
        fingerprint=frame_hash(content)
        if fingerprint in self.proofs:return self.proofs[fingerprint]
        proof=await self._against(self.reference,self.scope,content,fingerprint)
        advisory_hash=self.advisory.get('_frame_pixel_sha256')
        if advisory_hash and advisory_hash!=fingerprint and advisory_hash!=self.reference:
            other=await self._against(advisory_hash,'preflight',content,fingerprint)
            if other['mode']!='exact_frame':proof=other
        self.proofs[fingerprint]=proof
        return proof
