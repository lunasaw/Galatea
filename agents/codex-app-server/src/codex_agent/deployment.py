"""Render a reviewable systemd unit; this module never installs or starts services."""
from __future__ import annotations

from pathlib import Path
import re


def _path(value):
    value = str(value)
    # Avoid systemd argument expansion, specifiers, whitespace and directive injection.
    if not value.startswith('/') or not re.fullmatch(r'/[A-Za-z0-9/_.+-]+', value) or '..' in Path(value).parts:
        raise ValueError('deployment paths must be simple absolute paths')
    return value


def render_unit(config, *, executable, user, token_file, environment_file, conflicts=None):
    if not re.fullmatch(r'[a-z_][a-z0-9_-]{0,31}', user) or user == 'root':
        raise ValueError('a dedicated non-root service user is required')
    if conflicts and not re.fullmatch(r'[A-Za-z0-9_.@-]+\.service', conflicts):
        raise ValueError('invalid conflicting service name')
    executable, token, environment = map(_path, (executable, token_file, environment_file))
    root = _path(config.config_dir)
    writes = [config.codex_home, config.config_dir/'codex-process-home', config.state_dir,
              config.event_dir, config.workspace_dir, Path(config.raw['galatea']['state_root']),
              Path(config.raw['galatea']['platform']['artifact_download_root'])]
    writes = list(dict.fromkeys(_path(path) for path in writes))
    readonly = _path(config.codex_home/'config.toml')
    lines = ['[Unit]', 'Description=Galatea native Codex Agent Host',
        'After=network-online.target', 'Wants=network-online.target']
    if conflicts:
        lines += ['Conflicts='+conflicts, 'After='+conflicts]
    lines += ['', '[Service]', 'Type=simple', 'User='+user, 'Group='+user, 'WorkingDirectory='+root,
        'EnvironmentFile='+environment,
        'Environment=PYTHONNOUSERSITE=1',
        'UnsetEnvironment=PYTHONPATH PYTHONHOME PYTHONSTARTUP PYTHONUSERBASE PYTHONINSPECT',
        f'ExecStart={executable} --config {root}/agent.json serve --token-file {token}',
        'Restart=on-failure', 'RestartSec=5', 'KillMode=control-group', 'TimeoutStopSec=90',
        'UMask=0077', 'NoNewPrivileges=true', 'PrivateTmp=true', 'PrivateDevices=true',
        'ProtectSystem=strict', 'ProtectHome=true', 'ProtectProc=invisible',
        'ProtectKernelTunables=true', 'ProtectKernelModules=true', 'ProtectControlGroups=true',
        'RestrictSUIDSGID=true', 'LockPersonality=true', 'CapabilityBoundingSet=',
        *['ReadWritePaths='+path for path in writes], 'ReadOnlyPaths='+readonly,
        'StandardOutput=journal', 'StandardError=journal', '', '[Install]', 'WantedBy=multi-user.target', '']
    return '\n'.join(lines)
