"""Sync model metadata; leave the real API key in the knowledge database."""
import argparse
import json
import os
from pathlib import Path
import secrets
import socket
import subprocess
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from openclaw_settings import build_shared_model_patch, load_shared_ai_settings


INSTRUCTIONS = '''你是智教伴学的课程助手。使用已连接的智教伴学工具读取真实数据。
先获取当前账号可以访问的课程，再按课程 ID 读取资料、回答问题或查看学情。
不得编造课程、资料或统计结果；工具拒绝权限时应向用户说明，不能尝试绕过。
无需进行身份设定或运行环境初始化。回答使用中文，课程问答应调用课程问答工具。
'''


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument('--executable')
    parser.add_argument('--prefix', action='append', default=[])
    parser.add_argument('--python')
    parser.add_argument('--user-id', required=True)
    parser.add_argument('--database', required=True)
    parser.add_argument('--check-only', action='store_true')
    args = parser.parse_args()
    settings = load_shared_ai_settings(args.user_id, args.database)
    if args.check_only:
        print(f"知识库 API 配置可读取，模型：{settings['model']}")
        return 0
    if not args.executable or not args.python:
        parser.error('--executable 和 --python 是配置同步必需参数')
    project = Path(__file__).resolve().parents[1]
    config_path = Path(os.environ['OPENCLAW_CONFIG_PATH'])
    state_dir = config_path.parent
    bridge_path = state_dir / 'model-bridge.json'
    bridge = json.loads(bridge_path.read_text(encoding='utf-8')) if bridge_path.exists() else {}
    if not bridge.get('port'):
        for port in range(18889, 19110, 20):
            with socket.socket() as sock:
                try:
                    sock.bind(('127.0.0.1', port))
                except OSError:
                    continue
            bridge['port'] = port
            break
        else:
            raise RuntimeError('找不到可用的本机共享模型端口')
    bridge.update(user_id=args.user_id, database=str(Path(args.database).resolve()))
    bridge.setdefault('token', secrets.token_urlsafe(32))
    temporary = bridge_path.with_suffix('.tmp')
    temporary.write_text(json.dumps(bridge, ensure_ascii=False), encoding='utf-8')
    temporary.replace(bridge_path)
    patch = build_shared_model_patch(settings, state_dir=state_dir,
                                    bridge_url=f"http://127.0.0.1:{bridge['port']}/v1",
                                    bridge_token=bridge['token'])
    saved = json.loads(config_path.read_text(encoding='utf-8-sig')) if config_path.exists() else {}
    gateway = saved.get('gateway', {})
    patch['gateway'] = {'mode': 'local', 'bind': 'loopback'}
    if not gateway.get('port'):
        for port in range(18789, 19010, 20):
            with socket.socket() as sock:
                try:
                    sock.bind(('127.0.0.1', port))
                except OSError:
                    continue
            patch['gateway']['port'] = port
            break
        else:
            raise RuntimeError('找不到可用的本机 Gateway 端口')
    if not gateway.get('auth', {}).get('token'):
        patch['gateway']['auth'] = {'mode': 'token', 'token': secrets.token_urlsafe(32)}
    workspace = state_dir / 'workspace'
    workspace.mkdir(parents=True, exist_ok=True)
    instructions = workspace / 'AGENTS.md'
    if not instructions.exists():
        instructions.write_text(INSTRUCTIONS, encoding='utf-8')
    patch_path = state_dir / 'knowledge-model.patch.json'
    patch_path.write_text(json.dumps(patch, ensure_ascii=False, indent=2), encoding='utf-8')
    command = [args.executable, *args.prefix]
    result = subprocess.run([*command, 'config', 'patch', '--file', str(patch_path)], timeout=120)
    if result.returncode:
        return result.returncode
    print(f"OpenClaw 已复用知识库 API 分析配置，模型：{settings['model']}（每次请求读取知识库配置）。")
    return 0


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f'同步知识库 AI 配置失败：{exc}', file=sys.stderr)
        raise SystemExit(2)
