"""Settings for an explicitly enabled image API skill; never runs an Agent loop."""
from __future__ import annotations

import json
import importlib.util
import os
from pathlib import Path
import re
import shlex
import sys
import tempfile
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit, urlunsplit
from urllib.request import Request, HTTPRedirectHandler, build_opener

from .state import payload_digest, safe_path

DEFAULTS = {'enabled': False, 'connection': 'current', 'baseUrl': '',
            'model': 'gpt-image-2', 'quality': 'medium', 'size': '1024x1024'}
MARKER = '<!-- codex-agent:image-generation-settings -->'


def configuration_defaults(raw, path):
    """Parse non-secret deployment defaults; credentials stay in a protected file."""
    names = {'enabled': 'enabled', 'connection': 'connection', 'base_url': 'baseUrl',
             'model': 'model', 'quality': 'quality', 'size': 'size'}
    if not isinstance(raw, dict) or raw.keys() - {*names, 'api_key_file'}:
        raise ValueError('invalid image_generation fields')
    profile = {**DEFAULTS, **{names[key]: value for key, value in raw.items() if key in names}}
    validate_options(profile)
    if profile['connection'] == 'custom':
        profile['baseUrl'] = api_base(profile['baseUrl'])
        if 'api_key_file' in raw:
            profile['keyFile'] = str(path(raw['api_key_file']))
        if profile['enabled'] and not profile.get('keyFile'):
            raise ValueError('image_generation.api_key_file is required for an enabled custom connection')
    elif raw.get('api_key_file') or profile['baseUrl']:
        raise ValueError('image_generation with connection=current reuses the chat address and key')
    return profile


def validate_options(profile):
    if type(profile['enabled']) is not bool or not isinstance(profile['connection'], str) or profile['connection'] not in {'current', 'custom'}:
        raise ValueError('请选择有效的图像连接方式。')
    if not isinstance(profile['model'], str) or not re.fullmatch(r'gpt-image-[A-Za-z0-9._-]{1,80}', profile['model']):
        raise ValueError('此绘图技能使用 GPT Image 模型，请填写 gpt-image-* 模型 ID。')
    if (not isinstance(profile['quality'], str) or profile['quality'] not in {'low', 'medium', 'high', 'auto'}
            or not isinstance(profile['size'], str) or profile['size'] not in {'auto', '1024x1024', '1536x1024', '1024x1536'}):
        raise ValueError('请选择有效的图片质量和尺寸。')


def api_base(value):
    if not isinstance(value, str) or len(value) > 2048:
        raise ValueError('请输入有效的 API 地址。')
    parts = urlsplit(value.strip())
    if (parts.scheme not in {'http', 'https'} or not parts.hostname or parts.username or parts.password
            or parts.query or parts.fragment or any(ord(c) < 33 for c in value.strip())):
        raise ValueError('API 地址不能包含凭据、查询参数或片段。')
    return urlunsplit((parts.scheme, parts.netloc, parts.path.rstrip('/'), '', ''))


def private_write(path, text):
    path = Path(path)
    if any(p.is_symlink() for p in (path, *path.parents)):
        raise ValueError('配置路径不能是符号链接。')
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd, temporary = tempfile.mkstemp(dir=path.parent, prefix='.' + path.name)
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as stream:
            os.fchmod(stream.fileno(), 0o600)
            stream.write(text)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)


def read_key(path):
    path = Path(path)
    if (any(p.is_symlink() for p in (path, *path.parents)) or not path.is_file()
            or path.stat().st_mode & 0o077):
        raise ValueError('当前聊天的密钥文件不可用，或文件权限不是仅所有者可读写。')
    return path.read_text().strip()


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def request_json(base, key, path, body=None):
    request = Request(base + path, data=None if body is None else json.dumps(body).encode(),
                      headers={'Authorization': 'Bearer ' + key, 'Content-Type': 'application/json'})
    try:
        with build_opener(NoRedirect).open(request, timeout=15) as response:
            data = response.read(2 * 1024 * 1024 + 1)
            if len(data) > 2 * 1024 * 1024:
                return 502, {}
            return response.status, json.loads(data)
    except HTTPError as exc:
        try:
            data = json.loads(exc.read(65536))
        except (ValueError, OSError):
            data = {}
        return exc.code, data
    except (URLError, TimeoutError, OSError, ValueError):
        return 0, {}


