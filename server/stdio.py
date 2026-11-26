"""Optional official MCP stdio bridge. Credentials and logs never go to stdout."""
import asyncio
import os
from pathlib import Path
import sys
from urllib.parse import urlsplit
import uuid
import httpx
from mcp.server.stdio import stdio_server
from agent.safety import BridgeError, read_secret
from tapmesh_runtime import _read_json
from .app import make_mcp


def connect():
    connection_file=os.environ.get('TAPMESH_CONNECTION_FILE')
    if connection_file is not None:
        profile_file=Path(connection_file).expanduser()
        configuration=_read_json(profile_file)
        url=configuration.get('api_url') if configuration else None
        token_file=profile_file.parent/'server.token'
    else:
        url=os.environ.get('TAPMESH_SERVER_URL','http://127.0.0.1:48793')
        token_file=os.environ.get('TAPMESH_SERVER_TOKEN_FILE')
    if not isinstance(url,str): raise ValueError('Missing TapMesh service URL')
    endpoint=urlsplit(url)
    if (endpoint.scheme not in {'http','https'} or endpoint.hostname not in {'127.0.0.1','localhost','::1'}
        or endpoint.username is not None or endpoint.password is not None or endpoint.port==0):
        raise ValueError('Stdio bridge must connect through loopback')
    return url,read_secret(token_file)


class HTTPBridge:
    def __init__(self, connect): self.connect=connect
    async def call(self, session, tool, arguments):
        payload={'session_id':session,'tool':tool,'arguments':arguments}
        timeout=arguments.get('timeout_ms',1800000)/1000+10
        for _ in range(2):
            try: url,token=await asyncio.to_thread(self.connect)
            except (OSError,ValueError,RuntimeError): break
            async with httpx.AsyncClient(base_url=url,headers={'Authorization':'Bearer '+token},follow_redirects=False,trust_env=False) as client:
                try:
                    response=await client.post('/api/call',json=payload,timeout=timeout)
                except httpx.ConnectError:
                    # No request reached the old service. Resolve the profile again.
                    continue
                response.raise_for_status()
                return response.json()
        error=BridgeError('backend_unavailable',"TapMesh isn't running. Open TapMesh and try again.",retryable=True)
        return {'request_id':uuid.uuid4().hex,'status':'error','error':error.public()}


async def run():
    mcp=make_mcp(HTTPBridge(connect),stdio_session='stdio-'+uuid.uuid4().hex)
    async with stdio_server() as (read,write): await mcp.run(read,write,mcp.create_initialization_options())


def main():
    import logging
    logging.basicConfig(stream=sys.stderr,level=logging.WARNING)
    asyncio.run(run())


if __name__=='__main__': main()
