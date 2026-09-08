import streamlit as st

from config import DB_PATH, MATERIALS_DIR, MAX_EVIDENCE_CHARS, MIN_EVIDENCE_SCORE, TOP_K
from database import LearningDatabase
from llm_provider import build_provider
from skills.exercise import ExerciseItem, generate_exercises, grade_exercises
from skills.profile import export_csv_bytes, get_learning_profile, recommend_practice
from skills.qa import answer_question
from skills.retrieval import CourseRetriever
from ui import hero, inject_theme, section_intro

st.set_page_config(page_title="智教伴学", page_icon="📘", layout="wide")
inject_theme()


@st.cache_resource
def resources() -> tuple[LearningDatabase, CourseRetriever]:
    return LearningDatabase(DB_PATH), CourseRetriever(MATERIALS_DIR)


db, retriever = resources()
hero(len(retriever.chunks))

with st.sidebar:
    st.markdown("""
        <div class="side-brand">
          <span>STUDENT MODE</span>
          <h3>智教伴学</h3>
          <p>本地资料 · 有据可查 · 专注学习</p>
        </div>
    """, unsafe_allow_html=True)
    st.subheader("运行配置")
    provider_kind = st.selectbox("答疑模型", ["MockProvider（本地演示）", "OpenAI 兼容接口"])
    api_key = base_url = model = ""
    if provider_kind == "OpenAI 兼容接口":
        api_key = st.text_input("API Key", type="password")
        base_url = st.text_input("Base URL", placeholder="https://example.com/v1")
        model = st.text_input("模型名称")
    st.caption(f"● 资料索引正常 · {len(retriever.chunks)} 个片段")

tab_qa, tab_practice, tab_profile = st.tabs(["课程答疑", "个性化练习", "我的学习"])

with tab_qa:
    section_intro("01 · COURSE Q&A", "从课程资料中找到答案",
                  "答案严格基于本地资料，并附上文件、章节和原文证据；没有充分证据时会明确拒答。")
    question = st.text_area("请输入课程问题", placeholder="例如：什么是监督学习？", height=90)
    if st.button("查找课程答案", type="primary", disabled=not question.strip()):
        try:
            provider = build_provider(provider_kind, api_key, base_url, model)
            with st.spinner("正在检索课程资料……"):
                result = answer_question(question, retriever, provider, MIN_EVIDENCE_SCORE, TOP_K)
            qid = db.save_question(question, result.answer, [e.to_dict() for e in result.evidence],
                                   result.knowledge_points, result.refused)
            st.session_state.current_qa = {
                "question_id": qid, "answer": result.answer,
                "knowledge_points": result.knowledge_points, "refused": result.refused,
            }
            st.session_state.pop("exercise_items", None)
            if result.refused:
                st.warning(result.answer)
            else:
                st.success("已找到课程资料证据")
                st.markdown(result.answer)
                if result.knowledge_points:
                    st.caption("涉及知识点：" + "、".join(result.knowledge_points))
                st.markdown("#### 证据")
                for index, item in enumerate(result.evidence, 1):
                    with st.expander(f"[证据 #{index}] {item.source_file} · {item.section} · 匹配度 {item.score:.3f}"):
                        st.markdown(f"> {item.text[:MAX_EVIDENCE_CHARS]}")
        except Exception as exc:
            st.error(f"处理问题时发生错误：{exc}")

