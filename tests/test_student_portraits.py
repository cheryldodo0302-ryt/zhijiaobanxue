import json
from datetime import datetime, timedelta, timezone

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from auth_service import AuthService
from browser_study_room_service import BrowserStudyRoomService
from campus_service import CampusService, PermissionDenied, ValidationError
from class_task_service import ClassTaskService, stamp
from database import LearningDatabase
from ingestion_service import IngestionService
from question_bank_service import QuestionBankService
from student_portrait_service import StudentPortraitService
from student_todo_service import StudentTodoService
from teacher_service import TeacherService
from test_reviewed_question_bank import question_workbook, XLSX_MIME


@pytest.fixture
def world(tmp_path, monkeypatch):
    monkeypatch.setenv('ZHIJIAO_STUDENT_DEFAULT_PASSWORD', 'initial-password-123')
    db = LearningDatabase(tmp_path / 'test.db')
    campus = CampusService(db, tmp_path / 'uploads', provider_factory=lambda: None)
    auth = AuthService(db, tmp_path / 'secret')
    teacher = auth.create_user('teacher', 'safe-password-123', 'teacher', '教师甲')
    other = auth.create_user('other', 'safe-password-123', 'teacher')
    teachers = TeacherService(db, campus)
    course = teachers.create_course(teacher, '数据库原理')
    term = teachers.create_term(teacher, '秋季')
    classroom = teachers.create_class(teacher, course['course_id'], term['term_id'], '一班')
    members = teachers.add_members(teacher, classroom['class_id'], ['20260001', '20260002'])['members']
    students = [{**m, 'role': 'student'} for m in members]
    bank = QuestionBankService(db, campus)
    ingestion = IngestionService(db, campus)
    bank.import_template(teacher, course['course_id'], '题库.xlsx', XLSX_MIME, question_workbook(), ai_mode='local')
    for item in ingestion.list_question_bank(teacher, course['course_id']):
        if item['answer_markdown']:
            ingestion.review_question(teacher, item['item_id'], {'status': 'approved'})
    version = ingestion.publish_question_bank(teacher, course['course_id'])
    now = [datetime(2026, 9, 1, 8, tzinfo=timezone.utc)]
    monkeypatch.setattr('class_task_service.utc_now', lambda: now[0])
    tasks = ClassTaskService(campus, lambda: now[0])
    study = BrowserStudyRoomService(tmp_path / 'study_room.db', campus=campus)
    portraits = StudentPortraitService(campus, study, lambda: now[0])
    items = tasks.sources(teacher, course['course_id'], classroom['class_id'])[0]['items']
    # Include a choice and a true/false question from the real reviewed publication.
    items = [next(q for q in items if q['question_type']=='single_choice'), next(q for q in items if q['question_type']=='true_false')]
    w = dict(db=db, campus=campus, teacher=teacher, other=other, teachers=teachers, course=course['course_id'],
        classroom=classroom['class_id'], students=students, tasks=tasks, study=study, portraits=portraits, now=now, items=items, version=version['version_id'])
    yield w
    study.engine.dispose(); db.engine.dispose()


def publish(w, kind='homework', days=1, points=(1, 1), max_submissions=None):
    return w['tasks'].publish(w['teacher'], w['course'], w['classroom'], '正式测试任务', kind, w['version'],
        stamp(w['now'][0]+timedelta(days=days)), [{'item_id':q['item_id'], 'points':p} for q,p in zip(w['items'], points)],
        1 if kind == 'exam' and max_submissions is None else max_submissions)


def responses(w):
    return {q['item_id']:json.loads(q['correct_answer_json']) for q in w['items']}


def get(w, index=0, start='2026-09-01T00:00:00+00:00', end='2026-10-01T00:00:00+00:00'):
    return w['portraits'].get(w['teacher'], w['course'], w['classroom'], w['students'][index]['user_id'], start, end)


def submit(w, task, index=0, key='first', answers=None):
    return w['tasks'].submit(w['students'][index], task['task_id'], key, responses(w) if answers is None else answers)


