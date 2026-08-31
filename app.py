from __future__ import annotations

import base64
import html
import json

import streamlit as st
import streamlit.components.v1 as components

from agent_service import CampusAgentService
from auth_service import AuthService
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
def resources() -> tuple[LearningDatabase, CampusService, CampusAgentService, AuthService]:
    database = LearningDatabase(DB_PATH)
    service = CampusService(database)
    service.seed_demo(MATERIALS_DIR)
    return database, service, CampusAgentService(service), AuthService(database)


db, campus, agents, auth = resources()


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


def hero(display_name: str, course_count: int) -> None:
    safe_name = html.escape(display_name or "同学")
    st.markdown(f"""
    <section class="app-hero student-hero">
      <div class="hero-copy">
        <div class="eyebrow"><span></span> 课程学习空间</div>
        <h1>今天从哪一个问题开始？</h1>
        <p>嗨，{safe_name}。我们只根据课程资料一起思考，证据不足时会坦诚告诉你。</p>
      </div>
      <div class="hero-meta">
        <div class="meta-dot"></div>
        <div><strong>{course_count}</strong><small>门可学习课程</small></div>
      </div>
    </section>
    """, unsafe_allow_html=True)


def flashcard(block: dict) -> None:
    title = html.escape(block["title"])
    keywords = html.escape(" · ".join(block["keywords"]) or "待补充关键词")
    content = html.escape(block["content"]).replace("\n", "<br>")
    components.html(f"""
    <style>
      .scene{{perspective:900px;height:205px;margin:4px}} .card{{width:100%;height:100%;position:relative;
      transform-style:preserve-3d;transition:.5s;cursor:pointer}} .card.flip{{transform:rotateY(180deg)}}
      .face{{position:absolute;inset:0;backface-visibility:hidden;border:1px solid #b8cec5;border-radius:18px;
      padding:22px;box-sizing:border-box;background:#ffffff;color:#245c4f;overflow:auto}}
      .back{{transform:rotateY(180deg);background:#eef2ee}} h3{{margin:0 0 14px;color:#245c4f}}
      .keys{{color:#64736e;font-size:13px}} .hint{{position:absolute;bottom:12px;right:16px;color:#64736e;font-size:11px}}
    </style><div class="scene"><div class="card" onclick="this.classList.toggle('flip')">
      <div class="face"><h3>{title}</h3><div class="keys">{keywords}</div><div class="hint">点击翻转</div></div>
      <div class="face back">{content}<div class="hint">点击返回</div></div>
    </div></div>""", height=215)


def speech_player(text: str, speed: float, key: str) -> None:
    safe_text = json.dumps(text, ensure_ascii=False)
    components.html(f"""
    <button id="play_{key}" style="border:0;border-radius:10px;padding:9px 16px;background:#245c4f;color:#f6f7f5;cursor:pointer">
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
      <button id="start" style="border:0;border-radius:10px;padding:9px 16px;background:#c9ddd5;color:#245c4f;cursor:pointer">🎧 开启耳返</button>
      <button id="stop" style="border:0;border-radius:10px;padding:9px 16px;margin-left:8px;cursor:pointer">停止耳返</button>
      <span id="status" style="margin-left:10px;font-size:12px">请佩戴耳机，避免啸叫</span>
    </div><script>
    let stream,ctx,source;
    document.getElementById('start').onclick=async()=>{try{stream=await navigator.mediaDevices.getUserMedia({audio:true});
      ctx=new AudioContext();source=ctx.createMediaStreamSource(stream);source.connect(ctx.destination);
      document.getElementById('status').innerText='耳返运行中';}catch(e){document.getElementById('status').innerText='麦克风授权失败：'+e.message;}};
    document.getElementById('stop').onclick=()=>{if(stream)stream.getTracks().forEach(t=>t.stop());if(ctx)ctx.close();
      document.getElementById('status').innerText='耳返已停止';};</script>""", height=55)


