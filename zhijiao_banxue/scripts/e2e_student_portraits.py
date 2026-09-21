"""Real local HTTP + optional Chrome UI acceptance; always uses isolated demo data.

Usage: .venv/Scripts/python.exe scripts/e2e_student_portraits.py
       --browser-tools <temporary npm prefix containing node_modules/playwright>
No real student records or production services are touched.
"""
from __future__ import annotations

import argparse
import json
import os
import socket
import subprocess
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from io import BytesIO
from pathlib import Path

import requests
from openpyxl import Workbook

from e2e_smoke import ROOT, credentials, stop, wait_port


def free_port():
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        return sock.getsockname()[1]


def question_bytes():
    book = Workbook(); sheet = book.active
    sheet.append(['题目类型', '答案类型', '题干内容', '答案', '知识点', '选项A', '选项B', '选项C', '选项D'])
    sheet.append(['选择题', '单选题', 'SQL 查询的核心关键字是？', 'A', 'SQL', 'SELECT', 'UPDATE', 'DELETE', 'INSERT'])
    sheet.append(['判断题', '判断题', '关系模型中的元组可以重复。', '错', '关系模型', None, None, None, None])
    sheet.append(['多选题', '多选题', '下列哪些属于关系操作？', 'A,C', '关系运算', '选择', '压缩', '投影', '加密'])
    out = BytesIO(); book.save(out); book.close()
    return out.getvalue()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--browser-tools', type=Path)
    args = parser.parse_args()
    data_dir = Path(tempfile.mkdtemp(prefix='zhijiao-portrait-acceptance-'))
    api_port, web_port = free_port(), free_port()
    env = {**os.environ, 'ZHIJIAO_DATA_DIR': str(data_dir), 'ZHIJIAO_AI_MODE': 'mock', 'ZHIJIAO_AI_PROVIDER': 'mock',
           'ZHIJIAO_TEACHER_AGENT_ENABLED': '1', 'NO_PROXY': '127.0.0.1,localhost', 'no_proxy': '127.0.0.1,localhost', 'PYTHONUTF8': '1'}
    env['PYTHONPATH'] = os.pathsep.join([str(ROOT), env.get('PYTHONPATH', '')])
    subprocess.run([sys.executable, str(ROOT/'scripts/bootstrap_demo.py'), '--if-empty'], env=env, check=True)
    teacher_password, student_password = credentials(data_dir/'demo_credentials.txt')
    logs = [(data_dir/name).open('w', encoding='utf-8') for name in ('api.log','web.log')]
    processes = []
    flags = subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0
    try:
        processes.append(subprocess.Popen([sys.executable, '-m', 'uvicorn', 'api:app', '--host', '127.0.0.1', '--port', str(api_port)], cwd=ROOT, env=env, stdout=logs[0], stderr=subprocess.STDOUT, creationflags=flags))
        processes.append(subprocess.Popen(['npm.cmd' if os.name=='nt' else 'npm','run','dev','--','--host','127.0.0.1','--port',str(web_port),'--strictPort'], cwd=ROOT/'web', env={**env, 'VITE_API_PROXY_TARGET':f'http://127.0.0.1:{api_port}'}, stdout=logs[1], stderr=subprocess.STDOUT, creationflags=flags))
        wait_port(api_port); wait_port(web_port)
        base = f'http://127.0.0.1:{api_port}/api/v1'
        def login(name, password):
            session = requests.Session(); session.trust_env = False
            result = session.post(base+'/auth/login', json={'username':name,'password':password}, timeout=10)
            result.raise_for_status(); session.headers['Authorization'] = 'Bearer '+result.json()['access_token']
            return session
        teacher = login('demo_teacher', teacher_password)
        student = login('demo_student', student_password)
        course, classroom = 'virtual_ai_101', 'class_demo_2026'
        imported = teacher.post(f'{base}/teacher/courses/{course}/question-bank/import', files={'file':('portrait.xlsx',question_bytes(),'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')}, data={'ai_mode':'local'}, timeout=30)
        imported.raise_for_status()
        items = teacher.get(f'{base}/teacher/courses/{course}/question-bank',timeout=10).json()
        assert len(items)==3,items
        for item in items:
            response = teacher.patch(f"{base}/teacher/question-bank/{item['item_id']}",json={'status':'approved'},timeout=10)
            response.raise_for_status()
        response = teacher.post(f'{base}/teacher/courses/{course}/question-bank/publish',timeout=10)
        response.raise_for_status()
        version = response.json()['version_id']
        prefix=f'{base}/teacher/courses/{course}/classes/{classroom}'
        now=datetime.now(timezone.utc)
        task=teacher.post(prefix+'/tasks',json={'title':'HTTP 验收作业','kind':'homework','version_id':version,'due_at':(now+timedelta(minutes=20)).isoformat(),'items':[{'item_id':q['item_id'],'points':2} for q in items]},timeout=10)
        task.raise_for_status(); task=task.json()
        answers={q['item_id']:q['correct_answer'] for q in items}
        submitted=student.post(f"{base}/student/tasks/{task['task_id']}/submissions",json={'request_id':'http-acceptance','responses':answers},timeout=10)
        submitted.raise_for_status(); assert submitted.json()['score']==100
        period={'start_at':(now-timedelta(days=30)).isoformat(),'end_at':(now+timedelta(days=1)).isoformat()}
        result=teacher.get(prefix+'/portraits/demo_student_001',params=period,timeout=10)
        result.raise_for_status(); portrait=result.json()
        assert portrait['tasks'][0]['rank']==1 and portrait['metrics']['homework']['average_score']==100
        evaluated=teacher.post(prefix+'/portraits/demo_student_001/evaluate',json=period,timeout=10)
        evaluated.raise_for_status(); assert evaluated.json()['status']=='failed'  # No fake AI in offline mode.
        assert student.get(prefix+'/portraits/demo_student_001',params=period,timeout=10).status_code==403
        if args.browser_tools:
            config={'url':f'http://127.0.0.1:{web_port}', 'teacherPassword':teacher_password,'studentPassword':student_password,
                    'output':str(data_dir),'playwright':str(args.browser_tools.resolve()/'node_modules/playwright'), 'due':(datetime.now()+timedelta(minutes=30)).strftime('%Y-%m-%d %H:%M:%S')}
            config_path=data_dir/'browser-config.json'; config_path.write_text(json.dumps(config),encoding='utf-8')
            browser_result = subprocess.run(['node',str(ROOT/'scripts/student_portraits_browser.cjs'),str(config_path)],env=env,capture_output=True,text=True,encoding='utf-8',errors='replace',creationflags=flags)
            (data_dir/'browser.log').write_text(browser_result.stdout+'\n'+browser_result.stderr,encoding='utf-8')
            print(browser_result.stdout)
            if browser_result.returncode:
                print(browser_result.stderr)
                browser_result.check_returncode()
        print('PORTRAIT E2E PASS: publication, full submission, scoped portrait, real grade, offline AI failure, teacher-only access')
        print(f'Acceptance artifacts: {data_dir}')
        return 0
    finally:
        for process in reversed(processes): stop(process)
        for log in logs: log.close()


if __name__ == '__main__':
    raise SystemExit(main())
