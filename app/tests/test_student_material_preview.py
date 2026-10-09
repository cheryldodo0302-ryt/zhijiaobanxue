from io import BytesIO

import pytest

from auth_service import AuthService
from campus_service import CampusService, PermissionDenied
from database import LearningDatabase
from ingestion_service import IngestionService
from published_knowledge import withdraw_version
from teacher_service import TeacherService
from skills.memory.service import MemoryLearningSkill


def test_source_preview_requires_enrollment_publication_and_explicit_visibility(tmp_path):
    db = LearningDatabase(tmp_path / 'preview.db')
    campus = CampusService(db, tmp_path / 'uploads', provider_factory=lambda: None)
    auth = AuthService(db, tmp_path / 'secret')
    teacher = auth.create_user('teacher', 'safe-password-123', 'teacher')
    student = auth.create_user('student', 'safe-password-123', 'student')
    outsider = auth.create_user('outsider', 'safe-password-123', 'student')
    course = TeacherService(db, campus).create_course(teacher, '网页资料预览')
    campus.enroll_student(course['course_id'], teacher['user_id'], student['user_id'])
    service = IngestionService(db, campus)
    job = service.queue_document_stream(teacher, course['course_id'], 'source.txt',
                                        'text/plain', BytesIO('数据库事务'.encode()))
    doc_id = job['document_id']
    service.process_job(job['job_id'])
    service.set_student_file_visibility(teacher, doc_id, True)
    assert service.list_student_source_files(student, course['course_id']) == []
    assert MemoryLearningSkill(campus).student_dashboard(student['user_id'], course['course_id'])['document_count'] == 0
    with pytest.raises(PermissionDenied):
        service.preview_descriptor(student, doc_id)
    block = service.list_blocks(teacher, doc_id)[0]
    service.review_block(teacher, block['block_id'], markdown=block['markdown'],
                         plain_text=block['plain_text'], latex='', visibility_level='PUBLIC', accepted=True)
    version = service.publish(teacher, course['course_id'])
    assert MemoryLearningSkill(campus).student_dashboard(student['user_id'], course['course_id'])['document_count'] == 1
    assert service.preview_descriptor(student, doc_id)['preview_kind'] == 'text'
    assert service.preview_file(student, doc_id) == ('text/plain', '数据库事务')
    assert [d['document_id'] for d in service.list_student_source_files(student, course['course_id'])] == [doc_id]
    with pytest.raises(PermissionDenied):
        service.preview_file(outsider, doc_id)
    with pytest.raises(PermissionDenied):
        service.set_student_file_visibility(student, doc_id, False)
    token = auth.issue_document_token(student, doc_id)
    service.set_student_file_visibility(teacher, doc_id, False)
    actor = auth.authenticate_document_token(token, doc_id)
    with pytest.raises(PermissionDenied):
        service.preview_file(actor, doc_id)
    assert service.list_student_source_files(student, course['course_id']) == []
    assert MemoryLearningSkill(campus).student_dashboard(student['user_id'], course['course_id'])['document_count'] == 0
    service.set_student_file_visibility(teacher, doc_id, True)
    withdraw_version(campus, teacher, course['course_id'], version['version_id'])
    assert service.list_student_source_files(student, course['course_id']) == []
    assert MemoryLearningSkill(campus).student_dashboard(student['user_id'], course['course_id'])['document_count'] == 0
    with pytest.raises(PermissionDenied):
        service.preview_file(actor, doc_id)
