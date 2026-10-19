#!/usr/bin/env python3
"""Vendor the upstream configuration schema and its serde-flatten map types."""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess


def export(source: Path, output: Path):
    raw = (source / 'codex-rs/core/config.schema.json').read_bytes()
    schema = json.loads(raw)
    # schemars omits these flattened maps. Their value types are declared in
    # codex-rs/config/src/permissions_toml.rs, not invented client settings.
    maps = {'PermissionsToml': 'PermissionProfileToml',
            'FilesystemPermissionsToml': 'FilesystemPermissionToml',
            'NetworkDomainPermissionsToml': 'NetworkDomainPermissionToml',
            'NetworkUnixSocketPermissionsToml': 'NetworkUnixSocketPermissionToml'}
    for name, target in maps.items():
        schema['definitions'][name]['additionalProperties'] = {'$ref': '#/definitions/' + target}
    schema['definitions']['WorkspaceRootsToml']['additionalProperties'] = {'type': 'boolean'}
    for name in ('profile', 'profiles'):
        schema['properties'][name]['deprecated'] = True
        schema['properties'][name]['x-native-write-status'] = 'legacy-read-only'
        schema['properties'][name]['description'] = (
            '0.163 已停止支持旧 profile 配置的写入。CLI 使用 --profile <name> 和 '
            '<name>.config.toml；当前 app-server 启动命令不支持 --profile。'
            '此处可查看或删除旧配置，不能创建或修改旧配置集。')
    schema['x-source'] = {
        'revision': subprocess.check_output(['git', '-C', str(source), 'rev-parse', 'HEAD'], text=True).strip(),
        'sha256': hashlib.sha256(raw).hexdigest(),
        'path': 'codex-rs/core/config.schema.json',
        'supplements': ['codex-rs/config/src/permissions_toml.rs',
                        'codex-rs/app-server/src/config_manager_service.rs',
                        'codex-rs/cli/src/main.rs'],
    }
    output.write_text(json.dumps(schema, ensure_ascii=False, separators=(',', ':')) + '\n')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, required=True)
    parser.add_argument('--output', type=Path, default=Path(__file__).resolve().parents[1] / 'src/codex_agent/protocol/config.schema.json')
    args = parser.parse_args()
    export(args.source, args.output)