def require_student_login() -> dict:
    user_id = st.session_state.get("student_user_id")
    if user_id:
        try:
            user = auth.get_user(str(user_id))
            if user.get("status") == "active" and user.get("role") == "student":
                return user
        except CampusError:
            pass
        st.session_state.pop("student_user_id", None)

    st.title("智教伴学")
    st.caption("请使用教师已导入的学生账号登录")
    with st.form("student_login"):
        username = st.text_input("学号或用户名")
        password = st.text_input("密码", type="password")
        submitted = st.form_submit_button("登录", type="primary", width="stretch")
    if submitted:
        try:
            user, _access, _refresh = auth.login(username, password, "streamlit")
            if user.get("role") != "student":
                st.error("该入口仅供学生使用，教师请打开教师端")
            else:
                st.session_state["student_user_id"] = user["user_id"]
                st.rerun()
        except CampusError as exc:
            st.error(str(exc))
    st.stop()


student_user = require_student_login()
user_id = str(student_user["user_id"])
if student_user.get("must_change_password"):
    st.warning("首次登录需要先修改初始密码")
    with st.form("student_change_password"):
        old_password = st.text_input("当前密码", type="password")
        new_password = st.text_input("新密码（至少 10 个字符）", type="password")
        change = st.form_submit_button("修改密码", type="primary")
    if change:
        try:
            auth.change_password(student_user, old_password, new_password)
            st.success("密码已修改，请继续学习")
            st.rerun()
        except CampusError as exc:
            st.error(str(exc))
    st.stop()


with st.sidebar:
    display_name = html.escape(str(student_user.get("display_name") or "同学"))
    username = html.escape(str(student_user.get("username") or ""))
    st.markdown(f'''<div class="side-brand"><span>课程学习</span><h3>学生学习空间</h3><p>按自己的节奏学习，答案回到课程资料核对。</p></div>
    <div class="student-account-card"><div class="account-avatar">{display_name[:1]}</div><div><strong>{display_name}</strong><small>{username}</small></div></div>''', unsafe_allow_html=True)
    if st.button("退出登录", icon=":material/logout:", width="stretch"):
        st.session_state.clear()
        st.rerun()
    ai_status = backend_provider_status()
    mode_label = {
        "mock": "确定性 Mock（无需配置）", "relay": "内置云中转",
        "custom": "自定义接口", "qwen": "管理员接口",
    }.get(str(ai_status["mode"]), "智能服务")
    st.success(f"当前 AI：{mode_label}")
    st.caption(f"{ai_status['provider']} · {ai_status['model']}")
    with st.expander("AI 服务设置"):
        current_ai = get_ai_settings()
        mode_options = ["确定性 Mock（无需配置）", "默认云端服务", "使用我自己的接口"]
        mode_indexes = {"mock": 0, "relay": 1, "custom": 2, "qwen": 2}
        selected_mode = st.radio(
            "调用方式",
            mode_options,
            index=mode_indexes.get(str(current_ai["mode"]), 0),
        )
        if selected_mode == "确定性 Mock（无需配置）":
            st.caption("不联网、不需要 Key；相同输入得到稳定结果，适合开箱运行、演示和测试。")
            if st.button("切换到确定性 Mock", width="stretch", disabled=current_ai["mode"] == "mock"):
                if run(lambda: (save_user_ai_settings("mock"), True)[1], "已切换到确定性 Mock"):
                    st.rerun()
        elif selected_mode == "默认云端服务":
            st.caption("使用项目提供的云端中转服务，真实模型 Key 不会下载到本机。")
            if st.button("切换并使用默认云端服务", width="stretch", disabled=current_ai["mode"] == "relay"):
                if run(lambda: (save_user_ai_settings("relay"), True)[1], "已切换到默认云端服务"):
                    st.rerun()
        else:
            provider_labels = {
                "自动识别（推荐）": "auto",
                "OpenAI 兼容接口": "openai_compatible",
                "Google Gemini 原生接口": "gemini",
                "本机 Ollama": "ollama",
            }
            current_provider = str(current_ai["provider"])
            default_provider_label = next(
                (label for label, value in provider_labels.items() if value == current_provider),
                "自动识别（推荐）",
            )
            custom_provider_label = st.selectbox(
                "接口协议",
                list(provider_labels),
                index=list(provider_labels).index(default_provider_label),
            )
            custom_base_url = st.text_input(
                "API Base URL",
                value=str(current_ai["base_url"]) if current_ai["mode"] == "custom" else "",
                placeholder="https://example.com/v1 或 http://127.0.0.1:11434/v1",
                help="可填写 OpenAI 兼容、Gemini 或 Ollama 地址；Ollama 允许使用本机内网地址。",
            )
            custom_model = st.text_input(
                "模型名称",
                value=str(current_ai["model"]) if current_ai["mode"] == "custom" else "qwen-plus",
            )
            custom_api_key = st.text_input(
                "API Key",
                type="password",
                help="仅保存到本机 user_ai.env；Ollama 可留空，其他接口必须填写。",
            )
            if st.button("保存并使用自定义接口", width="stretch"):
                if run(lambda: (
                    save_user_ai_settings(
                        "custom",
                        base_url=custom_base_url,
                        api_key=custom_api_key,
                        model=custom_model,
                        provider=provider_labels[custom_provider_label],
                    ),
                    True,
                )[1], "自定义 API 已保存"):
                    st.rerun()
    st.caption("公共部署场景不要开放本机 Key 配置；教师端接口配置请在教师工作台中完成。")
    st.caption("教师端使用独立登录入口和教师知识中心。")