def test_submission_limits_and_first_exam_score(world):
    w = world
    items = [{'item_id': q['item_id'], 'points': 1} for q in w['items']]
    homework = w['tasks'].publish(w['teacher'], w['course'], w['classroom'], '一次作业',
        'homework', w['version'], stamp(w['now'][0] + timedelta(days=1)), items)
    partial = submit(w, homework, key='partial', answers={})
    assert submit(w, homework, key='partial', answers={}) == partial
    with pytest.raises(ValidationError, match='次数'):
        submit(w, homework, key='second')
    exam = publish(w, kind='exam', max_submissions=2)
    first = submit(w, exam, key='exam-1', answers={})
    second = submit(w, exam, key='exam-2')
    assert first['score'] == 0 and second['score'] == 100
    assert get(w)['metrics']['exam']['average_score'] == 0
    with pytest.raises(ValidationError, match='次数'):
        submit(w, exam, key='exam-3')


def test_private_todos_and_class_task_projection(world):
    w = world
    service = StudentTodoService(w['campus'])
    owner, another = w['students']
    todo_id = service.create(owner, '复习第一章')['todo_id']
    task = publish(w, max_submissions=2)
    items = service.list_items(owner)
    assert any(item['id'] == 'personal:' + todo_id and item['state'] == 'pending' for item in items)
    assert any(item['task_id'] == task['task_id'] and item['state'] == 'pending' for item in items)
    assert not any(item['id'] == 'personal:' + todo_id for item in service.list_items(another))
    with pytest.raises(Exception):
        service.set_completed(another, todo_id, True)
    service.set_completed(owner, todo_id, True)
    assert any(item['id'] == 'personal:' + todo_id and item['state'] == 'completed' for item in service.list_items(owner))
    submit(w, task, answers={})
    assert next(item for item in service.list_items(owner) if item['task_id'] == task['task_id'])['state'] == 'pending'
    submit(w, task, key='complete')
    assert next(item for item in service.list_items(owner) if item['task_id'] == task['task_id'])['state'] == 'completed'
    service.delete(owner, todo_id)
    assert not any(item['id'] == 'personal:' + todo_id for item in service.list_items(owner))


def test_real_publication_submit_portrait_and_immutable_snapshot(world):
    w = world; task = publish(w, points=(1, 3))
    first = submit(w, task)
    assert first['score'] == 100 and first['complete']
    assert submit(w, task) == first
    assert w['db'].fetch_one('SELECT COUNT(*) n FROM class_task_submissions')['n'] == 1
    with pytest.raises(ValidationError): submit(w, task, answers={})
    w['db'].execute("UPDATE question_bank_items SET correct_answer_json='\"B\"'")
    w['db'].execute("UPDATE question_bank_versions SET status='superseded'")
    w['now'][0] += timedelta(seconds=4)
    assert submit(w, task, key='second')['score'] == 100
    assert get(w)['tasks'][0]['rank'] == 1
    assert get(w)['metrics']['homework']['average_score'] == 100
    assert 'answer' not in w['tasks'].detail(w['students'][0], task['task_id'])['items'][0]
    assert w['tasks'].student_scopes(w['students'][0])[0]['class_id'] == w['classroom']


def test_forty_students_ties_first_complete_and_frozen_denominator(world):
    w = world
    with w['db'].connect() as conn:
        for i in range(38):
            uid = f'extra-{i}'
            conn.execute("INSERT INTO users(user_id,username,password_hash,role) VALUES(?,?,?,'student')", (uid, uid, 'unused'))
            conn.execute("INSERT INTO class_memberships(class_id,student_id,anonymous_id) VALUES(?,?,?)", (w['classroom'], uid, uid))
    task = publish(w)
    submit(w, task, answers={w['items'][0]['item_id']:responses(w)[w['items'][0]['item_id']]})
    assert get(w)['tasks'][0]['rank'] is None
    w['now'][0] += timedelta(seconds=1, microseconds=100)
    submit(w, task, key='complete')
    w['now'][0] += timedelta(microseconds=300)
    submit(w, task, index=1)
    assert get(w)['tasks'][0]['top_percent'] == 2.5
    assert get(w,1)['tasks'][0]['rank'] == 1
    w['db'].execute("UPDATE class_memberships SET status='inactive' WHERE student_id='extra-0'")
    assert get(w)['tasks'][0]['expected_count'] == 40
    w['now'][0] += timedelta(days=2)
    submit(w, task, key='late')
    detail = get(w)['tasks'][0]
    assert detail['rank'] == 1 and detail['rank_dynamic'] is False and detail['late_count']==1


