import pytest

from campus_service import PermissionDenied
from ingestion_service import IngestionService
from test_guided_qa import guided_campus


def test_guidance_preserves_document_and_page_on_initial_and_hint_turns(tmp_path):
    service, course, _ = guided_campus(tmp_path)
    document = service.list_documents(course['course_id'], 'student_1', 'student')[0]
    service.db.execute('UPDATE document_chunks SET page_number=3 WHERE document_id=?',
                       (document['document_id'],))
    result = service.ask(course['course_id'], 'student_1', 'student',
                         '关系数据库规范化为什么能够减少数据冗余？', intent='start')
    assert not result['completed']
    assert result['sources'][0]['document_id'] == document['document_id']
    assert result['sources'][0]['page_number'] == 3
    hint = service.ask(course['course_id'], 'student_1', 'student',
                       '关系数据库规范化为什么能够减少数据冗余？', intent='hint',
                       session_id=result['session_id'])
    assert hint['sources'][0]['document_id'] == document['document_id']
    assert hint['sources'][0]['page_number'] == 3


def test_published_evidence_keeps_each_file_page_pair_without_guessing(tmp_path, monkeypatch):
    service, course, _ = guided_campus(tmp_path)
    refs = [{'document_id': 'doc-a', 'original_name': '教材.pdf', 'page_number': None},
            {'document_id': 'doc-b', 'original_name': '教材.pdf', 'page_number': 7}]
    point = {'node_type': 'knowledge_point', '_allowed': True, 'material_type': 'textbook',
             'markdown': '规范化减少数据冗余', 'section': '第二节 规范化',
             'original_name': '教材.pdf', 'page_number': 7, 'sources': refs}
    monkeypatch.setattr('published_knowledge.publication', lambda *args: ({'version_id': 'v1'}, [point], []))
    source = service._retriever(course['course_id']).search('规范化减少数据冗余')[0].to_dict()
    assert source['document_id'] == 'doc-a'
    assert source['page_number'] is None  # Never borrow doc-b's page for doc-a.
    assert [(item['document_id'], item['page_number']) for item in source['locations']] == [
        ('doc-a', None), ('doc-b', 7)]


def test_personal_source_preview_is_owner_only(tmp_path):
    service, course, _ = guided_campus(tmp_path)
    document = service.list_documents(course['course_id'], 'student_1', 'student')[0]
    ingestion = IngestionService(service.db, service)
    assert ingestion.preview_descriptor({'user_id': 'student_1', 'role': 'student'},
                                         document['document_id'])['preview_kind'] == 'text'
    for actor in [{'user_id': 'student_2', 'role': 'student'},
                  {'user_id': 'teacher_1', 'role': 'teacher'}]:
        with pytest.raises(PermissionDenied):
            ingestion.preview_file(actor, document['document_id'])
