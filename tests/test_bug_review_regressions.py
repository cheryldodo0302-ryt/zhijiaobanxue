from datetime import date

import pytest

from campus_service import PermissionDenied, ValidationError
from test_teacher_foundation import services


def setup_class(services):
    db, auth, teachers = services
    actor = auth.create_user('calendar-owner', 'safe-password-123', 'teacher')
    course = teachers.create_course(actor, '课程')
    term = teachers.create_term(actor, '秋季', date(2026, 9, 7), date(2026, 12, 31))
    group = teachers.create_class(actor, course['course_id'], term['term_id'], '一班')
    return db, auth, teachers, actor, course, group


def test_calendar_adjustment_atomicity_and_scope(services):
    db, auth, teachers, actor, course, group = setup_class(services)
    rows = [dict(weekday=1, start_time='08:00', end_time='09:00', location='A101', starts_week=1, ends_week=3)]
    changes = [dict(original_date='2026-09-07', makeup_date='2026-09-12', reason='补课'),
               dict(original_date='2026-09-14', makeup_date=None, reason='停课')]
    teachers.replace_weekly_schedules(actor, group['class_id'], rows, changes)
    calendar = teachers.course_calendar(actor, course['course_id'])
    assert [e['date'] for e in calendar['events']] == ['2026-09-12', '2026-09-21']
    assert calendar['events'][0]['week'] == 1
    assert calendar['events'][0]['weekday'] == 6
    assert calendar['events'][0]['original_date'] == '2026-09-07'
    assert len(calendar['cancelled_events']) == 1
    for invalid in ([dict(original_date='bad')], changes + changes[:1], [dict(original_date='2027-01-01')]):
        with pytest.raises(ValidationError):
            teachers.replace_weekly_schedules(actor, group['class_id'], [], invalid)
        assert teachers.course_calendar(actor, course['course_id']) == calendar
    with pytest.raises(ValidationError):
        teachers.replace_weekly_schedules(actor, group['class_id'], [{**rows[0], 'location': '  '}], [])
    assert teachers.course_calendar(actor, course['course_id']) == calendar
    other = auth.create_user('calendar-other', 'safe-password-123', 'teacher')
    with pytest.raises(PermissionDenied):
        teachers.replace_weekly_schedules(other, group['class_id'], [], [])
    teachers.replace_weekly_schedules(actor, group['class_id'], rows)
    assert len(teachers.course_calendar(actor, course['course_id'])['adjustments']) == 2
    teachers.replace_weekly_schedules(actor, group['class_id'], rows, [])
    assert len(teachers.course_calendar(actor, course['course_id'])['events']) == 3


def test_student_number_validation_and_duplicate_identity(services, monkeypatch):
    monkeypatch.setenv('ZHIJIAO_STUDENT_DEFAULT_PASSWORD', 'initial-password-123')
    db, auth, teachers, actor, course, group = setup_class(services)
    def add(number, name='张同学'):
        return teachers.import_members(actor, group['class_id'], [{'student_number': number, 'display_name': name}])['results'][0]
    for number in ('20202asda', '１２３４５６', '12345', '1' * 21):
        assert add(number)['status'] == 'invalid'
    first = add('00202601')
    assert first['status'] == 'created'
    repeated = add('00202601')
    assert repeated['status'] == 'already_member'
    assert repeated['user_id'] == first['user_id']
    assert add('00202601', '另一位')['status'] == 'conflict'
    assert len(db.fetch_all('SELECT * FROM users WHERE student_number=?', ('00202601',))) == 1
    with pytest.raises(ValidationError):
        auth.create_user('invalid-number', 'safe-password-123', 'student', student_number='20202asda')
