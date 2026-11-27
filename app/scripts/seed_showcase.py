"""Create an additive, repeatable fictional showcase without resetting existing users."""
from __future__ import annotations

import csv
import hashlib
import io
import json
import secrets
import sys
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from auth_service import AuthService
from campus_service import CampusService
from config import DB_PATH
from database import LearningDatabase
from ingestion_service import IngestionService
from llm_provider import MockProvider
from teacher_service import TeacherService
from teaching_archive_service import TeachingArchiveService

TEACHER_ID = "demo_showcase_teacher_001"
COURSES = [
    ("DB2026", "数据库原理与应用", [
        ("数据库系统概述", "数据库管理系统负责数据定义、组织、存储和访问控制，应用程序通过接口访问数据。", "建立课程实验环境"),
        ("关系模型", "关系由元组和属性组成，主键用于唯一标识元组，外键用于维护表间引用完整性。", "设计学生选课关系表"),
        ("SQL基础查询", "SELECT选择查询列，WHERE过滤记录，ORDER BY对结果排序；NULL使用IS NULL判断。", "编写单表条件查询"),
        ("连接与聚合", "JOIN通过连接条件组合多表，GROUP BY用于分组，HAVING筛选分组结果。", "完成选课统计查询"),
        ("数据库设计", "从需求分析到概念模型，再映射为关系模式；规范化减少冗余和更新异常。", "完成教务系统ER模型"),
        ("事务与并发", "事务具有原子性、一致性、隔离性和持久性；并发控制用于避免脏读等异常。", "观察并发事务隔离"),
        ("索引与优化", "索引可加快检索，但占用空间并增加更新代价；执行计划用于分析查询路径。", "比较建索引前后的查询"),
        ("安全与备份", "最小权限控制数据访问；备份必须配合恢复演练，验证数据完整性与可恢复性。", "完成权限配置和恢复演练"),
    ]),
    ("PY2026", "Python程序设计", [
        ("程序与环境", "程序由可执行指令组成，解释器执行Python代码；虚拟环境隔离项目依赖。", "建立Python虚拟环境"),
        ("数据类型", "整数、浮点数、字符串和布尔值用于表示不同数据；类型转换应考虑非法输入。", "实现健康指标输入校验"),
        ("分支与循环", "if根据条件选择分支，for遍历可迭代对象，while在条件成立时重复执行。", "实现菜单和循环统计"),
        ("函数与模块", "函数封装可复用逻辑，参数传入数据，返回值输出结果；模块组织相关功能。", "封装数据清洗函数"),
        ("容器与算法", "列表保留顺序，字典保存键值映射，集合用于去重；算法需考虑时间和空间开销。", "完成词频与去重分析"),
        ("文件与异常", "with语句管理资源，读取文本应指定编码，异常处理用于报告和恢复可预期错误。", "读写虚构实验记录"),
        ("对象与测试", "类封装数据与行为，测试通过已知输入验证预期输出，边界情况需单独检查。", "实现课程成绩管理类"),
        ("项目与交付", "项目应提供依赖说明、输入输出约定和运行入口，并使用虚构数据验证结果。", "交付学习记录统计工具"),
    ]),
    ("AI2026", "医学人工智能导论", [
        ("人工智能与医学", "医学人工智能利用数据和算法辅助分析，其结果需要专业人员结合使用场景审核。", "分析虚构医学AI案例"),
        ("数据质量", "缺失、噪声、标签偏差和样本代表性影响模型质量，清洗过程应保留可追溯记录。", "检查虚构样本质量"),
        ("数据划分", "训练集用于拟合，验证集用于选择模型，测试集用于最终评估，需避免患者级数据泄漏。", "按虚构患者划分数据"),
        ("分类指标", "灵敏度衡量阳性检出能力，特异度衡量阴性识别能力，指标应结合误判代价解释。", "计算混淆矩阵指标"),
        ("模型训练", "模型训练通过优化损失函数调整参数，正则化和合理验证有助于识别过拟合。", "比较简单分类器"),
        ("外部验证", "外部验证使用不同来源的数据检查泛化能力，不能用训练表现替代真实适用性评估。", "比较不同数据来源表现"),
        ("解释与不确定性", "解释方法帮助理解预测依据，不确定性提示模型置信边界，两者都不能替代临床证据。", "审查虚构预测解释"),
        ("伦理与人机协同", "遵循最小必要、访问控制和人工复核原则，明确系统用途、责任和退出机制。", "制定演示系统使用规范"),
    ]),
]


