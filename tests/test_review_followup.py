import json
from concurrent.futures import ThreadPoolExecutor

import pytest

from assessment_store import AssessmentStore
from campus_service import CampusService, ValidationError
from database import LearningDatabase
from published_knowledge import capture_publication, visible_nodes


def test_teacher_deployment_setting_reads_server_file_with_environment_priority(tmp_path, monkeypatch):
    import config

    settings = tmp_path / 'server.env'
    settings.write_text('ZHIJIAO_TEACHER_AGENT_ENABLED=1\n', encoding='utf-8')
    monkeypatch.setattr(config, 'SERVER_ENV', settings)
    monkeypatch.delenv('ZHIJIAO_TEACHER_AGENT_ENABLED', raising=False)
    assert config.get_runtime_setting('ZHIJIAO_TEACHER_AGENT_ENABLED', '0') == '1'
    monkeypatch.setenv('ZHIJIAO_TEACHER_AGENT_ENABLED', '0')
    assert config.get_runtime_setting('ZHIJIAO_TEACHER_AGENT_ENABLED', '1') == '0'


@pytest.fixture
def system(tmp_path):
    db = LearningDatabase(tmp_path / 'review.db')
    campus = CampusService(db, tmp_path / 'uploads')
    course = campus.create_course('测试课', 'shared_course', 'teacher', 'teacher', visibility='public')
    yield db, campus, course['course_id']
    db.engine.dispose()


def test_snapshot_survives_draft_edit_and_honors_class_scope(system):
    db, _, course = system
    with db.connect() as conn:
        conn.execute("INSERT INTO terms(term_id,term_name,owner_id) VALUES('term','学期','teacher')")
        conn.execute("INSERT INTO classes(class_id,course_id,term_id,class_name,teacher_id) VALUES('a',?,'term','A','teacher')", (course,))
        conn.execute("INSERT INTO class_memberships(class_id,student_id,anonymous_id) VALUES('a','student','anon')")
        conn.execute("""INSERT INTO knowledge_nodes(node_id,course_id,node_scope,node_type,title,markdown,status)
            VALUES('point',?,'course','knowledge_point','知识','已发布原文','approved')""", (course,))
        conn.execute("INSERT INTO knowledge_node_class_scopes(node_id,class_id) VALUES('point','a')")
        conn.execute("INSERT INTO knowledge_versions(version_id,course_id,version_number,status,created_by) VALUES('v1',?,1,'published','teacher')", (course,))
        conn.execute("INSERT INTO knowledge_version_nodes VALUES('v1','point')")
        capture_publication(conn, 'v1')
    db.execute("UPDATE knowledge_nodes SET markdown='未发布新稿',status='draft' WHERE node_id='point'")
    assert visible_nodes(db, course, 'student')[1][0]['markdown'] == '已发布原文'
    assert visible_nodes(db, course, 'outsider')[1] == []
    with db.connect() as conn:
        capture_publication(conn, 'v1')
    assert visible_nodes(db, course, 'student')[1][0]['markdown'] == '已发布原文'


def test_paper_ownership_and_atomic_duplicate_submission(system):
    db, _, course = system
    store = AssessmentStore(db)
    paper_id = store.create(course, 'student', 'course', [{'answer':'A'}])
    with pytest.raises(ValidationError):
        store.load(paper_id, course, 'other', 'course')
    paper = store.load(paper_id, course, 'student', 'course')
    saves = []
    def submit(_):
        def save(conn):
            saves.append(1)
            return {'score':100}
        return store.finish(paper, ['A'], save)
    with ThreadPoolExecutor(max_workers=3) as pool:
        assert list(pool.map(submit, range(3))) == [{'score':100}] * 3
    assert len(saves) == 1
    with pytest.raises(ValidationError):
        store.finish(paper, ['B'], lambda _: {})


def test_existing_database_reopens_without_reapplying_schema(system):
    db, _, course = system
    again = LearningDatabase(db.db_path)
    assert again.fetch_one('SELECT course_id FROM courses WHERE course_id=?', (course,))
    assert again.fetch_one('PRAGMA integrity_check')['integrity_check'] == 'ok'
    again.engine.dispose()


def test_course_grading_ignores_forged_answers_and_replays_once(system):
    db, campus, course = system
    qid = db.execute("""INSERT INTO course_questions(course_id,user_id,question,answer,knowledge_points_json)
        VALUES(?,'student','监督学习是什么','监督学习使用带标签样本训练模型。','["监督学习"]')""", (course,))
    quiz = campus.generate_quiz(course,'student','student',qid)
    forged = [{**q,'answer':'forged'} for q in quiz['items']]
    answers = ['forged'] * len(forged)
    grade = campus.submit_quiz(course,'student','student',qid,forged,answers)
    assert grade['score'] < 100
    assert campus.submit_quiz(course,'student','student',qid,forged,answers) == grade
    assert db.fetch_one('SELECT COUNT(*) n FROM course_attempts')['n'] == 1
    with pytest.raises(ValidationError):
        campus.submit_quiz(course,'other','student',qid,forged,answers)
    assert campus.profile(course,'student','student')['weak_points']


