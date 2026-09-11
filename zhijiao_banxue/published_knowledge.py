"""Immutable publication content and shared student scope checks."""
import json


def capture_publication(conn, version_id):
    version = conn.execute("SELECT snapshot_ready FROM knowledge_versions WHERE version_id=?", (version_id,)).fetchone()
    if not version or version[0]:
        return
    nodes = conn.execute("SELECT n.* FROM knowledge_version_nodes v JOIN knowledge_nodes n USING(node_id) WHERE v.version_id=?", (version_id,)).fetchall()
    for raw in nodes:
        node = dict(raw)
        blocked = conn.execute("""SELECT 1 FROM knowledge_node_sources s JOIN document_blocks b USING(block_id)
            WHERE s.node_id=? AND (b.visibility_level<>'PUBLIC' OR b.verification_status NOT IN ('auto_verified','teacher_verified')) LIMIT 1""", (node['node_id'],)).fetchone()
        if blocked:
            continue
        node["class_ids"] = [r[0] for r in conn.execute("SELECT class_id FROM knowledge_node_class_scopes WHERE node_id=?", (node["node_id"],))]
        sources = [dict(r) for r in conn.execute("""SELECT s.document_id,s.page_number,d.original_name FROM knowledge_node_sources s
            JOIN course_documents d USING(document_id) WHERE s.node_id=?""", (node["node_id"],))]
        if not sources and node.get("document_id"):
            sources = [dict(r) for r in conn.execute("SELECT document_id,original_name,NULL page_number FROM course_documents WHERE document_id=?", (node["document_id"],))]
        node["sources"] = sources
        node["original_name"] = "；".join(dict.fromkeys(s["original_name"] for s in sources)) or "课程知识库"
        node["page_number"] = next((s["page_number"] for s in sources if s["page_number"]), None)
        node["section"] = node["title"]
        conn.execute("INSERT INTO published_knowledge_items VALUES(?,?,?,?)", (version_id,node["node_id"],"node",json.dumps(node,ensure_ascii=False)))
    for raw in conn.execute("""SELECT b.*,d.original_name,COALESCE(m.material_type,'other') material_type
        FROM knowledge_version_blocks v JOIN document_blocks b USING(block_id)
        JOIN course_documents d USING(document_id) LEFT JOIN document_material_metadata m USING(document_id)
        WHERE v.version_id=?""", (version_id,)).fetchall():
        block = dict(raw)
        if block['visibility_level'] != 'PUBLIC' or block['verification_status'] not in ('auto_verified','teacher_verified'):
            continue
        block["class_ids"] = [r[0] for r in conn.execute("SELECT class_id FROM teaching_archive_document_assignments WHERE document_id=?", (block["document_id"],))]
        conn.execute("INSERT INTO published_knowledge_items VALUES(?,?,?,?)", (version_id,block["block_id"],"block",json.dumps(block,ensure_ascii=False)))
    conn.execute("UPDATE knowledge_versions SET snapshot_ready=1 WHERE version_id=?", (version_id,))


def publication(db, course_id, user_id=None):
    version = db.fetch_one("SELECT * FROM knowledge_versions WHERE course_id=? AND status='published' ORDER BY version_number DESC LIMIT 1", (course_id,))
    if not version or not version["snapshot_ready"]:
        return version, [], []
    classes = {r["class_id"] for r in db.fetch_all("""SELECT c.class_id FROM classes c JOIN class_memberships m USING(class_id)
        WHERE c.course_id=? AND m.student_id=? AND c.status='active' AND m.status='active'""", (course_id,user_id))} if user_id else set()
    rows = db.fetch_all("SELECT kind,snapshot_json FROM published_knowledge_items WHERE version_id=?", (version["version_id"],))
    nodes, blocks = [], []
    for row in rows:
        item = json.loads(row["snapshot_json"])
        item["_allowed"] = not item.get("class_ids") or bool(classes.intersection(item["class_ids"]))
        if user_id is None:
            item["_allowed"] = not item.get("class_ids")
        (nodes if row["kind"] == "node" else blocks).append(item)
    return version, nodes, blocks