def test_partial_latest_grades_late_missing_and_deadline(world):
    w = world; task = publish(w, points=(1,3))
    submit(w, task, answers={w['items'][0]['item_id']:responses(w)[w['items'][0]['item_id']]})
    assert get(w)['tasks'][0]['score']==25
    w['now'][0] += timedelta(days=1)
    submit(w, task, key='at-deadline')
    assert get(w)['metrics']['on_time_rate']==50*2
    w['now'][0] += timedelta(microseconds=1)
    submit(w, task, key='late-empty', answers={})
    assert get(w)['tasks'][0]['score']==100
    missing = get(w,1)
    assert missing['metrics']['homework']['average_score'] is None
    assert missing['metrics']['completion_rate']==0
    submit(w, task, index=1)
    late = get(w,1)
    assert late['tasks'][0]['rank'] is None and late['tasks'][0]['late_score']==100
    assert late['metrics']['homework']['count']==0 and late['metrics']['completion_rate']==100
    assert late['metrics']['on_time_rate']==0


def test_exam_one_shot_and_retry_after_close(world):
    w=world; task=publish(w,kind='exam')
    result=submit(w,task)
    with pytest.raises(ValidationError): submit(w,task,key='again')
    w['now'][0]+=timedelta(days=2)
    assert submit(w,task)==result
    with pytest.raises(ValidationError): submit(w,task,index=1)


def test_service_scope_and_private_data_isolation(world):
    w=world; task=publish(w)
    with pytest.raises(PermissionDenied):
        w['portraits'].get(w['other'],w['course'],w['classroom'],w['students'][0]['user_id'],'2026-09-01T00:00:00Z','2026-10-01T00:00:00Z')
    with pytest.raises(PermissionDenied):
        w['portraits'].get(w['students'][0],w['course'],w['classroom'],w['students'][1]['user_id'],'2026-09-01T00:00:00Z','2026-10-01T00:00:00Z')
    private=w['campus'].create_course('私人课程','personal_course',w['students'][0]['user_id'],'student')
    with pytest.raises(PermissionDenied): w['tasks'].scope(w['teacher'],private['course_id'],w['classroom'])
    with pytest.raises(PermissionDenied): w['tasks'].scope(w['teacher'],w['course'],'other-class')
    before=get(w)
    w['db'].execute("INSERT INTO learning_events(event_id,course_id,user_id,source,score,total,records_json) VALUES('private-ai',?,?,'ai_private',99,1,'[]')",(w['course'],w['students'][0]['user_id']))
    assert get(w)==before
    w['db'].execute("UPDATE class_memberships SET status='inactive' WHERE student_id=?",(w['students'][0]['user_id'],))
    with pytest.raises(PermissionDenied): submit(w,task)
    with pytest.raises(PermissionDenied): get(w)