def test_class_statistics_use_event_time_membership_and_percent_scores(system):
    from learning_events import record
    db, campus, course = system
    with db.connect() as conn:
        conn.execute("INSERT INTO terms(term_id,term_name,owner_id) VALUES('term','学期','teacher')")
        for cls in ['a','b']:
            conn.execute("INSERT INTO classes(class_id,course_id,term_id,class_name,teacher_id) VALUES(?,?,'term',?,'teacher')", (cls,course,cls))
        conn.execute("INSERT INTO class_memberships(class_id,student_id,anonymous_id) VALUES('a','student','anon')")
        record(conn,'course',1,course,'student',50,2,[{'knowledge_points':['关系'],'correct':False}])
        record(conn,'published','one',course,'student',100,4,[{'knowledge_points':['关系'],'correct':True}])
        conn.execute("UPDATE class_memberships SET class_id='b' WHERE student_id='student'")
        record(conn,'ai',1,course,'student',75,4,[])
    a = campus.class_analysis(course,'teacher','a')
    b = campus.class_analysis(course,'teacher','b')
    assert a['quiz_count']==2 and a['average_score']==75
    assert a['score_buckets']['0-59']==1 and a['score_buckets']['90-100']==1
    assert b['quiz_count']==1 and b['average_score']==75
    assert 'user_id' not in json.dumps(a) and 'student_id' not in json.dumps(a)
    assert campus.class_analysis(course,'teacher')['quiz_count']==3


def test_password_change_and_logout_revoke_access_and_preview(system,tmp_path):
    from auth_service import AuthService
    from campus_service import PermissionDenied
    db, _, _ = system
    auth = AuthService(db,tmp_path/'secret')
    user = auth.create_user('user','initial-password','student')
    _, access, refresh = auth.login('user','initial-password')
    preview = auth.issue_document_token(user,'doc')
    updated, next_access, next_refresh = auth.change_password(user,'initial-password','new-password-123')
    for token,kind in [(access,'access'),(preview,'document_source'),(refresh,'refresh')]:
        with pytest.raises(PermissionDenied):
            auth.decode(token,kind)
    auth.revoke(next_refresh)
    with pytest.raises(PermissionDenied):
        auth.authenticate(next_access)


def test_explicit_upgrade_freezes_legacy_once_without_changing_learning(system):
    from scripts.upgrade_review_data import upgrade
    db, _, course = system
    with db.connect() as conn:
        conn.execute("INSERT INTO knowledge_nodes(node_id,course_id,node_scope,node_type,title,markdown,status) VALUES('legacy',?,'course','knowledge_point','旧知识','旧正文','approved')", (course,))
        conn.execute("INSERT INTO knowledge_versions(version_id,course_id,version_number,status,created_by) VALUES('legacy-v',?,1,'published','teacher')", (course,))
        conn.execute("INSERT INTO knowledge_version_nodes VALUES('legacy-v','legacy')")
    before = db.fetch_all('SELECT * FROM courses')
    assert upgrade(db.db_path)['published_knowledge_items']==1
    db.execute("UPDATE knowledge_nodes SET markdown='新草稿' WHERE node_id='legacy'")
    upgrade(db.db_path)
    assert visible_nodes(db,course,'student')[1][0]['markdown']=='旧正文'
    assert db.fetch_all('SELECT * FROM courses')==before


def test_withdrawal_is_owner_bound_and_only_changes_the_selected_version(system):
    from published_knowledge import withdraw_version
    from campus_service import PermissionDenied
    db,campus,course = system
    other = campus.create_course('其他课','shared_course','other_teacher','teacher')['course_id']
    with db.connect() as conn:
        conn.execute("INSERT INTO knowledge_versions(version_id,course_id,version_number,status,created_by) VALUES('one',?,1,'published','teacher')", (course,))
        conn.execute("INSERT INTO knowledge_versions(version_id,course_id,version_number,status,created_by) VALUES('other',?,1,'published','other_teacher')", (other,))
    with pytest.raises(PermissionDenied):
        withdraw_version(campus,{'user_id':'student','role':'student'},course,'one')
    with pytest.raises(PermissionDenied):
        withdraw_version(campus,{'user_id':'other_teacher','role':'teacher'},course,'one')
    with pytest.raises(ValidationError):
        withdraw_version(campus,{'user_id':'teacher','role':'teacher'},course,'other')
    assert withdraw_version(campus,{'user_id':'teacher','role':'teacher'},course,'one')['withdrawn']
    assert not withdraw_version(campus,{'user_id':'teacher','role':'teacher'},course,'one')['withdrawn']
    assert db.fetch_one("SELECT status FROM knowledge_versions WHERE version_id='other'")['status']=='published'


def test_student_imported_private_questions_stay_out_of_teacher_statistics(system):
    db,campus,course = system
    db.execute("INSERT INTO ai_practice_attempts(course_id,user_id,questions_json,result_json,score) VALUES(?,'student',?,?,50)",
        (course,json.dumps([{'question':'私人题目','knowledge_point':'私人笔记','source_file':'private.txt'}]),
         json.dumps({'results':[{'index':1,'correct':False}]})))
    assert campus.class_analysis(course,'teacher')['quiz_count']==0
    assert len(campus.profile(course,'student','student')['attempts'])==1