courses = run(lambda: campus.list_courses(user_id, "student")) or []
hero(str(student_user.get("display_name") or student_user.get("username") or "同学"), len(courses))
if courses:
    labels = {x["course_id"]: f"{x['course_name']} · {'个人' if x['course_type']=='personal_course' else '共享'}" for x in courses}
    with st.container(border=True):
        st.markdown('<div class="course-strip-heading"><span class="section-kicker">当前课程</span><strong>选择今天要继续的学习空间</strong></div>', unsafe_allow_html=True)
        selected_id = st.selectbox("当前课程", list(labels), format_func=labels.get, label_visibility="collapsed")
        course = next(x for x in courses if x["course_id"] == selected_id)
        st.caption(course.get("description") or ("个人课程，资料只属于你。" if course["course_type"] == "personal_course" else "教师共享课程，学生可以使用资料但不能修改源文件。"))
else:
    selected_id = None; course = None
    with st.container(border=True):
        st.markdown('<div class="empty-learning"><span class="empty-icon">:material/menu_book:</span><strong>还没有已授权课程</strong><p>请联系任课教师导入名单，或者先创建一个属于自己的个人课程。</p></div>', unsafe_allow_html=True)

if course:
    summary_documents = invoke("document_status", selected_id) or []
    summary_blocks = invoke("knowledge_blocks_list", selected_id) or []
    with st.container(border=True):
        st.markdown('<div class="workspace-summary-heading"><span class="section-kicker">学习概览</span><span class="muted">数据仅来自当前课程</span></div>', unsafe_allow_html=True)
        summary_cols = st.columns(4)
        summary_cols[0].metric("课程资料", len(summary_documents))
        summary_cols[1].metric("知识卡片", len(summary_blocks))
        summary_cols[2].metric("学习方式", "按需")
        summary_cols[3].metric("证据原则", "可核对")
        st.caption("你可以先问一个问题，也可以先整理一段材料；每一步都可以暂停，学习不必一次完成。")

guided_sessions = st.session_state.setdefault("guided_qa_sessions", {})
previous_qa_course = st.session_state.get("guided_qa_active_course")
if previous_qa_course is not None and previous_qa_course != selected_id:
    guided_sessions.pop(previous_qa_course, None)
st.session_state["guided_qa_active_course"] = selected_id