def test_sharing_consent_revocation_regrant_and_effective_sampling(world, monkeypatch):
    w=world; room=w['study']; student=w['students'][0]; uid=student['user_id']
    room.start(uid); room.finish(uid)  # private legacy-style session
    with pytest.raises(ValidationError): room.start(uid,w['course'],w['classroom'])
    grant=room.grant(student,w['course'],w['classroom'])
    assert room.grant(student,w['course'],w['classroom'])['grant_id']==grant['grant_id']
    mono=[100.0]; monkeypatch.setattr('browser_study_room_service.time.monotonic',lambda:mono[0])
    room.start(uid,w['course'],w['classroom'])
    payload=dict(camera_available=True,face_ok=True,head_ok=True,person_ok=True)
    room.telemetry(uid,payload)
    for _ in range(30):
        mono[0]+=2; room.telemetry(uid,payload)
    mono[0]+=20; room.telemetry(uid,payload)  # disconnect excluded
    mono[0]+=1; room.telemetry(uid,dict(camera_available=False))
    mono[0]+=4; room.finish(uid)
    p=get(w)['metrics']['study']
    assert p['sessions']==1 and p['valid_sample_seconds']==60 and p['focus_reference']==100
    assert p['unobserved_seconds']==25
    with pytest.raises(PermissionDenied): room.revoke(w['students'][1],grant['grant_id'])
    room.revoke(student,grant['grant_id'])
    assert get(w)['metrics']['study']['sessions']==0
    room.grant(student,w['course'],w['classroom'])
    assert get(w)['metrics']['study']['sessions']==0
    room.start(uid,w['course'],w['classroom']); mono[0]+=3; room.finish(uid)
    assert get(w)['metrics']['study']['focus_reference'] is None
    room.clear_records(uid)
    assert get(w)['metrics']['study']['sessions']==0


def test_trends_require_three_tasks_each_period_and_normalize_scores(world):
    w=world
    for day in (2,4,6,12,14,16):
        w['now'][0]=datetime(2026,9,day,8,tzinfo=timezone.utc)
        task=publish(w,points=(10,30))
        submit(w,task,answers=responses(w) if day>10 else {w['items'][0]['item_id']:responses(w)[w['items'][0]['item_id']]})
    w['now'][0]=datetime(2026,9,20,tzinfo=timezone.utc)
    p=get(w,start='2026-09-11T00:00:00Z',end='2026-09-21T00:00:00Z')
    assert p['trends']['homework']['change']==75
    assert p['trends']['exam']['status']=='insufficient_data'
    assert p['metrics']['homework']['average_score']==100


class EvidenceProvider:
    def generate_json(self, prompt, payload):
        self.payload=json.loads(payload)
        return {k:[{'text':'依据现有记录，可继续观察学习变化。','evidence_ids':['M1']}] for k in ('overview','strengths','improvements','suggestions','limitations')}


def evaluate(w):
    return w['portraits'].evaluate(w['teacher'],w['course'],w['classroom'],w['students'][0]['user_id'],'2026-09-01T00:00:00Z','2026-10-01T00:00:00Z')


def test_ai_failure_evidence_validation_and_invalidation(world):
    w=world
    assert evaluate(w)['status']=='insufficient_data'
    task=publish(w); submit(w,task)
    assert evaluate(w)['status']=='failed'
    provider=EvidenceProvider(); w['campus'].provider_factory=lambda:provider
    result=evaluate(w)
    assert result['status']=='draft'
    assert 'student_id' not in json.dumps(provider.payload) and '正式测试任务' not in json.dumps(provider.payload)
    assert get(w)['evaluation']['status']=='draft'
    submit(w,task,key='revision',answers={})
    assert get(w)['evaluation']['status']=='stale' and get(w)['evaluation']['content'] is None
    assert evaluate(w)['status']=='draft'
    grant=w['study'].grant(w['students'][0],w['course'],w['classroom'])
    evaluate(w)
    w['study'].revoke(w['students'][0],grant['grant_id'])
    assert get(w)['evaluation']['status']=='stale'
    for bad in ('懒惰', '成绩 999 分'):
        content=provider.generate_json('',json.dumps({'evidence':{}})); content['overview'][0]['text']=bad
        with pytest.raises(ValidationError): StudentPortraitService._validate_evaluation(content,result['portrait']['evidence'])
    content=provider.generate_json('',json.dumps({'evidence':{}})); content['overview'][0]['evidence_ids']=['invented']
    with pytest.raises(ValidationError): StudentPortraitService._validate_evaluation(content,result['portrait']['evidence'])
    class TimeoutProvider:
        def generate_json(self,*args): raise TimeoutError()
    w['campus'].provider_factory=TimeoutProvider
    assert evaluate(w)['status']=='failed'


