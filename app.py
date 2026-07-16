from __future__ import annotations

import base64
import html
import json

import streamlit as st
import streamlit.components.v1 as components

from agent_service import CampusAgentService
from campus_service import CampusError, CampusService
from config import (
    DB_PATH,
    MATERIALS_DIR,
    MAX_EVIDENCE_CHARS,
    get_ai_settings,
    save_user_ai_settings,
)
from database import LearningDatabase
from llm_provider import backend_provider_status
from ui import inject_theme

st.set_page_config(page_title="智教伴学 · 学生端", page_icon="📘", layout="wide")
inject_theme()


@st.cache_resource
def resources() -> tuple[LearningDatabase, CampusService, CampusAgentService]:
    database = LearningDatabase(DB_PATH)
    service = CampusService(database)
    service.seed_demo(MATERIALS_DIR)
    return database, service, CampusAgentService(service)


db, campus, agents = resources()


def run(action, success: str | None = None):
    try:
        value = action()
        if success:
            st.success(success)
        return value
    except CampusError as exc:
        st.error(str(exc))
    except Exception as exc:
        st.error(f"操作失败：{exc}")
    return None


def invoke(action: str, course_id: str | None = None, input_data: dict | None = None):
    response = agents.invoke({
        "request_id": f"ui_{action}", "agent": "student_assistant", "action": action,
        "actor": {"user_id": user_id, "role": "student"},
        "scope": {"course_id": course_id} if course_id else {}, "input": input_data or {},
        "context": {"source": "streamlit", "language": "zh-CN"},
    })
    if response.status != "success":
        st.error(response.message or "学生智能体调用失败")
        return None
    return response.data


def hero() -> None:
    st.markdown("""
    <section class="app-hero"><div class="hero-copy">
      <div class="eyebrow"><span></span> STUDENT MEMORY COPILOT</div>
      <h1>智教<span>伴学</span></h1>
    </div><div class="hero-meta"><div class="meta-dot"></div>
      <div><strong>学生端</strong><small>教师端暂未开发</small></div>
    </div></section>
    """, unsafe_allow_html=True)


def flashcard(block: dict) -> None:
    title = html.escape(block["title"])
    keywords = html.escape(" · ".join(block["keywords"]) or "待补充关键词")
    content = html.escape(block["content"]).replace("\n", "<br>")
    components.html(f"""
    <style>
      .scene{{perspective:900px;height:205px;margin:4px}} .card{{width:100%;height:100%;position:relative;
      transform-style:preserve-3d;transition:.5s;cursor:pointer}} .card.flip{{transform:rotateY(180deg)}}
      .face{{position:absolute;inset:0;backface-visibility:hidden;border:1px solid #acc3e5;border-radius:18px;
      padding:22px;box-sizing:border-box;background:#ffffff;color:#355aa4;overflow:auto}}
      .back{{transform:rotateY(180deg);background:#d9ebf2}} h3{{margin:0 0 14px;color:#355aa4}}
      .keys{{color:#7a939f;font-size:13px}} .hint{{position:absolute;bottom:12px;right:16px;color:#7a939f;font-size:11px}}
    </style><div class="scene"><div class="card" onclick="this.classList.toggle('flip')">
      <div class="face"><h3>{title}</h3><div class="keys">{keywords}</div><div class="hint">点击翻转</div></div>
      <div class="face back">{content}<div class="hint">点击返回</div></div>
    </div></div>""", height=215)


def speech_player(text: str, speed: float, key: str) -> None:
    safe_text = json.dumps(text, ensure_ascii=False)
    components.html(f"""
    <button id="play_{key}" style="border:0;border-radius:10px;padding:9px 16px;background:#355aa4;color:#ece3ef;cursor:pointer">
      ▶ 真人发音朗读（{speed}×）</button>
    <button id="stop_{key}" style="border:0;border-radius:10px;padding:9px 16px;margin-left:8px;cursor:pointer">停止</button>
    <script>
    document.getElementById('play_{key}').onclick=()=>{{speechSynthesis.cancel();const u=new SpeechSynthesisUtterance({safe_text});
      u.lang='zh-CN';u.rate={speed};speechSynthesis.speak(u);}};
    document.getElementById('stop_{key}').onclick=()=>speechSynthesis.cancel();
    </script>""", height=55)


