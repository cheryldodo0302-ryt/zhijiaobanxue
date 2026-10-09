from test_campus import campus


def test_counts_include_all_records_beyond_recent_history(campus):
    course = campus.create_course('统计回归', 'personal_course', 'student_1', 'student')
    cid = course['course_id']
    for _ in range(23):
        campus.ask(cid, 'student_1', 'student', '当前资料还不足吗？')
    result = campus.profile(cid, 'student_1', 'student')
    assert len(result['questions']) == 20
    assert result['summary']['question_count'] == 23
    assert result['summary']['quiz_count'] == 0
    other = campus.create_course('另一门课', 'personal_course', 'student_1', 'student')
    assert campus.profile(other['course_id'], 'student_1', 'student')['summary']['question_count'] == 0