def test_http_routes_and_disabled_agent(world,monkeypatch):
    from portrait_api import portrait_router
    from agent_service import CampusAgentService
    import config
    w=world; app=FastAPI(); actor=[w['teacher']]
    def teacher():
        if actor[0]['role']!='teacher': raise HTTPException(403)
        return actor[0]
    def student():
        if actor[0]['role']!='student': raise HTTPException(403)
        return actor[0]
    app.include_router(portrait_router(w['campus'],w['study'],teacher,student))
    client=TestClient(app); base=f"/api/v1/teacher/courses/{w['course']}/classes/{w['classroom']}"
    # The adapter uses real current time, with a future deadline.
    payload=dict(title='HTTP 任务',kind='homework',version_id=w['version'],due_at=stamp(datetime.now(timezone.utc)+timedelta(days=1)),items=[{'item_id':q['item_id']} for q in w['items']])
    created=client.post(base+'/tasks',json=payload)
    assert created.status_code==200,created.text
    task_id=created.json()['task_id']; actor[0]=w['students'][0]
    assert client.get('/api/v1/student/task-scopes').status_code==200
    submitted=client.post(f'/api/v1/student/tasks/{task_id}/submissions',json={'request_id':'http-1','responses':responses(w)})
    assert submitted.status_code==200 and submitted.json()['score']==100
    assert client.get(base+'/tasks').status_code==403
    actor[0]=w['teacher']
    params={'start_at':stamp(datetime.now(timezone.utc)-timedelta(days=1)), 'end_at':stamp(datetime.now(timezone.utc)+timedelta(days=3))}
    result=client.get(base+f"/portraits/{w['students'][0]['user_id']}",params=params)
    assert result.status_code==200 and result.json()['metrics']['homework']['count']==1
    actor[0]=w['other']; assert client.get(base+'/portraits',params=params).status_code==403
    agent=CampusAgentService(w['campus'])
    request={'request_id':'agent-1','agent':'teacher_assistant','action':'student_portrait','actor':w['teacher'],
        'scope':{'course_id':w['course'],'class_id':w['classroom']},'input':{'student_id':w['students'][0]['user_id'],**params}}
    monkeypatch.setattr(config,'TEACHER_PORTAL_ENABLED',False)
    assert agent.invoke(request).status=='disabled'
    monkeypatch.setattr(config,'TEACHER_PORTAL_ENABLED',True)
    assert agent.invoke(request).status=='success'


def test_new_members_cannot_enter_old_tasks_and_cross_class_is_denied(world):
    w=world; task=publish(w)
    term=w['db'].fetch_one('SELECT term_id FROM classes WHERE class_id=?',(w['classroom'],))['term_id']
    other_class=w['teachers'].create_class(w['teacher'],w['course'],term,'二班')['class_id']
    added=w['teachers'].add_members(w['teacher'],other_class,['20260003'])['members'][0]
    actor={**added,'role':'student'}
    with pytest.raises(PermissionDenied): w['tasks'].detail(actor,task['task_id'])
    with pytest.raises(PermissionDenied):
        w['portraits'].get(w['teacher'],w['course'],other_class,w['students'][0]['user_id'],'2026-09-01T00:00:00Z','2026-10-01T00:00:00Z')
    w['teachers'].add_members(w['teacher'],w['classroom'],['20260003'])
    assert w['tasks'].list_tasks(actor,w['course'],w['classroom'])==[]
    with pytest.raises(PermissionDenied): w['tasks'].submit(actor,task['task_id'],'outsider',responses(w))
    assert get(w)['tasks'][0]['expected_count']==2