def live_monitor() -> None:
    components.html("""
    <div style="font-family:sans-serif;color:#4f5652">
      <button id="start" style="border:0;border-radius:10px;padding:9px 16px;background:#a2d1e6;color:#355aa4;cursor:pointer">🎧 开启耳返</button>
      <button id="stop" style="border:0;border-radius:10px;padding:9px 16px;margin-left:8px;cursor:pointer">停止耳返</button>
      <span id="status" style="margin-left:10px;font-size:12px">请佩戴耳机，避免啸叫</span>
    </div><script>
    let stream,ctx,source;
    document.getElementById('start').onclick=async()=>{try{stream=await navigator.mediaDevices.getUserMedia({audio:true});
      ctx=new AudioContext();source=ctx.createMediaStreamSource(stream);source.connect(ctx.destination);
      document.getElementById('status').innerText='耳返运行中';}catch(e){document.getElementById('status').innerText='麦克风授权失败：'+e.message;}};
    document.getElementById('stop').onclick=()=>{if(stream)stream.getTracks().forEach(t=>t.stop());if(ctx)ctx.close();
      document.getElementById('status').innerText='耳返已停止';};</script>""", height=55)


with st.sidebar:
    st.markdown('<div class="side-brand"><span>STUDENT ONLY</span><h3>学生学习空间</h3><p>轻量 Skill · 千问智能体 · 私有课程</p></div>', unsafe_allow_html=True)
    user_id = st.text_input("学生 ID", value="demo_student_001").strip() or "demo_student_001"
    ai_status = backend_provider_status()
    if ai_status["configured"]:
        mode_label = {
            "relay": "默认云端服务",
            "custom": "自定义接口",
            "qwen": "本机管理员配置",
        }.get(str(ai_status["mode"]), "智能接口")
        st.success(f"智能服务已连接：{mode_label}")
    else:
        st.error("智能服务尚未完成配置")
    st.caption(f"{ai_status['provider']} · {ai_status['model']}")
    with st.expander("AI 服务设置"):
        current_ai = get_ai_settings()
        selected_mode = st.radio(
            "调用方式",
            ["默认云端服务", "使用我自己的接口"],
            index=0 if current_ai["mode"] == "relay" else 1,
            help="默认云端服务不会把千问 API Key 下载到本机。",
        )
        if selected_mode == "默认云端服务":
            st.caption("调用项目维护者部署的中转服务；真实千问 Key 只保存在云服务器。")
            if st.button("切换到默认云端服务", use_container_width=True):
                if run(lambda: (save_user_ai_settings("relay"), True)[1],
                       "已切换到默认云端服务"):
                    st.rerun()
        else:
            with st.form("custom_ai_settings"):
                custom_base_url = st.text_input(
                    "OpenAI 兼容 Base URL",
                    value=str(current_ai["base_url"]) if current_ai["mode"] == "custom" else "",
                    placeholder="https://example.com/v1",
                )
                custom_model = st.text_input(
                    "模型名称",
                    value=str(current_ai["model"]) if current_ai["mode"] == "custom" else "qwen-plus",
                )
                custom_api_key = st.text_input(
                    "API Key",
                    type="password",
                    help="仅保存到本机 user_ai.env，该文件已被 Git 排除。",
                )
                save_custom = st.form_submit_button("保存并使用自定义接口")
            if save_custom:
                if run(lambda: (
                    save_user_ai_settings(
                        "custom",
                        base_url=custom_base_url,
                        api_key=custom_api_key,
                        model=custom_model,
                    ),
                    True,
                )[1], "自定义接口已保存"):
                    st.rerun()
        st.caption("公共部署场景不要开放本机 Key 配置；此入口用于用户自行下载运行。")
    st.caption("教师端已按当前阶段要求禁用")

