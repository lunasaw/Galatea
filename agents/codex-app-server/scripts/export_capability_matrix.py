#!/usr/bin/env python3
"""Export review inventories from the bundled native schemas and source routes.

Source references locate implementations; they are not end-to-end test results.
"""
import csv
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / 'docs/native-capabilities'
INTERNAL = {
    'initialize', 'thread/unsubscribe', 'thread/increment_elicitation',
    'thread/decrement_elicitation', 'thread/loaded/list', 'thread/items/list',
    'thread/items/read', 'thread/inject_items', 'mock/experimentalMethod',
    'externalAgentConfig/import/recordHistory', 'fuzzyFileSearch',
}
ADVANCED = {
    'thread/start', 'thread/resume', 'thread/fork', 'thread/settings/update',
    'turn/start', 'turn/steer', 'turn/settings/update', 'review/start',
    'plugin/share/updateTargets', 'rollout/compress',
}
RESOURCES = {'thread/attachment/add', 'thread/attachment/list', 'thread/attachment/remove',
             'thread/attachmentOwner/list', 'thread/readState/update',
             'thread/prediction/request', 'thread/shellCommand'}


def write(name, fields, rows):
    with (OUTPUT / name).open('w', newline='') as stream:
        writer = csv.DictWriter(stream, fieldnames=fields, lineterminator='\n')
        writer.writeheader()
        writer.writerows(rows)