def response_status(status, body):
    error = body.get('error', {}) if isinstance(body, dict) else {}
    message = error.get('message', '') if isinstance(error, dict) else str(error)
    if not isinstance(message, str):
        message = str(message)
    if status == 403 and 'not enabled for this group' in message.lower():
        return {'status': 'blocked', 'message': '当前 API Key 所属分组未开启图像生成。请在网关为该分组启用绘图权限。', 'httpStatus': status}
    messages = {0: '无法连接图像服务，请检查地址和网络。', 401: 'API Key 无效或已过期。',
                403: '图像服务拒绝访问，请检查 Key 的模型和绘图权限。',
                404: '此连接未提供 Images API，请检查服务地址与网关路由。',
                429: '服务额度或请求频率受限，请在服务商控制台检查。'}
    return {'status': 'blocked', 'message': messages.get(status, f'图像服务返回 HTTP {status}，尚未确认可用。'), 'httpStatus': status}


class ImageGenerationSettings:
    def __init__(self, config, *, editable=False):
        self.config, self.editable = config, editable
        self.path = safe_path(config.state_dir, 'console/image-generation.json')
        self.runtime_path = safe_path(config.state_dir, 'console/image-generation-runtime.json')
        self.defaults = getattr(config, 'image_generation', None) or dict(DEFAULTS)
        self.skill = safe_path(config.codex_home, 'skills/imagegen-api/SKILL.md')
        self.script = safe_path(config.codex_home, 'skills/.system/imagegen/scripts/image_gen.py')

    def read(self):
        if not self.path.exists():
            return dict(self.defaults)
        return {**DEFAULTS, **json.loads(read_key(self.path))}

    def current_connection(self, native_config):
        provider_id = native_config.get('model_provider') or 'openai'
        provider = (native_config.get('model_providers') or {}).get(provider_id, {})
        base = provider.get('base_url')
        if not base and provider_id == 'openai':
            base = native_config.get('openai_base_url') or 'https://api.openai.com/v1'
        key_file = self.config.raw.get('codex', {}).get('model_api_key_file')
        # Only reuse the exact credential source that this Host injects. Never
        # send a chat credential to an independently entered endpoint.
        supported = provider_id == 'openai' and not provider.get('env_key') and not provider.get('auth')
        # A malformed native provider URL must not make the whole settings
        # page unreadable. The independent connection form can still repair
        # the image setup, and save will report the native URL problem when
        # the user explicitly chooses the current connection.
        try:
            normalized = api_base(base) if base else ''
        except ValueError:
            normalized = ''
        return {'baseUrl': normalized, 'keyFile': key_file if supported else None}

    def public(self, native_config):
        saved = self.read()
        current = self.current_connection(native_config)
        return {**{k: saved.get(k, v) for k, v in DEFAULTS.items()},
                'revision': payload_digest(saved), 'editable': self.editable,
                'keyConfigured': saved['connection'] == 'custom' and bool(saved.get('apiKey') or saved.get('keyFile')),
                'currentBaseUrl': current['baseUrl'], 'currentKeyConfigured': bool(current['keyFile']),
                'scriptAvailable': self.script.is_file(),
                'sdkAvailable': importlib.util.find_spec('openai') is not None,
                'skillPath': str(self.skill) if saved['enabled'] else None}

    def prepare(self, body, native_config):
        if not isinstance(body, dict) or set(body) - {*DEFAULTS, 'apiKey', 'revision'}:
            raise ValueError('图像配置包含未知字段。')
        old = self.read()
        if body.get('revision') != payload_digest(old):
            raise ValueError('图像设置已在其他页面修改，请重新读取后再保存。')
        profile = {key: body.get(key, value) for key, value in DEFAULTS.items()}
        validate_options(profile)
        if profile['connection'] == 'current':
            profile.update(self.current_connection(native_config))
            if profile['enabled'] and not profile['baseUrl']:
                raise ValueError('默认聊天服务的 API 地址不可用于绘图，请使用独立连接。')
            if profile['enabled'] and not profile.get('keyFile'):
                raise ValueError('当前聊天没有可复用的 API Key 文件，请使用独立连接。')
        else:
            profile['baseUrl'] = api_base(profile['baseUrl'])
            key = body.get('apiKey', '')
            if not isinstance(key, str) or len(key) > 16384 or any(ord(c) < 32 for c in key):
                raise ValueError('API Key 格式无效。')
            same_connection = old.get('connection') == 'custom' and old.get('baseUrl') == profile['baseUrl']
            if not key and same_connection and old.get('keyFile'):
                profile['keyFile'] = old['keyFile']
                return profile
            if not key and same_connection:
                key = old.get('apiKey', '')
            if not key.strip():
                raise ValueError('请输入此 API 地址对应的 Key；更换地址后必须重新填写。')
            profile['apiKey'] = key.strip()
        return profile

    def credential(self, profile):
        key = read_key(profile['keyFile']) if profile.get('keyFile') else profile.get('apiKey', '')
        if not key:
            raise ValueError('尚未配置绘图 API Key。')
        return key

    def save(self, body, native_config):
        if not self.editable:
            raise ValueError('此发布实例的图像设置为只读。')
        profile = self.prepare(body, native_config)
        profile['script'] = str(self.script)
        self.validate_runtime(profile)
        private_write(self.path, json.dumps(profile, ensure_ascii=False))
        self.activate(profile)
        return self.public(native_config)

    def initialize(self, native_config):
        """Restore skill discovery on startup, including config-only deployments."""
        profile = self.read()
        if profile['connection'] == 'current' and not self.path.exists():
            profile.update(self.current_connection(native_config))
        self.validate_runtime(profile)
        changed = profile['enabled'] or self.skill.exists()
        self.activate(profile)
        return changed

    def validate_runtime(self, profile):
        if profile['enabled']:
            api_base(profile['baseUrl'])
            self.credential(profile)
            if not self.script.is_file():
                raise ValueError('当前实例尚未安装 imagegen 技能的 API 脚本。')
            if importlib.util.find_spec('openai') is None:
                raise ValueError('服务端缺少绘图依赖，请更新安装本项目依赖后再启用。')
        if self.skill.exists() and MARKER not in self.skill.read_text():
            raise ValueError('已有同名 imagegen-api 技能，请先处理名称冲突。')

    def activate(self, profile):
        # A native shell has a minimal environment, with neither this package
        # nor its interpreter necessarily on PATH/PYTHONPATH.
        command = shlex.join([sys.executable, str(Path(__file__).with_name('image_generation_cli.py')),
                              '--profile', str(self.runtime_path)])
        text = f'''---
name: imagegen-api
description: Generate or edit raster images using the API connection and model explicitly enabled in the Web image settings. Use for drawing, illustrations and image editing; not for code-native charts or diagrams.
---
{MARKER}

The user enabled API image generation in the instance configuration or Settings → 图像生成. Use this configured API path for image requests; do not require the unavailable built-in image_gen tool or ask the user to select API fallback again. This authorizes the chosen execution path, not unsolicited images.

Run `{command} --help` for supported arguments. Generate with `--prompt "..." --out output/imagegen/name.png`; add repeated `--image path` arguments to edit. The runner loads the saved model, endpoint and credential without printing secrets. Never read or print the profile/key file, or put a key in command arguments. Saving settings does not itself generate images.

Use a new output filename; preserve requested content and style. Return the resulting image with an absolute file path. On permission, unsupported endpoint or quota errors, report the error and stop; do not switch models or providers silently. Native sandbox/network approvals still apply. If settings are disabled, the runner refuses generation.
'''
        # The runner checks enabled state, so a stale skill can never keep API
        # generation enabled after its profile is disabled.
        private_write(self.runtime_path, json.dumps({**profile, 'script': str(self.script),
                                                   'settingsFile': str(self.path)}, ensure_ascii=False))
        if profile['enabled']:
            private_write(self.skill, text)
        elif self.skill.exists():
            self.skill.unlink()

    def check(self, body, native_config):
        profile = self.prepare(body, native_config)
        key = self.credential(profile)
        base = api_base(profile['baseUrl'])
        status, data = request_json(base, key, '/models')
        # Some image gateways route Images API without exposing a model list.
        # The explicitly entered model remains usable in that case.
        model_list_available = status == 200
        if status not in {200, 404, 405, 501}:
            return {**response_status(status, data), 'models': []}
        if model_list_available and (not isinstance(data, dict) or not isinstance(data.get('data'), list)):
            return {**response_status(502, {}), 'models': []}
        rows = data['data'] if model_list_available else []
        models = sorted({row['id'] for row in rows if isinstance(row, dict)
                         and isinstance(row.get('id'), str) and row['id'].startswith('gpt-image-')})
        # No prompt: this can expose a gateway permission denial, but can never
        # prove a successful image generation. Do not label validation as ready.
        status, data = request_json(base, key, '/images/generations', {'model': profile['model'], 'n': 1, 'prompt': ''})
        if status not in {200, 400, 422}:
            return {**response_status(status, data), 'models': models}
        prefix = '模型列表已读取。' if model_list_available else '此网关未提供模型列表，请手动填写绘图模型。'
        return {'status': 'unverified', 'models': models, 'httpStatus': status,
                'message': prefix + '图像接口已响应；实际出图权限和额度仍需首次绘图确认。'}