hero()
courses = run(lambda: campus.list_courses(user_id, "student")) or []
if courses:
    labels = {x["course_id"]: f"{x['course_name']} · {'个人' if x['course_type']=='personal_course' else '共享'}" for x in courses}
    selected_id = st.selectbox("当前学习课程", list(labels), format_func=labels.get)
    course = next(x for x in courses if x["course_id"] == selected_id)
else:
    selected_id = None; course = None

tab_course, tab_blocks, tab_train, tab_check, tab_profile, tab_qa = st.tabs([
    "① 我的课程与材料", "② 智能知识块", "③ 多模式训练", "④ AI 智能出题",
    "⑤ 个人画像与统计", "个性化答疑"
])

with tab_course:
    left, right = st.columns([1, 2])
    with left:
        st.subheader("创建个人课程")
        with st.form("create_personal"):
            new_name = st.text_input("课程名称", placeholder="例如：细胞生物学背诵")
            new_desc = st.text_area("课程说明")
            create = st.form_submit_button("创建并开始整理", type="primary")
        if create:
            if run(lambda: campus.create_course(new_name, "personal_course", user_id, "student", new_desc), "个人课程已创建"):
                st.rerun()
    with right:
        if not course:
            st.info("请先创建个人课程。")
        else:
            st.subheader(f"材料整理 · {course['course_name']}")
            if course["course_type"] == "personal_course":
                input_tab, file_tab, image_tab = st.tabs(["文本输入", "PDF / Word", "图片提取"])
                with input_tab:
                    text_name = st.text_input("材料名称", "剪贴板材料")
                    pasted = st.text_area("粘贴或手动输入学习材料", height=220)
                    if st.button("保存为课程材料", disabled=not pasted.strip(), type="primary"):
                        result = invoke("student_document_upload", selected_id, {
                            "file_name": f"{text_name or '文本材料'}.txt", "mime_type": "text/plain",
                            "content_base64": base64.b64encode(pasted.encode("utf-8")).decode("ascii"),
                        })
                        if result: st.success(f"已保存并解析为 {result['chunk_count']} 个文字片段"); st.rerun()
                with file_tab:
                    uploaded = st.file_uploader("上传 PDF 或 Word", type=["pdf", "docx"], key="student_docs")
                    if uploaded and st.button("解析文档", type="primary"):
                        result = invoke("student_document_upload", selected_id, {
                            "file_name": uploaded.name, "mime_type": uploaded.type or "application/octet-stream",
                            "content_base64": base64.b64encode(uploaded.getvalue()).decode("ascii"),
                        })
                        if result: st.success(f"文档已解析为 {result['chunk_count']} 个后端文字片段"); st.rerun()
                with image_tab:
                    image_file = st.file_uploader("上传带文字的图片", type=["png", "jpg", "jpeg", "webp"], key="student_image")
                    if image_file:
                        st.image(image_file, width=360)
                        if st.button("调用千问 OCR 并保存文字", type="primary"):
                            result = invoke("image_text_extract", selected_id, {
                                "file_name": image_file.name, "mime_type": image_file.type,
                                "content_base64": base64.b64encode(image_file.getvalue()).decode("ascii"),
                            })
                            if result: st.success("图片文字已提取并保存到课程后端"); st.text_area("提取结果", result["extracted_text"], height=180); st.rerun()
            else:
                st.info("这是教师共享课程，学生可使用资料，但不能修改源文件。")
            documents = invoke("document_status", selected_id) or []
            st.markdown("#### 已解析材料")
            for doc in documents:
                with st.expander(f"{doc['original_name']} · {doc['chunk_count']} 个文字片段 · {doc['status']}"):
                    st.write(doc.get("text_preview") or "暂无文字预览")
                    if course["course_type"] == "personal_course":
                        if st.button("删除材料", key=f"delete_{doc['document_id']}"):
                            if invoke("student_document_delete", selected_id, {"document_id": doc["document_id"]}): st.rerun()
            if course["course_type"] == "personal_course":
                with st.expander("删除这个个人课程"):
                    st.warning("删除后，课程资料、知识块、背诵本和练习记录将一并删除，无法恢复。")
                    delete_confirm = st.text_input("输入课程名称以确认", key="delete_course_confirm")
                    if st.button("永久删除课程", type="primary", disabled=delete_confirm != course["course_name"]):
                        if invoke("personal_course_delete", selected_id):
                            for key in ("cloze", "cloze_result", "memory_questions", "practice_grade", "qa_answer"):
                                st.session_state.pop(key, None)
                            st.success("个人课程已删除")
                            st.rerun()