def seed(db: LearningDatabase, *, with_learning: bool = True):
    folder = db.db_path.parent / "showcase"
    folder.mkdir(parents=True, exist_ok=True)
    credential_path = folder / "accounts.json"
    auth = AuthService(db)
    teacher = db.fetch_one("SELECT * FROM users WHERE username='showcase_teacher'")
    if teacher and teacher["user_id"] != TEACHER_ID:
        raise RuntimeError("showcase_teacher 已被其他账号使用，未修改该账号。")
    if not teacher:
        password = secrets.token_urlsafe(12)
        credential_path.write_text(json.dumps({"username": "showcase_teacher", "password": password}, indent=2), encoding="utf-8")
        db.execute("""INSERT INTO users(user_id,username,password_hash,role,display_name,status,must_change_password)
                      VALUES(?, 'showcase_teacher', ?, 'teacher', '林**（演示）', 'active', 0)""",
                   (TEACHER_ID, auth.passwords.hash(password)))
    from scripts.showcase_learning import anonymize_showcase
    anonymize_showcase(db, TEACHER_ID)
    teacher = auth.get_user(TEACHER_ID)
    campus = CampusService(db, db.db_path.parent / "uploads", provider_factory=MockProvider)
    teachers = TeacherService(db, campus)
    ingestion = IngestionService(db, campus)
    archives = TeachingArchiveService(db, campus, ingestion)
    term_name = "2026-2027学年第一学期（演示）"
    term = db.fetch_one("SELECT * FROM terms WHERE owner_id=? AND term_name=?", (TEACHER_ID, term_name))
    if not term:
        term = teachers.create_term(teacher, term_name, date(2026, 9, 7), date(2027, 1, 17), "2026-2027", "第一学期")
    names = list("陈林王李张刘赵周吴郑徐孙朱胡郭何高罗梁宋唐许韩冯邓曹彭曾肖田")
    roster = []
    for group in (1, 2):
        for index, name in enumerate(names, 1):
            number = f"202624{group:02d}{index:02d}"
            display_name = name + "**"
            existing = db.fetch_one("SELECT * FROM users WHERE username=? OR student_number=?", (number, number))
            if existing and (existing["display_name"] != display_name or existing["role"] != "student"):
                raise RuntimeError(f"演示学号 {number} 与已有账号冲突，未覆盖。")
            if not existing:
                existing = auth.create_user(number, number, "student", display_name, student_number=number)
            roster.append({"group": group, "student_number": number, "display_name": display_name, "user_id": existing["user_id"]})

    summary = []
    for course_index, (code, name, topics) in enumerate(COURSES):
        course = db.fetch_one("SELECT * FROM courses WHERE owner_id=? AND course_name=?", (TEACHER_ID, name))
        if not course:
            course = teachers.create_course(teacher, name, f"完全虚构的展示课程；课程代码{code}；4学分；64学时（理论32、实验32）；面向信管专业演示教学班。")
        classes = []
        for group in (1, 2):
            class_name = f"信管{group}班（演示）"
            classroom = db.fetch_one("SELECT * FROM classes WHERE course_id=? AND class_name=?", (course["course_id"], class_name))
            if not classroom:
                classroom = teachers.create_class(teacher, course["course_id"], term["term_id"], class_name,
                    f"{code}-{group:02d}", f"周{'一三五'[course_index]} {'1-4' if group == 1 else '5-8'}节",
                    "演示校区", "20**", "信管", "本科·标准")
            result = teachers.import_members(teacher, classroom["class_id"], [r for r in roster if r["group"] == group])
            if result["summary"]["conflict"] or result["summary"]["invalid"]:
                raise RuntimeError(str(result["summary"]))
            if not db.fetch_one("SELECT 1 ok FROM class_weekly_schedules WHERE class_id=?", (classroom["class_id"],)):
                teachers.replace_weekly_schedules(teacher, classroom["class_id"], [{"weekday": course_index * 2 + 1,
                    "start_time": "08:00" if group == 1 else "13:30", "end_time": "11:30" if group == 1 else "17:00",
                    "starts_week": 1, "ends_week": 16, "location": f"教学楼A{301 + course_index}",
                    "details": {"period_label": "1-4节" if group == 1 else "5-8节", "fictional": True}}])
            classes.append(classroom)
        seed_archive(db, archives, teacher, course, term, classes, code, topics)
        seed_knowledge(db, ingestion, teacher, course, topics)
        learning = {}
        if with_learning:
            from scripts.showcase_learning import enrich_course
            learning = enrich_course(db, campus, ingestion, teacher, course, classes, code, topics)
        view = archives.workbench(teacher, course["course_id"])
        if view["metrics"]["review_required"]:
            raise RuntimeError(f"{name} 的教学档案尚有待核对项：{view['metrics']}")
        summary.append({"course_id": course["course_id"], "name": name, "classes": [c["class_id"] for c in classes],
                        "archive_metrics": view["metrics"], "learning": learning})
    anonymize_showcase(db, TEACHER_ID)
    with (folder / "学生账号名单.csv").open("w", encoding="utf-8-sig", newline="") as file:
        writer = csv.writer(file)
        writer.writerow(["班级", "姓名", "学号/登录账号", "初始密码", "说明"])
        writer.writerows([[f"信管{r['group']}班（演示）", r["display_name"], r["student_number"], r["student_number"],
                          "完全虚构；首次演示免改密；自行改密后以新密码为准"] for r in roster])
    with (folder / "展示脱敏名单.csv").open("w", encoding="utf-8-sig", newline="") as file:
        writer = csv.writer(file)
        writer.writerow(["班级", "姓名", "账号标识（已脱敏）", "密码（不展示）", "说明"])
        writer.writerows([[f"信管{r['group']}班（演示）", r["display_name"],
                           r["student_number"][:2] + "******" + r["student_number"][-2:], "********", "虚构展示数据"] for r in roster])
    if credential_path.exists():
        credentials = json.loads(credential_path.read_text(encoding="utf-8"))
        (folder / "展示账号说明.txt").write_text(
            "所有展示账号、课程和档案均为虚构，不代表实际教学记录。\n"
            f"教师账号：{credentials['username']}\n教师初始密码：{credentials['password']}\n"
            "学生一班：2026240101—2026240130；二班：2026240201—2026240230\n"
            "学生初始密码为本人账号，演示账号免首次改密；已自行改密的账号保持新密码。\n"
            "共3门课、6个教学班，每班30人；同一行政班跨课程复用同一批账号。\n"
            "班级统一为信管，姓名仅保留姓氏，校区、年级等身份信息使用演示值；登录账号与密码为虚构数据。\n"
            "投屏或对外演示可用展示脱敏名单.csv，其中账号和密码均已遮盖。\n"
            "每门课均有大纲、16次教案、16周进度、8个实验、考核方案、反思及原始资料。\n"
            "已补齐关联题库、正式作业与考试、作答、学生画像、知识图谱及教学诊断；模拟评价明确标注，未调用在线AI。\n"
            "复制整个项目后，这些账号及资料一并保留。名单见同目录学生账号名单.csv。\n", encoding="utf-8")
    (folder / "manifest.json").write_text(json.dumps({"fictional": True, "student_count": len(roster), "courses": summary}, ensure_ascii=False, indent=2), encoding="utf-8")
    return summary