tab_qa, tab_profile, tab_course, tab_blocks, tab_train, tab_check = st.tabs([
    ":material/forum: 先问一个问题", ":material/insights: 我的学习", ":material/menu_book: 课程与材料",
    ":material/auto_stories: 知识卡片", ":material/psychology: 训练巩固", ":material/task_alt: 作答与测验"
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
                        if result:
                            st.success(f"已保存并解析为 {result['chunk_count']} 个文字片段")
                with file_tab:
                    with st.form(f"document_upload_{selected_id}", clear_on_submit=True):
                        uploaded = st.file_uploader(
                            "上传 PDF 或 Word", type=["pdf", "docx"], key=f"student_docs_{selected_id}"
                        )
                        submit_document = st.form_submit_button("解析文档", type="primary")
                    if uploaded and submit_document:
                        result = invoke("student_document_upload", selected_id, {
                            "file_name": uploaded.name, "mime_type": uploaded.type or "application/octet-stream",
                            "content_base64": base64.b64encode(uploaded.getvalue()).decode("ascii"),
                        })
                        if result:
                            st.success(f"文档已解析为 {result['chunk_count']} 个后端文字片段")
                with image_tab:
                    with st.form(f"image_upload_{selected_id}", clear_on_submit=True):
                        image_file = st.file_uploader(
                            "上传带文字的图片", type=["png", "jpg", "jpeg", "webp"],
                            key=f"student_image_{selected_id}",
                        )
                        if image_file:
                            st.image(image_file, width=360)
                        submit_image = st.form_submit_button("调用当前视觉模型并保存文字", type="primary")
                    if image_file and submit_image:
                        result = invoke("image_text_extract", selected_id, {
                            "file_name": image_file.name, "mime_type": image_file.type,
                            "content_base64": base64.b64encode(image_file.getvalue()).decode("ascii"),
                        })
                        if result:
                            st.success("图片文字已提取并保存到课程后端")
                            st.text_area(
                                "提取结果", result["extracted_text"], height=180,
                                key=f"ocr_result_{selected_id}", disabled=True,
                            )
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
                            for key in ("cloze", "cloze_result", "memory_questions", "practice_grade", "qa_answer",
                                        "guided_qa_sessions", "guided_qa_active_course"):
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
    if course and course["course_type"] == "shared_course":
        st.markdown("#### 教师审核题库")
        st.caption("这里只展示教师从 Excel 题库导入、审核并发布的题目；教材例题不会自动进入正式题库。")
        bank_key = f"published_question_bank_{selected_id}"
        grade_key = f"published_question_grade_{selected_id}"
        folders_key = f"published_question_folders_{selected_id}"
        if st.button("刷新已发布试卷与作业", width="stretch"):
            st.session_state[folders_key] = invoke("quiz_generate", selected_id, {
                "source": "published_question_folders",
            }) or []
        publications = st.session_state.get(folders_key)
        if publications is None:
            publications = invoke("quiz_generate", selected_id, {
                "source": "published_question_folders",
            }) or []
            st.session_state[folders_key] = publications
        selected_publication = None
        if publications:
            publication_ids = [item["folder_id"] for item in publications]
            selected_folder_id = st.selectbox(
                "选择教师发布的试卷、作业或章节练习",
                publication_ids,
                format_func=lambda value: next(
                    f"{item['folder_name']}（{item['item_count']} 题）"
                    for item in publications if item["folder_id"] == value
                ),
            )
            selected_publication = next(
                item for item in publications if item["folder_id"] == selected_folder_id
            )
        if st.button("载入整份任务", type="primary", width="stretch",
                     disabled=not selected_publication):
            bank = invoke("quiz_generate", selected_id, {
                "source": "published_question_bank", "count": 100,
                "folder_id": selected_publication["folder_id"],
            })
            if bank is not None:
                st.session_state[bank_key] = bank
                st.session_state.pop(grade_key, None)
        bank = st.session_state.get(bank_key)
        if not bank:
            st.info("点击上方按钮载入教师已发布题库。")
        elif not bank.get("items"):
            st.info("当前课程还没有已发布的审核题目，请等待教师发布。")
        else:
            st.caption(
                f"题库版本 v{bank['version_number']} · 共 {bank['total']} 题"
                f" · 当前载入 {len(bank['items'])} 题"
            )
            with st.form(f"published_bank_form_{selected_id}"):
                responses = []
                for index, item in enumerate(bank["items"], 1):
                    st.markdown(f"**{index}. {item['question']}**")
                    option_rows = item.get("options", [])
                    option_labels = {
                        option["key"]: f"{option['key']}. {option['text']}"
                        for option in option_rows
                    }
                    if item["type"] == "multiple_choice":
                        responses.append(st.multiselect(
                            "请选择所有正确选项", list(option_labels),
                            format_func=option_labels.get,
                            key=f"published_multi_{selected_id}_{item['item_id']}",
                        ))
                    elif item["type"] == "true_false":
                        judge_options = [
                            str(option.get("text") or option.get("key") or "").strip()
                            for option in option_rows
                            if str(option.get("text") or option.get("key") or "").strip()
                        ]
                        if len(judge_options) < 2:
                            judge_options = ["T", "F"]
                        responses.append(st.radio(
                            "请选择", list(dict.fromkeys(judge_options)), index=None,
                            key=f"published_judge_{selected_id}_{item['item_id']}",
                        ))
                    elif item["type"] == "single_choice":
                        responses.append(st.radio(
                            "请选择", list(option_labels), index=None,
                            format_func=option_labels.get,
                            key=f"published_single_{selected_id}_{item['item_id']}",
                        ))
                    else:
                        responses.append(st.text_area(
                            "请输入答案",
                            key=f"published_text_{selected_id}_{item['item_id']}",
                        ))
                submit_published = st.form_submit_button("提交本次答案", type="primary")
            if submit_published:
                result = invoke("quiz_submit", selected_id, {
                    "version_id": bank["version_id"],
                    "items": bank["items"],
                    "responses": responses,
                })
                if result:
                    st.session_state[grade_key] = result
            grade = st.session_state.get(grade_key)
            if grade:
                st.metric("本次正确率", f"{grade['accuracy']:.1f}%")
                for index, result in enumerate(grade["results"], 1):
                    state = "✅" if result["correct"] else "❌"
                    with st.expander(f"{state} 第 {index} 题", expanded=not result["correct"]):
                        st.write("你的答案：", result.get("response"))
                        st.write("正确答案：", result.get("correct_answer"))
                        if result.get("explanation"):
                            st.info(result["explanation"])
    elif not course:
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
            if st.button("根据知识块生成练习题", disabled=not blocks, width="stretch"):
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
            if bank_file and st.button("解析并载入题库", type="primary", width="stretch"):
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
            st.space("small")
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
        st.caption("引导式答疑只使用当前课程资料。AI 会逐步追问，只有你明确请求时才给出答案。")
        session = guided_sessions.get(selected_id)
        material_partitions = course.get("material_partitions") or []
        retrieval_options = [("all", None, "全部资料"), *[
            ("material", item["material_type"], item.get("label") or item["material_type"])
            for item in material_partitions
        ]]
        session_choice = next((
            option for option in retrieval_options
            if session and option[0] == session.get("retrieval_scope", "all")
            and option[1] == session.get("material_type")
        ), retrieval_options[0])
        retrieval_choice = st.selectbox(
            "资料范围", retrieval_options,
            index=retrieval_options.index(session_choice),
            format_func=lambda option: option[2],
            disabled=session is not None,
            key=f"guided_qa_material_scope_{selected_id}",
            help="选择“全部资料”会跨已发布材料检索；选择课件或教材时只使用对应分区。",
        )
        retrieval_scope, selected_material_type, retrieval_label = retrieval_choice
        if session:
            st.caption(f"本轮固定检索范围：{session.get('retrieval_label', '全部资料')}")

        if session is None:
            with st.form(f"guided_qa_start_{selected_id}"):
                question = st.text_area(
                    "请输入课程问题",
                    placeholder="例如：为什么关系数据库需要规范化？",
                    key=f"guided_qa_question_{selected_id}",
                )
                start_guidance = st.form_submit_button(
                    "开始引导", type="primary",
                )
            if start_guidance and not question.strip():
                st.warning("请先输入一个课程问题。")
            elif start_guidance:
                result = invoke("course_qa", selected_id, {
                    "question": question.strip(),
                    "student_message": "",
                    "intent": "start",
                    "retrieval_scope": retrieval_scope,
                    "material_type": selected_material_type,
                })
                if result:
                    guided_sessions[selected_id] = {
                        "question": question.strip(),
                        "session_id": result.get("session_id"),
                        "phase": result["phase"],
                        "can_reveal": result.get("can_reveal", False),
                        "completed": result["completed"],
                        "refused": result["refused"],
                        "question_id": result.get("question_id"),
                        "sources": result.get("sources", []),
                        "retrieval_scope": retrieval_scope,
                        "material_type": selected_material_type,
                        "retrieval_label": retrieval_label,
                        "messages": [
                            {"role": "user", "content": question.strip()},
                            {"role": "assistant", "content": result["reply"]},
                        ],
                    }
                    st.rerun()
        else:
            st.caption(f"当前问题：{session['question']}")
            for message in session["messages"]:
                with st.chat_message(message["role"]):
                    st.write(message["content"])

            if not session["completed"]:
                with st.form(f"guided_qa_response_{selected_id}", clear_on_submit=True):
                    student_response = st.text_area(
                        "说说你的想法",
                        placeholder="写下你目前想到的步骤或判断……",
                        key=f"guided_qa_response_text_{selected_id}",
                    )
                    submit_response = st.form_submit_button(
                        "提交想法", type="primary",
                    )

                with st.container(horizontal=True):
                    ask_hint = st.button(
                        "给我一点提示", icon=":material/lightbulb:",
                        key=f"guided_qa_hint_{selected_id}",
                    )
                    reveal_answer = st.button(
                        "查看课程答案", icon=":material/visibility:",
                        key=f"guided_qa_reveal_{selected_id}",
                        disabled=not session.get("can_reveal", False),
                    )
                    end_question = st.button(
                        "结束本题", icon=":material/stop_circle:",
                        key=f"guided_qa_end_{selected_id}",
                    )

                intent = ""
                current_message = ""
                if submit_response and not student_response.strip():
                    st.warning("请先写下你的想法，或者使用提示和答案按钮。")
                elif submit_response:
                    intent, current_message = "respond", student_response.strip()
                elif ask_hint:
                    intent, current_message = "hint", "我暂时没有思路，请给我一点提示。"
                elif reveal_answer:
                    intent, current_message = "reveal", "请基于课程资料给出答案。"
                elif end_question:
                    intent, current_message = "end", "结束本题。"

                if intent:
                    result = invoke("course_qa", selected_id, {
                        "question": session["question"],
                        "student_message": current_message,
                        "intent": intent,
                        "session_id": session.get("session_id"),
                    })
                    if result:
                        session["messages"].extend([
                            {"role": "user", "content": current_message},
                            {"role": "assistant", "content": result["reply"]},
                        ])
                        session.update({
                            "phase": result["phase"],
                            "can_reveal": result.get("can_reveal", False),
                            "completed": result["completed"],
                            "refused": result["refused"],
                            "question_id": result.get("question_id"),
                            "sources": result.get("sources", []),
                        })
                        guided_sessions[selected_id] = session
                        st.rerun()
            else:
                if session["phase"] == "revealed" and not session["refused"]:
                    st.success("答案已根据当前课程证据生成，可继续到练习模块巩固。")
                    for index, source in enumerate(session["sources"], 1):
                        with st.expander(f"证据 #{index} · {source['source_file']} · {source['section']}"):
                            st.write(source["text"][:MAX_EVIDENCE_CHARS])
                elif session["refused"]:
                    st.warning("当前课程资料不足，本次没有生成答案。")
                else:
                    st.info("本题已结束，中间引导内容未写入学习记录。")

                if st.button(
                    "开始新问题", icon=":material/restart_alt:",
                    key=f"guided_qa_restart_{selected_id}",
                ):
                    guided_sessions.pop(selected_id, None)
                    st.session_state.pop(f"guided_qa_question_{selected_id}", None)
                    st.session_state.pop(f"guided_qa_response_text_{selected_id}", None)
                    st.rerun()
