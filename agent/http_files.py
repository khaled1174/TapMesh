"""Keep artifact response ownership until the last streamed body is sent, and
release live-video streams however their response ends."""
import time
from starlette.responses import FileResponse, JSONResponse, StreamingResponse
from .safety import BridgeError

LIVE_MEDIA_TYPE='application/vnd.tapmesh.live'


class ClosingStreamingResponse(StreamingResponse):
    """A long-lived body whose source is released when the response ends for
    any reason, including a client that leaves before the first byte (when the
    body generator never starts and its own cleanup would never run)."""
    def __init__(self, content, *, on_close, **kwargs):
        super().__init__(content, **kwargs)
        self.on_close=on_close

    async def __call__(self, scope, receive, send):
        try: await super().__call__(scope,receive,send)
        finally:
            try:
                aclose=getattr(self.body_iterator,'aclose',None)
                if aclose: await aclose()
            finally: await self.on_close()


def live_error(exc):
    """Pre-stream refusal: 404 unknown device, 400 malformed request, otherwise
    409 live_unavailable (the original code is kept in details.reason)."""
    if exc.code in {'device_not_found','device_not_selected'}: status=404
    elif exc.code=='invalid_arguments': status=400
    else:
        status=409
        if exc.code!='live_unavailable': exc=BridgeError('live_unavailable',exc.message,retryable=exc.retryable,details={**exc.details,'reason':exc.code})
    return JSONResponse({'error':exc.public()},status_code=status,headers={'Cache-Control':'no-store'})


class TrackedFileResponse(FileResponse):
    def __init__(self, path, *, tracker, **kwargs):
        super().__init__(path, **kwargs)
        self.tracker=tracker

    async def __call__(self, scope, receive, send):
        if time.monotonic()<self.tracker.update_deadline:
            await JSONResponse({'error':BridgeError('overloaded','TapMesh is preparing an update; retry after it restarts',retryable=True).public()},status_code=503)(scope,receive,send)
            return
        self.tracker.active_downloads+=1
        try: await super().__call__(scope,receive,send)
        finally: self.tracker.active_downloads-=1