def seed_archive(db, archives, teacher, course, term, classes, code, topics):
    # Plain UTF-8 source documents remain previewable on a clean Windows machine.
    # Structured rows are seeded from these same sections, with precise provenance.
    def item(kind, title, text, payload, order=0):
        return {"record_type": kind, "title": title, "markdown": text, "payload": payload,
                "source": {"section": title, "fictional": True}, "sort": order}

    name = course["course_name"]
    syllabus = {"course_name": name, "course_code": code, "major": "信管", "credits": "4",
                "course_type": "专业课程", "audience": "信管1、2班（演示）", "total_hours": "64",
                "assessment_method": "形成性评价与期末综合评价", "theory_hours": 32, "experiment_hours": 32,
                "objectives": "掌握核心概念，能完成可复现实验，并说明方案的适用条件与局限。"}
    profile = "\n".join(f"{k}：{v}" for k, v in syllabus.items())
    lessons, schedule, experiments, reflections = [], [], [], []
    for week in range(1, 17):
        title, concept, experiment = topics[(week - 1) // 2]
        chapter = f"第{(week - 1) // 2 + 1}章 {title}"
        objective = f"{'理解概念并完成引导练习' if week % 2 else '完成综合应用并解释结果'}：{concept}"
        steps = [["环节", "内容", "教师活动", "学生活动", "时间"], ["导入", title, "提出案例问题", "解释已有认识", "15分钟"],
                 ["讲授", concept, "示范分析方法", "记录关键步骤", "55分钟"],
                 ["实验", experiment, "检查任务和边界条件", "分组操作并记录结果", "90分钟"],
                 ["总结", "反例与自评", "反馈共性问题", "提交结论与证据", "20分钟"]]
        body = f"{chapter}\n教学目标：{objective}\n重点：{title}\n难点：把概念用于具体情境并检查边界条件。\n" + "\n".join(" | ".join(row) for row in steps)
        lessons.append(item("lesson_session", f"第{week}次课：{title}", body,
            {"session_number": week, "chapter": chapter, "duration": "4学时", "audience": syllabus["audience"],
             "teaching_method": "案例讲授、分组讨论与上机实验", "learner_analysis": "已具备计算机基础，需强化证据与结果解释。",
             "objectives": objective, "key_difficulties": "概念辨析、规范操作与结果解释", "time_allocation": "理论2学时+实验2学时", "teaching_steps": steps}, week))
        day = date(2026, 9, 7) + timedelta(weeks=week - 1, days={"DB2026": 0, "PY2026": 2, "AI2026": 4}[code])
        schedule.append(item("teaching_schedule_entry", f"第{week}周：{title}", f"第{week}周，{chapter}；{experiment}；理论2学时+实验2学时。",
            {"周次": week, "日期": day.isoformat(), "课程名称": name, "授课班级": "信管1、2班（演示）", "理论/实验学时": "2/2",
             "理论/实验授课内容": f"{title}；{experiment}", "教师": "林**（演示）", "授课性质": "理论+实验"}, week))
    for number, (title, concept, experiment) in enumerate(topics, 1):
        body = f"实验{number}：{experiment}\n目标：{concept}\n任务：准备输入、执行操作、记录输出、比较反例、提交复现步骤。\n验收：过程可复现、结果有证据、说明适用条件。"
        experiments.append(item("experiment_project", f"实验{number}：{experiment}", body,
            {"experiment_number": number, "hours": "4", "requirements": "必做；双人协作，独立提交实验记录", "location": "演示校区实验室", "objectives": concept}, number))
        reflections.append(item("teaching_reflection", f"第{number}单元教学反思（模拟）",
            f"本条为模拟反思：{title}采用先解释再操作的安排；下一轮增加一组错误输入，检查学生能否说明{experiment}的边界。",
            {"session_number": number * 2, "fictional": True}, number))
    assessment_text = (f"{name}考核方案（完全虚构）\n平时成绩20%：预习、课堂讨论、学习记录与规范操作。\n"
                       "阶段考核成绩30%：实验报告与阶段项目，关注过程证据、复现和错误分析。\n"
                       "期末成绩50%：概念辨析、情境分析和综合任务。\n总评=平时×0.20+阶段×0.30+期末×0.50；满分100，60分合格。")
    documents = [
        ("教学大纲.txt", "syllabus", [item("syllabus_profile", name + "教学大纲", profile, syllabus)]),
        ("全学期教案.txt", "lesson_plan", lessons),
        ("教学进度.txt", "teaching_schedule", schedule),
        ("实验教学大纲.txt", "syllabus", experiments),
        ("教学反思.txt", "other", reflections),
        ("考核方案.txt", "assessment", None),
    ]
    for filename, document_type, items in documents:
        filename = code + "-" + filename
        existing = db.fetch_one("SELECT d.* FROM teaching_archive_documents d JOIN teaching_archive_versions v USING(version_id) WHERE v.course_id=? AND d.original_name=?", (course["course_id"], filename))
        if existing:
            continue
        content = assessment_text if items is None else "完全虚构的展示教学档案\n\n" + "\n\n".join("【" + entry["title"] + "】\n" + entry["markdown"] for entry in items)
        batch = archives.create_import_batch(teacher, course["course_id"], term_id=term["term_id"], defaults={
            "campus": "演示校区", "cohort_year": "20**", "major": "信管", "teaching_level": "本科·标准"})
        uploaded = archives.add_import_file(teacher, batch["batch_id"], filename, "text/plain", io.BytesIO(content.encode("utf-8")))
        archives.update_import_file(teacher, batch["batch_id"], uploaded["file_id"], {"class_ids": [c["class_id"] for c in classes], "record_type": document_type, "routing_target": "teaching_archive"})
        result = archives.commit_import_batch(teacher, batch["batch_id"])
        if result.get("error_count"):
            raise RuntimeError(f"教学档案导入失败：{filename}")
        doc = db.fetch_one("SELECT * FROM teaching_archive_documents WHERE file_id=?", (uploaded["file_id"],))
        if items is not None:
            # The generic TXT importer produces an "other" row. Replace only
            # this newly created document's row with its verified section data.
            db.execute("DELETE FROM teaching_archive_items WHERE archive_document_id=?", (doc["archive_document_id"],))
            archives._insert_items(doc["archive_document_id"], doc["version_id"], items, [])
    archives._validate_sequences(course["course_id"])
    archives._validate_hours(course["course_id"])


def seed_knowledge(db, service, teacher, course, topics):
    filename = course["course_name"] + "-学习手册（演示）.txt"
    text = "完全虚构的课程展示资料\n\n" + "\n\n".join(f"第{i}章 {title}\n{concept}\n实践任务：{experiment}。" for i, (title, concept, experiment) in enumerate(topics, 1))
    if db.fetch_one("SELECT 1 ok FROM course_documents WHERE course_id=? AND sha256=?", (course["course_id"], hashlib.sha256(text.encode("utf-8")).hexdigest())):
        return
    job = service.queue_document_stream(teacher, course["course_id"], filename, "text/plain", io.BytesIO(text.encode("utf-8")), analysis_mode="local")
    service.process_job(job["job_id"])
    blocks = service.list_blocks(teacher, job["document_id"])
    if not blocks:
        raise RuntimeError(f"学习手册未解析出内容：{filename}")
    for block in blocks:
        service.review_block(teacher, block["block_id"], markdown=block["markdown"], plain_text=block["plain_text"], latex=block.get("latex") or "", visibility_level="PUBLIC", accepted=True)
    service.set_student_file_visibility(teacher, job["document_id"], True)
    service.publish(teacher, course["course_id"])


if __name__ == "__main__":
    database = LearningDatabase(DB_PATH)
    try:
        result = seed(database)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        print("展示资料已就绪，账号见 data/showcase/展示账号说明.txt")
    finally:
        database.engine.dispose()