with tab_practice:
    section_intro("02 · PERSONAL PRACTICE", "把刚学会的，真正练会",
                  "围绕当前答疑涉及的知识点生成三道练习，提交后获得逐题解析与薄弱点记录。")
    qa = st.session_state.get("current_qa")
    if not qa or qa.get("refused"):
        st.info("请先在“课程答疑”中获得一条有课程证据支持的回答。")
    else:
        st.write("练习将围绕：" + "、".join(qa["knowledge_points"] or ["课程核心概念"]))
        if st.button("生成 3 道练习"):
            weak_topics = get_learning_profile(db)["weak_points"]
            st.session_state.exercise_items = [
                item.to_dict() for item in generate_exercises(
                    qa["answer"], qa["knowledge_points"], weak_topics
                )
            ]
            st.session_state.pop("last_grade", None)
        raw_items = st.session_state.get("exercise_items", [])
        if raw_items:
            items = [ExerciseItem(**item) for item in raw_items]
            with st.form("practice_form"):
                responses = []
                for index, item in enumerate(items, 1):
                    st.markdown(f"**{index}. [{item.item_type}] {item.question}**")
                    responses.append(st.radio("请选择", item.options, index=None, key=f"answer_{index}"))
                submitted = st.form_submit_button("提交练习", type="primary")
            if submitted:
                if any(response is None for response in responses):
                    st.warning("请完成全部题目后再提交。")
                else:
                    try:
                        grade = grade_exercises(items, responses)
                        points = sorted({p for item in items for p in item.knowledge_points})
                        db.save_attempt(qa["question_id"], grade.score, grade.total,
                                        grade.wrong_items, points, grade.records)
                        st.session_state.last_grade = grade
                    except Exception as exc:
                        st.error(f"保存练习结果失败：{exc}")
            if "last_grade" in st.session_state:
                grade = st.session_state.last_grade
                st.metric("本次成绩", f"{grade.score} 分",
                          help=f"答对 {grade.correct_count}/{grade.total} 道")
                for item in grade.records:
                    status = "🟢✅" if item["correct"] else "🔴❌"
                    with st.expander(f"{status} 第 {item['qid']} 题：{item['question']}", expanded=not item["correct"]):
                        st.write(f"你的作答：{item['student_answer']}")
                        st.write(f"正确答案：{item['answer']}")
                        st.info(f"解析：{item['explanation']}")
                if not grade.wrong_items:
                    st.success("全部回答正确！")
                else:
                    topic_summary = "；".join(
                        f"{point}：错 {stats['wrong']}/{stats['answered']}"
                        for point, stats in grade.topic_stats.items() if stats["wrong"]
                    )
                    st.warning("错题知识点分布：" + topic_summary)

with tab_profile:
    section_intro("03 · LEARNING PROFILE", "看见自己的学习轨迹",
                  "回顾最近提问、练习成绩与错题，持续识别薄弱知识点并获得下一轮练习建议。")
    try:
        profile = get_learning_profile(db)
        col1, col2, col3 = st.columns(3)
        col1.metric("最近提问", len(profile["questions"]))
        col2.metric("练习次数", len(profile["attempts"]))
        avg = sum(x["score"] for x in profile["attempts"]) / len(profile["attempts"]) if profile["attempts"] else 0
        col3.metric("平均成绩", f"{avg:.1f} 分")

        st.markdown("#### 最近提问")
        if profile["questions"]:
            st.dataframe([{"时间": x["created_at"], "问题": x["question"],
                           "状态": "已拒答" if x["refused"] else "已回答"} for x in profile["questions"]],
                         width="stretch", hide_index=True)
        else:
            st.caption("暂无提问记录")

        st.markdown("#### 练习成绩")
        if profile["attempts"]:
            st.dataframe([{"时间": x["created_at"], "成绩": x["score"], "题目数": x["total"]}
                          for x in profile["attempts"]], width="stretch", hide_index=True)
        else:
            st.caption("暂无练习记录")

        left, right = st.columns(2)
        with left:
            st.markdown("#### 错题本")
            wrong_items = [item for attempt in profile["attempts"] for item in attempt["wrong_items_json"]]
            if wrong_items:
                for item in wrong_items:
                    with st.expander(item["question"]):
                        st.write(f"你的答案：{item.get('student_answer', '未作答')}")
                        st.write(f"正确答案：{item['answer']}")
                        st.caption(item["explanation"])
            else:
                st.caption("暂无错题")
        with right:
            st.markdown("#### 薄弱知识点")
            if profile["weak_points"]:
                st.dataframe([{"知识点": x["knowledge_point"], "等级": x["level"],
                               "答题数": x["answered"], "正确率": f"{(x['correct']/x['answered']*100) if x['answered'] else 0:.1f}%",
                               "薄弱度": f"{x['weakness_score']:.2f}"} for x in profile["weak_points"]],
                             width="stretch", hide_index=True)
                recommendation = recommend_practice(profile)
                st.info(recommendation["message"])
            else:
                st.caption("暂无薄弱知识点")
        st.download_button("导出学习记录 CSV", export_csv_bytes(profile), "智教伴学_学习记录.csv", "text/csv")
    except Exception as exc:
        st.error(f"加载学习画像失败：{exc}")
