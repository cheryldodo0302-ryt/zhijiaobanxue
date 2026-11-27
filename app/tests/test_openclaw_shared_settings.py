import json
from pathlib import Path
import shutil
import sys

import httpx
from fastapi.testclient import TestClient

import pytest

from auth_service import AuthService
from database import LearningDatabase
import job_secret_store
from openclaw_settings import SharedAiSettingsError, build_shared_model_patch, load_shared_ai_settings
from openclaw_model_bridge import create_model_bridge


@pytest.mark.parametrize('has_api', [True, False])
def test_binding_preflight_does_not_modify_openclaw_state(saved_knowledge_api, tmp_path, monkeypatch, has_api):
    from scripts import configure_openclaw_shared

    db, actor = saved_knowledge_api
    config = tmp_path / 'openclaw.json'
    original = b'{"existing_binding":"original-teacher"}'
    config.write_bytes(original)
    monkeypatch.setenv('OPENCLAW_CONFIG_PATH', str(config))
    monkeypatch.setattr(sys, 'argv', ['configure_openclaw_shared.py', '--check-only',
                                    '--user-id', actor['user_id'], '--database', str(db.db_path)])

    def unexpected_native_call(*args, **kwargs):
        raise AssertionError('Preflight must not run OpenClaw mutations')

    monkeypatch.setattr(configure_openclaw_shared.subprocess, 'run', unexpected_native_call)
    if has_api:
        assert configure_openclaw_shared.main() == 0
    else:
        db.execute('DELETE FROM teacher_ai_settings WHERE teacher_id=?', (actor['user_id'],))
        with pytest.raises(SharedAiSettingsError):
            configure_openclaw_shared.main()
    assert config.read_bytes() == original


@pytest.fixture
def saved_knowledge_api(tmp_path, monkeypatch):
    data = tmp_path / 'original' / 'data'
    data.mkdir(parents=True)
    monkeypatch.setattr(job_secret_store, 'KEY_PATH', data / '.job-secret.key')
    db = LearningDatabase(data / 'learning.db')
    actor = AuthService(db).create_user('teacher', 'strong-password', 'teacher')
    encrypted = job_secret_store.encrypt_job_secret('knowledge-key-unique-test-value')
    db.execute('''INSERT INTO teacher_ai_settings(teacher_id,provider,base_url,model,api_key_encrypted)
                  VALUES(?,?,?,?,?)''', (actor['user_id'], 'openai_compatible',
                                        'https://example.com/v1', 'knowledge-model', encrypted))
    yield db, actor
    db.engine.dispose()


def test_knowledge_configuration_wins_over_inherited_keys(saved_knowledge_api, monkeypatch):
    db, actor = saved_knowledge_api
    monkeypatch.setenv('DASHSCOPE_API_KEY', 'stale-environment-key')
    settings = load_shared_ai_settings(actor['user_id'], db.db_path)
    assert settings['source'] == 'knowledge_api'
    assert settings['model'] == 'knowledge-model'
    assert settings['api_key'] == 'knowledge-key-unique-test-value'
    db.execute("UPDATE teacher_ai_settings SET model='updated-model' WHERE teacher_id=?", (actor['user_id'],))
    assert load_shared_ai_settings(actor['user_id'], db.db_path)['model'] == 'updated-model'


def test_copied_database_and_key_resolve_on_a_new_path(saved_knowledge_api, tmp_path, monkeypatch):
    db, actor = saved_knowledge_api
    expected = load_shared_ai_settings(actor['user_id'], db.db_path)
    db.engine.dispose()
    copied_data = tmp_path / '新电脑 带空格' / 'app' / 'data'
    shutil.copytree(db.db_path.parent, copied_data)
    # A copied installation must use the key beside its own database, even if
    # the old installation's module-level path is still present in this process.
    monkeypatch.setattr(job_secret_store, 'KEY_PATH', tmp_path / 'wrong-machine' / 'missing.key')
    resolved = load_shared_ai_settings(actor['user_id'], copied_data / 'learning.db')
    assert resolved == expected
    patch = build_shared_model_patch(
        resolved, state_dir=copied_data.parent / '.runtime' / 'fresh',
        bridge_url='http://127.0.0.1:18889/v1', bridge_token='local-bridge-only-token',
    )
    encoded = json.dumps(patch)
    assert expected['api_key'] not in encoded
    assert str(db.db_path.parent.parent) not in encoded
    defaults = patch['agents']['defaults']
    assert defaults['model'] == defaults['utilityModel']
    assert defaults['model']['primary'] == 'zhijiao-knowledge/knowledge-model'
    provider = patch['models']['providers']['zhijiao-knowledge']
    assert provider['baseUrl'] == 'http://127.0.0.1:18889/v1'
    assert provider['apiKey'] == 'local-bridge-only-token'


def test_missing_copied_decryption_key_is_not_regenerated(saved_knowledge_api, tmp_path):
    db, actor = saved_knowledge_api
    db.engine.dispose()
    destination = tmp_path / 'missing-key' / 'learning.db'
    destination.parent.mkdir()
    shutil.copy2(db.db_path, destination)
    with pytest.raises(RuntimeError, match='job-secret.key'):
        load_shared_ai_settings(actor['user_id'], destination)
    assert not (destination.parent / '.job-secret.key').exists()


