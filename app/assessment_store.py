"""Server-owned assessment papers. One result per paper, without content hashing."""
import json
import uuid


class AssessmentStore:
    def __init__(self, db):
        self.db = db

    def create(self, course_id, user_id, kind, items, metadata=None, paper_id=None):
        paper_id = paper_id or "paper_" + uuid.uuid4().hex
        self.db.execute("INSERT OR IGNORE INTO assessment_papers(paper_id,course_id,user_id,kind,items_json,metadata_json) VALUES(?,?,?,?,?,?)",
                        (paper_id,course_id,user_id,kind,json.dumps(items,ensure_ascii=False),json.dumps(metadata or {},ensure_ascii=False)))
        return paper_id

    def load(self, paper_id, course_id, user_id, kind):
        from campus_service import ValidationError
        row = self.db.fetch_one("SELECT * FROM assessment_papers WHERE paper_id=? AND course_id=? AND user_id=? AND kind=?", (paper_id,course_id,user_id,kind))
        if not row:
            raise ValidationError("试卷不存在或不属于当前账号和课程，请重新生成练习")
        row["items"] = json.loads(row["items_json"])
        row["metadata"] = json.loads(row["metadata_json"])
        return row

    @staticmethod
    def previous(row, responses):
        from campus_service import ValidationError
        if row["result_json"] is None:
            return None
        if json.loads(row["responses_json"]) != responses:
            raise ValidationError("本次练习已提交；如需再次练习，请生成新试卷")
        return json.loads(row["result_json"])

    def finish(self, paper, responses, save):
        with self.db.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = dict(conn.execute("SELECT * FROM assessment_papers WHERE paper_id=?", (paper["paper_id"],)).fetchone())
            previous = self.previous(row, responses)
            if previous is not None:
                return previous
            result = save(conn)
            conn.execute("UPDATE assessment_papers SET responses_json=?,result_json=? WHERE paper_id=?",
                         (json.dumps(responses,ensure_ascii=False),json.dumps(result,ensure_ascii=False),paper["paper_id"]))
            return result
