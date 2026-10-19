"""Execute the bundled API image skill with settings selected in the console."""
import argparse
import json
import os
from pathlib import Path
import subprocess
import sys

if not __package__:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from codex_agent.image_generation import api_base, read_key


def run(argv=None):
    parser = argparse.ArgumentParser(description='使用设置页中保存的图像模型和 API 连接。')
    parser.add_argument('--profile', type=Path, required=True)
    parser.add_argument('--prompt', required=True)
    parser.add_argument('--out', type=Path, required=True)
    parser.add_argument('--image', action='append', default=[])
    parser.add_argument('--dry-run', action='store_true')
    args = parser.parse_args(argv)
    profile = json.loads(read_key(args.profile))
    # Web overrides take effect even if refreshing the generated skill fails.
    # In particular, an old enabled runtime file cannot bypass a later disable.
    settings_file = profile.get('settingsFile')
    if settings_file and Path(settings_file).exists():
        profile = json.loads(read_key(settings_file))
    if not profile.get('enabled'):
        raise ValueError('API 绘图尚未启用，请在设置 → 图像生成中启用。')
    script = Path(profile['script'])
    env = dict(os.environ)
    env['OPENAI_API_KEY'] = read_key(profile['keyFile']) if profile.get('keyFile') else profile['apiKey']
    if not env['OPENAI_API_KEY']:
        raise ValueError('尚未配置绘图 API Key。')
    env['OPENAI_BASE_URL'] = api_base(profile['baseUrl'])
    command = [sys.executable, str(script), 'edit' if args.image else 'generate',
               '--model', profile['model'], '--quality', profile['quality'], '--size', profile['size'],
               '--prompt', args.prompt, '--out', str(args.out)]
    for path in args.image:
        command.extend(['--image', path])
    if args.dry_run:
        command.append('--dry-run')
    result = subprocess.run(command, env=env, check=False, capture_output=True, text=True)
    # Provider error bodies can echo input. Never return the credential to the
    # model or the observation log, even if an upstream error includes it.
    for output, stream in ((result.stdout, sys.stdout), (result.stderr, sys.stderr)):
        print(output.replace(env['OPENAI_API_KEY'], '[hidden]'), end='', file=stream)
    return result.returncode


if __name__ == '__main__':
    try:
        raise SystemExit(run())
    except (ValueError, KeyError, OSError):
        print('无法使用已保存的图像配置，请检查设置页和服务端凭据文件。', file=sys.stderr)
        raise SystemExit(1)