def make_bridge(tmp_path, db, actor, handler):
    path = tmp_path / 'bridge.json'
    path.write_text(json.dumps({'user_id': actor['user_id'], 'database': str(db.db_path),
                                'token': 'local-bridge-only-token'}), encoding='utf-8')
    return TestClient(create_model_bridge(path, transport=httpx.MockTransport(handler)))


def test_bridge_checks_token_and_follows_live_knowledge_model_and_key(saved_knowledge_api, tmp_path):
    db, actor = saved_knowledge_api
    captured = []

    def upstream(request):
        captured.append((request.headers.get('authorization'), json.loads(request.content)))
        return httpx.Response(200, json={'choices': [{'message': {'content': 'ok'}}]})

    client = make_bridge(tmp_path, db, actor, upstream)
    assert client.post('/v1/chat/completions', json={}).status_code == 401
    headers = {'Authorization': 'Bearer local-bridge-only-token'}
    tools = [{'type': 'function', 'function': {'name': 'check', 'parameters': {'type': 'object'}}}]
    payload = {'model': 'unrelated-client-model', 'messages': [{'role': 'user', 'content': 'hello'}],
               'tools': tools, 'tool_choice': 'auto'}
    assert client.post('/v1/chat/completions', headers=headers, json=payload).status_code == 200
    assert captured[-1][0] == 'Bearer knowledge-key-unique-test-value'
    assert captured[-1][1]['model'] == 'knowledge-model'
    assert captured[-1][1]['tools'] == tools
    assert captured[-1][1]['tool_choice'] == 'auto'
    encrypted = job_secret_store.encrypt_job_secret('updated-knowledge-key')
    db.execute('UPDATE teacher_ai_settings SET model=?,api_key_encrypted=? WHERE teacher_id=?',
               ('updated-knowledge-model', encrypted, actor['user_id']))
    assert client.post('/v1/chat/completions', headers=headers, json=payload).status_code == 200
    assert captured[-1][0] == 'Bearer updated-knowledge-key'
    assert captured[-1][1]['model'] == 'updated-knowledge-model'
    metadata = client.get('/v1/models', headers=headers).json()
    assert metadata['data'][0]['id'] == 'updated-knowledge-model'
    assert 'updated-knowledge-key' not in json.dumps(metadata)


def test_bridge_preserves_streamed_tool_calls(saved_knowledge_api, tmp_path):
    db, actor = saved_knowledge_api
    expected = b'data: {"choices":[{"delta":{"tool_calls":[{"id":"call_1","function":{"name":"check"}}]}}]}\n\ndata: [DONE]\n\n'

    class Events(httpx.AsyncByteStream):
        async def __aiter__(self):
            yield expected[:20]
            yield expected[20:]

    def upstream(request):
        assert json.loads(request.content)['stream'] is True
        return httpx.Response(200, headers={'content-type': 'text/event-stream'}, stream=Events())

    client = make_bridge(tmp_path, db, actor, upstream)
    response = client.post('/v1/chat/completions', headers={'Authorization': 'Bearer local-bridge-only-token'},
                           json={'stream': True, 'messages': []})
    assert response.status_code == 200
    assert response.headers['content-type'].startswith('text/event-stream')
    assert response.content == expected


def test_bridge_does_not_fake_streaming_success(saved_knowledge_api, tmp_path):
    db, actor = saved_knowledge_api
    client = make_bridge(tmp_path, db, actor, lambda _: httpx.Response(200, json={'choices': []}))
    response = client.post('/v1/chat/completions', headers={'Authorization': 'Bearer local-bridge-only-token'},
                           json={'stream': True, 'messages': []})
    assert response.status_code == 502
    assert response.json()['error']['type'] == 'upstream_streaming_unsupported'


def test_disabled_account_and_missing_knowledge_configuration_fail(saved_knowledge_api):
    db, actor = saved_knowledge_api
    db.execute("UPDATE users SET status='disabled' WHERE user_id=?", (actor['user_id'],))
    with pytest.raises(SharedAiSettingsError, match='账号不可用'):
        load_shared_ai_settings(actor['user_id'], db.db_path)
    db.execute("UPDATE users SET status='active' WHERE user_id=?", (actor['user_id'],))
    db.execute('DELETE FROM teacher_ai_settings WHERE teacher_id=?', (actor['user_id'],))
    with pytest.raises(SharedAiSettingsError, match='知识库'):
        load_shared_ai_settings(actor['user_id'], db.db_path)


def test_teacher_course_qa_uses_knowledge_configuration(saved_knowledge_api, monkeypatch):
    from integrations.openclaw_adapter import OpenClawAdapter
    import config

    db, actor = saved_knowledge_api
    adapter = OpenClawAdapter(db, actor)

    def ask(*args):
        settings = config.get_ai_settings()
        assert settings['model'] == 'knowledge-model'
        assert settings['api_key'] == 'knowledge-key-unique-test-value'
        assert settings['base_url'] == 'https://example.com/v1'
        return {'answer': 'shared'}

    monkeypatch.setattr(adapter.campus, 'ask', ask)
    result = adapter.invoke({'name': 'zhijiao_ask_course', 'arguments': {'course_id': 'course', 'question': '问题'}})
    assert result['answer'] == 'shared'
