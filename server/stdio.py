"""Optional official MCP stdio bridge. Credentials and logs never go to stdout."""
import asyncio
import os
from pathlib import Path
import sys
import uuid
import httpx
from mcp.server.stdio import stdio_server
from agent.safety import read_secret
from .app import make_mcp


class HTTPBridge:
    def __init__(self, client): self.client=client
    async def call(self, session, tool, arguments):
        response=await self.client.post('/api/call',json={'session_id':session,'tool':tool,'arguments':arguments},timeout=arguments.get('timeout_ms',1800000)/1000+10)
        response.raise_for_status(); return response.json()


async def run():
    token=read_secret(os.environ.get('TAPMESH_SERVER_TOKEN_FILE'))
    url=os.environ.get('TAPMESH_SERVER_URL','http://127.0.0.1:48793')
    from urllib.parse import urlsplit
    if urlsplit(url).hostname not in {'127.0.0.1','localhost','::1'}: raise RuntimeError('Stdio bridge must connect through loopback tunnel')
    async with httpx.AsyncClient(base_url=url,headers={'Authorization':'Bearer '+token},follow_redirects=False,trust_env=False) as client:
        mcp=make_mcp(HTTPBridge(client),stdio_session='stdio-'+uuid.uuid4().hex)
        async with stdio_server() as (read,write): await mcp.run(read,write,mcp.create_initialization_options())


def main():
    import logging
    logging.basicConfig(stream=sys.stderr,level=logging.WARNING)
    asyncio.run(run())


if __name__=='__main__': main()
