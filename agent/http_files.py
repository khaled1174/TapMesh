"""Keep artifact response ownership until the last streamed body is sent."""
import time
from starlette.responses import FileResponse, JSONResponse
from .safety import BridgeError


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