with tab_blocks:
    if not course:
        st.info("请先选择课程。")
    else:
        documents = invoke("document_status", selected_id) or []
        doc_options = {x["document_id"]: x["original_name"] for x in documents}
        if doc_options:
            build_doc = st.selectbox("选择要智能分块的材料", list(doc_options), format_func=doc_options.get)
            if st.button("AI 语义分块并提炼标题", type="primary"):
                result = invoke("knowledge_blocks_build", selected_id, {"document_id": build_doc})
                if result: st.success(f"已生成 {len(result)} 个知识块"); st.rerun()
        else:
            st.info("请先导入至少一份材料。")
        blocks = invoke("knowledge_blocks_list", selected_id) or []
        if blocks:
            st.markdown("#### 记忆卡片（点击翻转）")
            for start in range(0, len(blocks), 2):
                cols = st.columns(2)
                for col, block in zip(cols, blocks[start:start+2]):
                    with col: flashcard(block)
            st.markdown("#### 手动调整知识块")
            chosen_id = st.selectbox("选择知识块", [x["block_id"] for x in blocks],
                                     format_func=lambda x: next(b["title"] for b in blocks if b["block_id"] == x))
            chosen = next(x for x in blocks if x["block_id"] == chosen_id)
            with st.form("edit_block"):
                edit_title = st.text_input("记忆线索标题", chosen["title"])
                edit_keywords = st.text_input("关键词（用逗号分隔）", ",".join(chosen["keywords"]))
                edit_content = st.text_area("完整内容", chosen["content"], height=220)
                favorite = st.checkbox("收藏为难背知识块", bool(chosen["is_favorite"]))
                save_block = st.form_submit_button("保存调整")
            if save_block:
                result = invoke("knowledge_block_update", selected_id, {
                    "block_id": chosen_id, "title": edit_title,
                    "keywords": [x.strip() for x in edit_keywords.replace("，", ",").split(",") if x.strip()],
                    "content": edit_content, "favorite": favorite,
                })
                if result: st.success("知识块已更新"); st.rerun()
            c1, c2 = st.columns(2)
            split_at = c1.number_input("按字符位置拆分", min_value=20, max_value=max(20, len(chosen["content"])-20),
                                       value=max(20, len(chosen["content"])//2))
            if c1.button("拆成两个知识块"):
                if invoke("knowledge_block_split", selected_id, {"block_id": chosen_id, "position": int(split_at)}): st.rerun()
            if c2.button("与下一个知识块合并"):
                if invoke("knowledge_block_merge", selected_id, {"block_id": chosen_id}): st.rerun()

with tab_train:
    if not course:
        st.info("请先选择课程。")
    else:
        blocks = invoke("knowledge_blocks_list", selected_id) or []
        if not blocks:
            st.info("请先在“智能知识块”中生成卡片。")
        else:
            block_id = st.selectbox("训练知识块", [x["block_id"] for x in blocks], key="train_block",
                                    format_func=lambda x: next(b["title"] for b in blocks if b["block_id"] == x))
            block = next(x for x in blocks if x["block_id"] == block_id)
            mode_cloze, mode_audio, mode_shadow = st.tabs(["关键词挖空", "听觉强化", "耳返跟读"])
            with mode_cloze:
                extras = st.text_input("手动追加重点词（逗号分隔）")
                if st.button("生成挖空", type="primary"):
                    st.session_state.cloze = invoke("cloze_generate", selected_id, {
                        "block_id": block_id,
                        "extra_keywords": [x.strip() for x in extras.replace("，", ",").split(",") if x.strip()],
                    })
                    st.session_state.pop("cloze_result", None)
                cloze = st.session_state.get("cloze")
                if cloze:
                    st.markdown(f"### {cloze['title']}")
                    rendered = ""
                    for segment in cloze["segments"]:
                        rendered += segment["value"] if segment["type"] == "text" else f" **[第 {segment['index']} 空]** "
                    st.markdown(rendered)
                    with st.form(f"cloze_form_{block_id}"):
                        responses = []
                        cols = st.columns(3)
                        for index in range(cloze["blank_count"]):
                            with cols[index % 3]:
                                responses.append(st.text_input(f"第 {index+1} 空", key=f"cloze_answer_{block_id}_{index}"))
                        submit_cloze = st.form_submit_button("提交并检测挖空正确率", type="primary")
                    if submit_cloze:
                        result = invoke("cloze_submit", selected_id, {
                            "block_id":block_id, "extra_keywords":cloze.get("extra_keywords", []), "responses":responses,
                        })
                        if result: st.session_state.cloze_result = result
                    result = st.session_state.get("cloze_result")
                    if result:
                        st.metric("挖空正确率", f"{result['score']:.1f}%",
                                  help=f"答对 {result['correct_count']}/{result['total']} 空")
                        for item in result["corrections"]:
                            if item["correct"]:
                                st.success(f"第 {item['index']} 空：{item['response']} ✓")
                            else:
                                st.error(f"第 {item['index']} 空：填写“{item['response'] or '未填写'}”，正确答案“{item['correct_answer']}”")
                        st.caption("错误和缺失内容已自动加入个人背诵本。")
            with mode_audio:
                speed = st.select_slider("播放速度", options=[0.75, 1.0, 1.25, 1.5, 2.0], value=1.0)
                speech_player(block["content"], speed, str(block_id))
                st.caption("收藏难背知识块后，可在知识块列表中优先复习。")
            with mode_shadow:
                st.warning("请先佩戴耳机再开启耳返，避免扬声器啸叫。")
                live_monitor()
                recording = st.audio_input("也可以录制一段跟读并立即回放", key=f"record_{block_id}")
                if recording: st.audio(recording)

with tab_check:
    if not course:
        st.info("请先选择课程。")
    else:
        st.markdown("#### AI 智能出题与作答")
        blocks = invoke("knowledge_blocks_list", selected_id) or []
        generate_panel, import_panel = st.columns(2)
        with generate_panel:
            st.markdown("##### 根据课程材料生成")
            question_count = st.slider("题目数量", 3, 12, 6)
            if not blocks:
                st.caption("生成练习题前，请先在“智能知识块”中完成材料分块。")
            if st.button("根据知识块生成练习题", disabled=not blocks, use_container_width=True):
                questions = invoke("memory_questions_generate", selected_id, {"count": question_count})
                if questions:
                    st.session_state.memory_questions = questions
                    st.session_state.pop("practice_grade", None)
        with import_panel:
            st.markdown("##### 导入已有题库")
            bank_file = st.file_uploader(
                "导入题库和答案",
                type=["pdf", "docx", "txt", "xlsx", "xls"],
                key="question_bank_file",
                help="支持 PDF、Word、TXT 和 Excel（XLSX）。旧版 XLS 请先另存为 XLSX。",
            )
            if bank_file and st.button("解析并载入题库", type="primary", use_container_width=True):
                imported = invoke("question_bank_import", selected_id, {
                    "file_name":bank_file.name,
                    "mime_type":bank_file.type or "application/octet-stream",
                    "content_base64":base64.b64encode(bank_file.getvalue()).decode("ascii"),
                })
                if imported:
                    st.session_state.memory_questions = imported
                    st.session_state.pop("practice_grade", None)
                    missing_answers = sum(not item.get("answer") for item in imported)
                    st.success(f"已载入 {len(imported)} 道题，其中 {missing_answers} 道无标准答案，将在提交后由 AI 判题。")

        questions = st.session_state.get("memory_questions", [])
        if questions:
            st.divider()
            st.caption(f"当前题组共 {len(questions)} 题。标记“AI 判题”的题目表示原题库未提供答案。")
            with st.form("ai_practice_form"):
                responses = []
                for index, item in enumerate(questions, 1):
                    judge_note = " · AI 判题" if not item.get("answer") else ""
                    st.markdown(f"**{index}. [{item['type']}{judge_note}] {item['question']}**")
                    item_type = item["type"]
                    compact_type = str(item_type).lower().replace(" ", "").replace("/", "")
                    is_judgment = any(
                        token in compact_type for token in ("判断", "是非", "对错", "truefalse", "boolean")
                    )
                    if "多选" in item_type:
                        responses.append(st.multiselect("请选择所有正确选项", item.get("options", []), key=f"multi_{index}"))
                    elif "单选" in item_type or is_judgment or ("选择" in item_type and "多" not in item_type):
                        display_options = item.get("options", [])
                        if is_judgment and not display_options:
                            display_options = ["正确", "错误"]
                        responses.append(st.radio("请选择", display_options, index=None, key=f"single_{index}"))
                    else:
                        responses.append(st.text_area("请输入简答内容", key=f"short_{index}", height=90))
                submit_practice = st.form_submit_button("提交全部答案并由 AI 批改", type="primary")
            if submit_practice:
                grade = invoke("memory_questions_submit", selected_id, {"questions":questions,"responses":responses})
                if grade: st.session_state.practice_grade = grade
            grade = st.session_state.get("practice_grade")
            if grade:
                st.metric("本次练习正确率", f"{grade['score']:.1f}%")
                for item in grade.get("results", []):
                    status = "✅" if item.get("correct") else "❌"
                    with st.expander(f"{status} 第 {item.get('index')} 题", expanded=not item.get("correct")):
                        st.write(f"正确答案：{item.get('correct_answer', '')}")
                        st.info(item.get("feedback", ""))
                if grade.get("weak_points"): st.warning("薄弱点：" + "、".join(map(str,grade["weak_points"])))
                if grade.get("summary"): st.info(grade["summary"])
            exported = invoke("memory_workbook_export", selected_id, {"course_name": course["course_name"], "questions": questions})
            if exported:
                st.download_button("导出 Word 练习册", base64.b64decode(exported["content_base64"]),
                                   f"{course['course_name']}_练习册.docx",
                                   "application/vnd.openxmlformats-officedocument.wordprocessingml.document")

with tab_profile:
    if not course:
        st.info("请先选择课程。")
    else:
        dashboard = invoke("student_dashboard", selected_id)
        if dashboard:
            st.subheader(f"{course['course_name']} · 个人学习画像")
            c1,c2,c3,c4,c5 = st.columns(5)
            c1.metric("我的课程", dashboard["course_count"])
            c2.metric("课程资料", dashboard["document_count"])
            c3.metric("知识块", dashboard["block_count"])
            c4.metric("背诵平均", f"{dashboard['memory_average']:.1f}%")
            c5.metric("练习平均", f"{dashboard['practice_average']:.1f}%")
            left,right = st.columns(2)
            with left:
                st.markdown("#### 薄弱知识统计")
                if dashboard["weak_points"]:
                    st.bar_chart({x["point"]:x["count"] for x in dashboard["weak_points"]})
                    st.dataframe(dashboard["weak_points"], hide_index=True, width="stretch")
                else: st.caption("暂无薄弱点，先完成一次挖空或练习。")
            with right:
                st.markdown("#### 最近学习成绩")
                history = ([{"类型":"挖空/背诵","时间":x["created_at"],"成绩":x["score"]} for x in dashboard["memory_attempts"]] +
                           [{"类型":"AI练习","时间":x["created_at"],"成绩":x["score"]} for x in dashboard["practice_attempts"]])
                history.sort(key=lambda x:x["时间"], reverse=True)
                if history: st.dataframe(history[:20], hide_index=True, width="stretch")
                else: st.caption("暂无学习记录。")
            st.markdown("#### 我的背诵本")
            export_left, export_right = st.columns(2)
            recitation_export = invoke("recitation_book_export", selected_id, {"course_name":course["course_name"]})
            wrong_export = invoke("wrong_question_book_export", selected_id, {"course_name":course["course_name"]})
            if recitation_export:
                export_left.download_button("导出 Word 背诵本", base64.b64decode(recitation_export["content_base64"]),
                                            f"{course['course_name']}_个人背诵本.docx",
                                            "application/vnd.openxmlformats-officedocument.wordprocessingml.document")
            if wrong_export:
                export_right.download_button("导出 Word 错题本", base64.b64decode(wrong_export["content_base64"]),
                                             f"{course['course_name']}_个人错题本.docx",
                                             "application/vnd.openxmlformats-officedocument.wordprocessingml.document")
            if dashboard["recitation_book"]:
                for item in dashboard["recitation_book"]:
                    with st.expander(f"{item.get('title') or '知识块'} · {item['mode']} · {item['score']:.1f}% · {item['created_at']}"):
                        if item["missing_points"]: st.write("缺失：", "；".join(item["missing_points"]))
                        if item["error_points"]: st.write("错误：", "；".join(item["error_points"]))
                        st.info(item["feedback"])
            else: st.caption("暂无错背记录。挖空错误会自动进入这里。")
            st.markdown("#### 我的错题本")
            if dashboard["wrong_question_book"]:
                for item in dashboard["wrong_question_book"]:
                    with st.expander(f"[{item['type']}] {item['question']} · {item['created_at']}"):
                        answer = item["student_answer"]
                        correct = item["correct_answer"]
                        st.write("我的答案：", "、".join(map(str,answer)) if isinstance(answer,list) else (answer or "未作答"))
                        st.write("正确答案：", "、".join(map(str,correct)) if isinstance(correct,list) else correct)
                        st.caption("知识点：" + (item["knowledge_point"] or "未标注"))
                        st.info(item["feedback"])
            else: st.caption("暂无错题。AI 练习中的错误会自动进入这里。")

with tab_qa:
    if not course:
        st.info("请先选择课程。")
    else:
        st.caption("课后答疑只检索当前课程资料，回答必须附带文件和章节证据。")
        question = st.text_area("请输入课程问题", placeholder="例如：合同法的三项基本原则分别是什么？")
        if st.button("向学生智能体提问", type="primary", disabled=not question.strip()):
            answer = invoke("course_qa", selected_id, {"question": question})
            if answer: st.session_state.qa_answer = {**answer, "course_id": selected_id}
        answer = st.session_state.get("qa_answer")
        if answer and answer.get("course_id") == selected_id:
            (st.warning if answer["refused"] else st.success)(answer["answer"])
            for index, source in enumerate(answer["sources"], 1):
                with st.expander(f"证据 #{index} · {source['source_file']} · {source['section']}"):
                    st.write(source["text"][:MAX_EVIDENCE_CHARS])
