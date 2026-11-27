"""Register using the official CLI without shell/Windows JSON quoting losses."""
import argparse
import json
import os
from pathlib import Path
import subprocess
import sys


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument('--executable', required=True)
    parser.add_argument('--prefix', action='append', default=[])
    parser.add_argument('--python', required=True)
    parser.add_argument('--user-id', required=True)
    parser.add_argument('--database', required=True)
    args = parser.parse_args()
    project = Path(__file__).resolve().parents[1]
    definition = {
        'command': str(Path(args.python).resolve()),
        'args': [str(project / 'scripts' / 'run_openclaw_mcp.py'),
                 '--user-id', args.user_id, '--database', str(Path(args.database).resolve())],
        'cwd': str(project),
        'connectionTimeoutMs': 60000,
        'requestTimeoutMs': 180000,
    }
    config_path = os.environ.get('OPENCLAW_CONFIG_PATH')
    if config_path:
        try:
            saved = json.loads(Path(config_path).read_text(encoding='utf-8-sig'))
            current = saved.get('mcp', {}).get('servers', {}).get('zhijiao-database', {})
            if current == definition:
                # A running Gateway owns the native state lock. Repeated launches
                # need no mutation when the binding has not changed.
                print('MCP 配置已是当前绑定，无需重复写入。')
                return 0
        except (OSError, ValueError, AttributeError):
            pass
    command = [args.executable, *args.prefix]
    patch_path = Path(os.environ['OPENCLAW_STATE_DIR']) / 'mcp-registration.patch.json'
    patch_path.parent.mkdir(parents=True, exist_ok=True)
    patch_path.write_text(json.dumps({'mcp': {'servers': {'zhijiao-database': definition}}},
                                    ensure_ascii=False), encoding='utf-8')
    # config patch is routed through a running Gateway when it owns the state.
    # It also avoids shell JSON quoting and an offline-only mcp set mutation.
    result = subprocess.run([*command, 'config', 'patch', '--file', str(patch_path)], timeout=180)
    return result.returncode


if __name__ == '__main__':
    raise SystemExit(main())
