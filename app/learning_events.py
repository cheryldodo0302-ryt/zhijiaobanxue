"""Shared reporting vocabulary; historical rows are read without rewriting them."""
import json
from collections import Counter, defaultdict


def record(conn, source, source_id, course_id, user_id, score=None, total=0, records=None, question='', refused=False):
    classes = [r[0] for r in conn.execute("""SELECT c.class_id FROM classes c JOIN class_memberships m USING(class_id)
        WHERE c.course_id=? AND m.student_id=? AND c.status='active' AND m.status='active'""", (course_id,user_id))]
    conn.execute("""INSERT OR IGNORE INTO learning_events(event_id,course_id,user_id,source,score,total,
        records_json,question,refused,class_ids_json) VALUES(?,?,?,?,?,?,?,?,?,?)""",
        (f'{source}:{source_id}',course_id,user_id,source,score,total,json.dumps(records or [],ensure_ascii=False),
         question,int(refused),json.dumps(classes)))


def history(db, course_id, user_id=None):
    """Normalize existing records without inventing their historical class membership."""
    result = []
    for table, source, pk in [('course_questions','question','question_id'),('course_attempts','course','attempt_id'),
                              ('memory_attempts','memory','attempt_id'),('ai_practice_attempts','ai','attempt_id')]:
        args = (course_id,user_id) if user_id else (course_id,)
        rows = db.fetch_all(f"SELECT * FROM {table} WHERE course_id=?" + (' AND user_id=?' if user_id else ''), args)
        for row in rows:
            item = {**row,'event_id':f'{source}:{row[pk]}','source':source,'legacy':1,'class_ids_json':'[]'}
            item.setdefault('score',None)
            item.setdefault('total',0)
            item.setdefault('question','')
            item.setdefault('refused',0)
            item.setdefault('records_json','[]')
            if source == 'memory':
                block = db.fetch_one('SELECT title FROM knowledge_blocks WHERE block_id=?', (row['block_id'],))
                item['total'] = 1
                item['records_json'] = json.dumps([{'knowledge_points':[block['title']] if block else [],'correct':row['score']>=80}])
            elif source == 'ai':
                questions = json.loads(row['questions_json'])
                if any(q.get('source_file') for q in questions):
                    item['source'] = 'ai_private'
                    item['event_id'] = f"ai_private:{row[pk]}"
                grades = {int(r['index'])-1:r for r in json.loads(row['result_json']).get('results',[]) if str(r.get('index','')).isdigit()}
                item['total'] = len(questions)
                item['records_json'] = json.dumps([{'knowledge_points':[q.get('knowledge_point','')],
                    'question':q.get('question',''),'correct':bool(grades.get(i,{}).get('correct'))} for i,q in enumerate(questions)])
            result.append(item)
    rows = db.fetch_all("""SELECT a.*,
        COALESCE(json_extract(vi.snapshot_json,'$.knowledge_points_json'),'[]') knowledge_points_json,
        COALESCE(json_extract(vi.snapshot_json,'$.stem_markdown'),'历史题目（缺少发布快照）') stem_markdown
        FROM question_bank_attempts a
        LEFT JOIN question_bank_version_items vi ON vi.version_id=a.version_id AND vi.item_id=a.item_id
        WHERE a.course_id=?""" + (' AND a.student_id=?' if user_id else ''),
        (course_id,user_id) if user_id else (course_id,))
    groups = defaultdict(list)
    for row in rows:
        groups[row['submission_id']].append(row)
    for submission_id, group in groups.items():
        result.append({'event_id':f'published:{submission_id}','course_id':course_id,'user_id':group[0]['student_id'],
            'source':'published','score':round(100*sum(r['is_correct'] for r in group)/len(group),1),'total':len(group),
            'records_json':json.dumps([{'knowledge_points':json.loads(r['knowledge_points_json']),
                'question':r['stem_markdown'],'correct':bool(r['is_correct'])} for r in group]),
            'question':'','refused':0,'class_ids_json':'[]','legacy':1,'created_at':group[0]['submitted_at']})
    return result


def events(db, course_id, user_id=None, class_id=None):
    sql, args = 'SELECT * FROM learning_events WHERE course_id=?', [course_id]
    if user_id:
        sql += ' AND user_id=?'
        args.append(user_id)
    saved = db.fetch_all(sql,tuple(args))
    if user_id is None:
        saved = [r for r in saved if r['source']!='ai_private']
    if class_id:
        return [r for r in saved if not r['legacy'] and class_id in json.loads(r['class_ids_json'])]
    merged = {r['event_id']:r for r in history(db,course_id,user_id)}
    if user_id is None:
        merged = {key:r for key,r in merged.items() if r['source']!='ai_private'}
    merged.update({r['event_id']:r for r in saved})
    return sorted(merged.values(),key=lambda r:(r['created_at'],r['event_id']),reverse=True)


def summarize(rows):
    questions = [r for r in rows if r['source']=='question']
    attempts = [r for r in rows if r['score'] is not None]
    points = defaultdict(lambda:{'answered':0,'correct':0})
    buckets = {'0-59':0,'60-69':0,'70-79':0,'80-89':0,'90-100':0}
    for row in attempts:
        score = max(0,min(100,float(row['score'])))
        key = '0-59' if score<60 else '60-69' if score<70 else '70-79' if score<80 else '80-89' if score<90 else '90-100'
        buckets[key] += 1
        for record in json.loads(row['records_json']):
            for point in dict.fromkeys(record.get('knowledge_points',[])):
                if point:
                    points[point]['answered'] += 1
                    points[point]['correct'] += int(bool(record.get('correct')))
    weak = [{'knowledge_point':p,**s,'accuracy':round(100*s['correct']/s['answered'],1),
             'weakness_score':round(1-s['correct']/s['answered'],3)} for p,s in points.items()]
    weak.sort(key=lambda x:(x['accuracy'],-x['answered']))
    return {'question_count':len(questions),'quiz_count':len(attempts),
        'average_score':round(sum(float(a['score']) for a in attempts)/len(attempts),1) if attempts else 0,
        'active_students':len({r['user_id'] for r in rows}),'score_buckets':buckets,
        'frequent_questions':[{'question':q,'count':n} for q,n in Counter(r['question'] for r in questions).most_common(10)],
        'uncovered_questions':[{'question':q,'count':n} for q,n in Counter(r['question'] for r in questions if r['refused']).most_common(10)],
        'weak_points':weak,'sources':dict(Counter(a['source'] for a in attempts)),
        'legacy_event_count':sum(bool(r['legacy']) for r in rows),'privacy':'仅展示共享课程的匿名聚合结果'}