def export():
    OUTPUT.mkdir(exist_ok=True)
    protocol = json.loads((ROOT / 'src/codex_agent/protocol/0.163.0.json').read_text())
    config = json.loads((ROOT / 'src/codex_agent/protocol/config.schema.json').read_text())
    sources = [*sorted((ROOT / 'frontend/src').rglob('*.ts')),
               *sorted((ROOT / 'frontend/src').rglob('*.tsx')),
               *sorted((ROOT / 'src/codex_agent').rglob('*.py'))]
    lines = {str(p.relative_to(ROOT)): p.read_text().splitlines() for p in sources
             if '.test.' not in p.name}

    def references(method):
        return '; '.join(f'{path}:{number}' for path, content in lines.items()
                         for number, line in enumerate(content, 1)
                         if any(quote + method + quote in line for quote in ('"', "'"))
                         or (method.isidentifier() and re.search(r'\b' + re.escape(method) + r'\s*:', line)))

    rows = []
    for variant in protocol['client']['oneOf']:
        method = variant['properties']['method']['enum'][0]
        status, entry, boundary = '已有功能', '专用页面或工作区', '受原生权限、功能阶段和外部服务约束'
        if method in INTERNAL:
            status, entry = '客户端编排/替代流程', 'Host、历史、订阅、搜索或兼容工具'
            boundary = '无需逐方法按钮；mock 仅协议调试；历史注入不替代已绑定聊天身份'
        elif method.startswith('userVerification/'):
            status, entry, boundary = '原生提供方不可用', '按实际提供方状态隐藏', '当前构建返回 providerUnavailable'
        elif method in ADVANCED:
            status, entry = '完整原生参数表单与现有主流程', ('设置 → 高级原生能力' if method in {'plugin/share/updateTargets', 'rollout/compress'} else '聊天 /advanced')
        elif method in RESOURCES:
            status, entry = '专用流程', '聊天 /resources'
        elif method.startswith('account/') and method not in {'account/read', 'account/rateLimits/read'}:
            status, entry = '专用流程与原生参数表单', '账户与登录'
            boundary = '真实认证/额度/邮件需账号服务；chatgptAuthTokens 内部分支不开放'
        elif method in {'config/read', 'config/value/write', 'config/batchWrite'}:
            status, entry = '完整配置 Schema 与版本化叶字段编辑', '配置 → 全部原生设置'
            boundary = '只写用户层；103 域含 2 个旧 profile 只读项；value/write 由 batchWrite 替代'
        elif method in {'command/exec', 'process/spawn'}:
            status, entry = '终端与完整高级执行设置', '终端 → 高级执行设置'
            boundary = 'process 不使用 Codex 沙箱；恢复保留身份；高级 env 不存浏览器持久化'
        elif method == 'thread/realtime/start':
            status, entry = 'WebSocket/WebRTC 与完整高级启动参数', '语音与实时对话'
            boundary = '浏览器设备和主机设备分开；远端媒体服务尚需真实环境验证'
        elif method in {'memory/status', 'thread/memoryMode/set'}:
            entry = 'Memory 与完整原生设置'
        elif method in {'hooks/list', 'app/read', 'experimentalFeature/enablement/set'}:
            entry = '对应设置页面与完整结构化配置'
        if method == 'thread/start':
            boundary = 'dynamicTools 由 Host 注册表注入；其余实验参数由原生验证'
        if method == 'thread/resume':
            boundary = '绑定聊天按 threadId 恢复，不用 path/history 替换 Host 的聊天身份'
        if method == 'review/start':
            boundary = 'inline 与 detached；独立审查聊天由 Host 绑定并跟踪 admission'
        rows.append(dict(method=method, coverage=status, entry=entry,
                         boundary=boundary, source_references=references(method)))
    write('methods.csv', ['method', 'coverage', 'entry', 'boundary', 'source_references'], rows)

    def parameters(schema, branch=''):
        if '$ref' in schema:
            name = schema['$ref'].rsplit('/', 1)[-1]
            yield from parameters(protocol['client']['definitions'][name], branch)
            return
        for name, value in schema.get('properties', {}).items():
            yield branch, name, name in schema.get('required', []), value
        for union in ('oneOf', 'anyOf', 'allOf'):
            for index, value in enumerate(schema.get(union, [])):
                yield from parameters(value, f'{branch}/{union}/{index}')

    fields = []
    for variant in protocol['client']['oneOf']:
        method = variant['properties']['method']['enum'][0]
        for branch, name, required, schema in parameters(variant['properties'].get('params', {})):
            ownership = '原生参数；见逐方法清单中的入口和边界'
            if method == 'thread/start' and name == 'dynamicTools':
                ownership = 'Host ToolRegistry 管理，不开放任意工具注册'
            elif method == 'thread/resume' and name in {'path', 'history'}:
                ownership = '已绑定聊天由 Host 使用 threadId 恢复，不开放身份替换'
            elif method == 'account/login/start' and branch == '/oneOf/3':
                ownership = '内部 chatgptAuthTokens 凭据分支；普通用户表单隐藏'
            fields.append(dict(method=method, branch=branch, parameter=name, required=required,
                               ownership=ownership, schema=json.dumps(schema, ensure_ascii=False, separators=(',', ':'))))
    write('parameters.csv', ['method', 'branch', 'parameter', 'required', 'ownership', 'schema'], fields)

    write('settings.csv', ['key', 'editing', 'scope', 'description'], (
        dict(key=key, editing='旧项只读/可删除' if value.get('deprecated') and key in {'profile', 'profiles'} else 'Schema 表单/用户层版本化写入',
             scope='CLI/TUI 配置' if key in {'tui', 'file_opener', 'disable_paste_burst', 'hide_agent_reasoning', 'show_raw_agent_reasoning'} else
                   '宿主启动配置' if key in {'sqlite_home', 'log_dir', 'experimental_thread_store', 'thread_unload_delay_secs', 'allow_symlinked_codex_home'} else
                   '原生默认值；生效时机由运行时决定', description=value.get('description', ''))
        for key, value in config['properties'].items()))

    for domain, filename in [('server', 'server-requests.csv'), ('notifications', 'notifications.csv')]:
        entries = []
        for variant in protocol[domain]['oneOf']:
            method = variant['properties']['method']['enum'][0]
            if domain == 'server':
                handling = 'ToolRegistry' if method == 'item/tool/call' else '自动读取主机时间' if method == 'currentTime/read' else (
                    '明确返回 -32601：无外部凭据/平台证明提供方' if method in {'attestation/generate', 'account/chatgptAuthTokens/refresh'} else '原生审批/交互 Broker')
            else:
                handling = '原生观察链路；另有功能订阅/投影/状态消费，见源码索引' if references(method) else (
                    '原生观察链路；动态/前缀订阅由所属模块处理，无独立界面动作')
            entries.append(dict(method=method, handling=handling, source_references=references(method)))
        write(filename, ['method', 'handling', 'source_references'], entries)


if __name__ == '__main__':
    export()
