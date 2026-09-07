"""MCP SDK transport only; no platform or backend imports."""
import asyncio
import json
import os
from contextlib import asynccontextmanager

TOOLS = {'galatea_'+name for name in ('get_capabilities','list_projects','inspect_project','get_campaign',
    'list_operations','get_operation','plan_run','submit_job','observe_job','stop_job','cancel_campaign',
    'query_runs','get_metric_history','get_artifact','compare_runs','freeze_candidate','verify_candidate')}

class Platform:
    def __init__(self, session): self.session=session

    async def call(self,name,arguments):
        if name not in TOOLS: raise ValueError('Unsupported MCP tool')
        result=await self.session.call_tool(name,arguments)
        if result.isError: raise RuntimeError('MCP tool failed')
        value=getattr(result,'structuredContent',None)
        if value is None:
            texts=[item.text for item in result.content if item.type=='text']
            if len(texts)!=1: raise ValueError('Ambiguous MCP response')
            value=json.loads(texts[0])
        if (not isinstance(value,dict) or value.get('schema_version')!='galatea.tools/v1'
            or type(value.get('ok')) is not bool or not value.get('request_id')):
            raise ValueError('Invalid MCP envelope')
        if not value['ok']: raise RuntimeError('Platform rejected request')
        if 'data' not in value: raise ValueError('Missing MCP data')
        return value['data']

    async def preflight(self):
        result=await self.session.list_tools()
        found={item.name for item in result.tools}
        if not TOOLS <= found: raise RuntimeError('Required MCP tools missing: '+','.join(sorted(TOOLS-found)))
        return await self.call('galatea_get_capabilities',{})

    async def snapshot_async(self,state):
        scope={key:state[key] for key in ('project_id','campaign_id')}
        campaign=await self.call('galatea_get_campaign',scope)
        if any(campaign.get(key)!=value for key,value in scope.items()): raise ValueError('Foreign campaign')
        operations=[]; cursor=None; seen=set()
        while True:
            page=await self.call('galatea_list_operations',{**scope,**({'cursor':cursor} if cursor else {})})
            operations.extend(page['items'])
            cursor=page['next_cursor']
            if cursor is None: break
            if cursor in seen or len(seen)>=1000: raise ValueError('Invalid operation pagination')
            seen.add(cursor)
        ids=[op['operation_id'] for op in operations]
        if len(set(ids))!=len(ids): raise ValueError('Duplicate operation records')
        return campaign,operations

@asynccontextmanager
async def connect(config):
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client
    from mcp.client.streamable_http import streamable_http_client
    import httpx
    from contextlib import AsyncExitStack
    if config['transport']=='stdio':
        transport=stdio_client(StdioServerParameters(command=config['command'],args=config.get('args',[]),env=config.get('env',{})))
    elif config['transport']=='http':
        url=config['url']
        if not url.startswith('https://') and not (config.get('allow_loopback_http') and url.startswith('http://127.0.0.1:')):
            raise ValueError('MCP deployment requires HTTPS')
        token=os.environ[config.get('token_env','GALATEA_MCP_TOKEN')]
        http_client=httpx.AsyncClient(headers={'Authorization':'Bearer '+token}, trust_env=False,
                                      timeout=httpx.Timeout(60, read=60), follow_redirects=False,
                                      proxy=config.get('proxy_url'))
        transport=streamable_http_client(url,http_client=http_client)
    else: raise ValueError('Unknown MCP transport')
    async with AsyncExitStack() as stack:
        if config['transport']=='http':
            await stack.enter_async_context(http_client)
        streams=await stack.enter_async_context(transport)
        session=await stack.enter_async_context(ClientSession(streams[0],streams[1]))
        await session.initialize()
        yield Platform(session)

class SyncPlatform:
    """A connection per bounded observation avoids sharing SDK loops across threads."""
    def __init__(self,config): self.config=config
    def _run(self, action):
        async def invoke():
            async with connect(self.config) as platform: return await action(platform)
        return asyncio.run(invoke())
    def snapshot(self,state): return self._run(lambda p:p.snapshot_async(state))
    def preflight(self): return self._run(lambda p:p.preflight())
    def cancel(self,state):
        return self._run(lambda p:p.call('galatea_cancel_campaign',{**{k:state[k] for k in ('project_id','campaign_id')},'reason':'Trusted user cancellation'}))
    def stop(self,state,operation_id):
        return self._run(lambda p:p.call('galatea_stop_job',{**{k:state[k] for k in ('project_id','campaign_id')},'operation_id':operation_id,'reason':'Campaign cancellation'}))
    def verify_candidate(self,state,candidate_id):
        scope={key:state[key] for key in ('project_id','campaign_id')}
        return self._run(lambda p:p.call('galatea_verify_candidate',{**scope,'candidate_id':candidate_id}))