def test_concurrent_submission_idempotence_and_one_shot_exam(world):
    from concurrent.futures import ThreadPoolExecutor
    w=world; task=publish(w,kind='exam')
    with ThreadPoolExecutor(max_workers=2) as executor:
        results=list(executor.map(lambda _: submit(w,task),range(2)))
    assert results[0]==results[1]
    assert w['db'].fetch_one('SELECT COUNT(*) n FROM class_task_submissions')['n']==1


def test_validation_rejects_bad_weights_answer_options_and_time(world):
    w=world
    with pytest.raises(ValidationError): publish(w,points=(float('nan'),1))
    with pytest.raises(ValidationError): publish(w,points=(-1,1))
    task=publish(w)
    bad=responses(w); bad[w['items'][0]['item_id']]='Z'
    with pytest.raises(ValidationError): submit(w,task,answers=bad)
    with pytest.raises(ValidationError): submit(w,task,answers={'invented':'A'})
    with pytest.raises(ValidationError): get(w,start='2026-09-01')
    with pytest.raises(ValidationError): get(w,start='2026-10-02T00:00:00Z')
    assert w['db'].fetch_one('SELECT COUNT(*) n FROM class_task_submissions')['n']==0


def test_short_samples_and_scope_change_do_not_fake_focus(world,monkeypatch):
    w=world; room=w['study']; student=w['students'][0]; uid=student['user_id']
    room.grant(student,w['course'],w['classroom'])
    mono=[100.0]; monkeypatch.setattr('browser_study_room_service.time.monotonic',lambda:mono[0])
    room.start(uid)
    with pytest.raises(ValidationError): room.start(uid,w['course'],w['classroom'])
    room.finish(uid)
    room.start(uid,w['course'],w['classroom'])
    payload=dict(camera_available=True,face_ok=True,head_ok=True,person_ok=True)
    room.telemetry(uid,payload); mono[0]+=2; room.telemetry(uid,payload); room.finish(uid)
    p=get(w)['metrics']['study']
    assert p['valid_sample_seconds']==2 and p['focus_reference'] is None
    assert p['minimum_sample_seconds']==60


def test_ai_rechecks_data_and_authorization_after_generation(world):
    w=world; task=publish(w); submit(w,task)
    class ChangedProvider(EvidenceProvider):
        def generate_json(self,prompt,payload):
            submit(w,task,key='during-generation',answers={})
            return super().generate_json(prompt,payload)
    w['campus'].provider_factory=ChangedProvider
    assert evaluate(w)['status']=='stale'
    assert w['db'].fetch_one('SELECT COUNT(*) n FROM student_portrait_evaluations')['n']==0
    class RemovedProvider(EvidenceProvider):
        def generate_json(self,prompt,payload):
            w['db'].execute("UPDATE class_memberships SET status='inactive' WHERE student_id=?",(w['students'][0]['user_id'],))
            return super().generate_json(prompt,payload)
    w['campus'].provider_factory=RemovedProvider
    with pytest.raises(PermissionDenied): evaluate(w)


def test_incremental_initialization_preserves_tasks_and_records(world):
    w=world; task=publish(w); submit(w,task)
    w['db'].init_schema()
    assert get(w)['tasks'][0]['score']==100
    assert w['db'].fetch_one("SELECT COUNT(*) n FROM schema_migrations WHERE migration_id='038_student_portraits'")['n']==1


def test_grants_are_unique_across_instances_and_source_version_changes_on_regrant(world):
    from concurrent.futures import ThreadPoolExecutor
    w=world; room=w['study']; second=BrowserStudyRoomService(room.db_path,campus=w['campus'])
    try:
        with ThreadPoolExecutor(max_workers=2) as executor:
            grants=list(executor.map(lambda r: r.grant(w['students'][0],w['course'],w['classroom']),[room,second]))
        assert grants[0]['grant_id']==grants[1]['grant_id']
        assert len(room.grants(w['students'][0]))==1
        before=get(w)['evidence_version']
        room.revoke(w['students'][0],grants[0]['grant_id'])
        room.grant(w['students'][0],w['course'],w['classroom'])
        assert get(w)['evidence_version']!=before
    finally:
        second.engine.dispose()