def visible_nodes(db, course_id, user_id):
    version, nodes, _ = publication(db, course_id, user_id)
    return version, [n for n in nodes if n["_allowed"] and n["node_type"] == "knowledge_point"]


def document_allowed(db, course_id, document_id, user_id):
    _, nodes, blocks = publication(db, course_id, user_id)
    relevant = [n for n in nodes if n["node_type"] == "knowledge_point" and
                (n.get("document_id") == document_id or any(s["document_id"] == document_id for s in n.get("sources", [])))]
    if relevant:
        return all(n["_allowed"] for n in relevant)
    relevant = [b for b in blocks if b["document_id"] == document_id]
    return bool(relevant) and all(b["_allowed"] for b in relevant)


def capture_question_publication(conn, version_id):
    rows = conn.execute("""SELECT q.* FROM question_bank_version_items v JOIN question_bank_items q USING(item_id)
        WHERE v.version_id=? AND v.snapshot_json IS NULL""", (version_id,)).fetchall()
    for raw in rows:
        item = dict(raw)
        item['class_ids'] = [r[0] for r in conn.execute("SELECT class_id FROM knowledge_node_class_scopes WHERE node_id=?", (item.get('knowledge_node_id'),))]
        if not item['class_ids']:
            item['class_ids'] = [r[0] for r in conn.execute("SELECT class_id FROM teaching_archive_document_assignments WHERE document_id=?", (item.get('document_id'),))]
        conn.execute('UPDATE question_bank_version_items SET snapshot_json=? WHERE version_id=? AND item_id=? AND snapshot_json IS NULL',
            (json.dumps(item,ensure_ascii=False),version_id,item['item_id']))


def question_items(db, version_id, course_id, user_id):
    classes = {r['class_id'] for r in db.fetch_all("""SELECT c.class_id FROM classes c JOIN class_memberships m USING(class_id)
        WHERE c.course_id=? AND m.student_id=? AND c.status='active' AND m.status='active'""", (course_id,user_id))}
    rows = db.fetch_all('SELECT snapshot_json FROM question_bank_version_items WHERE version_id=? AND snapshot_json IS NOT NULL', (version_id,))
    items = [json.loads(r['snapshot_json']) for r in rows]
    items.sort(key=lambda q:(q.get('import_row_number') or 0,q.get('created_at') or '',q['item_id']))
    return [q for q in items if not q.get('class_ids') or classes.intersection(q['class_ids'])]


def withdraw_version(campus, actor, course_id, version_id):
    """Withdraw exactly the version selected by the owning teacher, never an entire course."""
    from campus_service import PermissionDenied, ValidationError
    if actor.get('role') != 'teacher':
        raise PermissionDenied('仅教师可以撤回自己发布的版本')
    campus.require_access(course_id,str(actor['user_id']),'teacher')
    with campus.db.connect() as conn:
        row = conn.execute('SELECT status FROM knowledge_versions WHERE course_id=? AND version_id=?', (course_id,version_id)).fetchone()
        if not row:
            raise ValidationError('所选版本不属于当前课程')
        changed = conn.execute("UPDATE knowledge_versions SET status='superseded' WHERE course_id=? AND version_id=? AND status='published'", (course_id,version_id)).rowcount
    return {'course_id':course_id,'version_id':version_id,'withdrawn':bool(changed)}


def version_history(campus, actor, course_id):
    from campus_service import PermissionDenied
    if actor.get('role') != 'teacher':
        raise PermissionDenied('仅教师可以管理课程发布版本')
    campus.require_access(course_id,str(actor['user_id']),'teacher')
    return campus.db.fetch_all('SELECT version_id,version_number,status,published_at FROM knowledge_versions WHERE course_id=? ORDER BY version_number DESC', (course_id,))
