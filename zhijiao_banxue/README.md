# 智教伴学

“智教伴学”是一个可本地运行的高校学生课程伴学 MVP。它使用 Streamlit、SQLite 和本地 Markdown 资料，通过 TF-IDF 与关键词混合检索提供有出处的课程答疑，并围绕答疑知识点生成练习和学习画像。

## 功能

- 课程答疑：只依据本地资料作答，以 `[证据 #n]` 对齐文件、章节和原文片段；证据不足时给出检索范围与改问建议。
- 个性化练习：出题阶段不泄露答案；提交后逐题显示作答、答案和解析，并按知识点统计错题。
- 我的学习：综合累计正确率与最近 10 题表现计算薄弱度，提供“数据不足/强/中/弱”等级和下一轮练习建议，并导出 UTF-8 CSV。
- LLMProvider：默认 MockProvider 可完全本地演示，也支持任意 OpenAI 兼容的 `/chat/completions` 接口。

## 项目结构

```text
zhijiao_banxue/
├── app.py
├── config.py
├── database.py
├── llm_provider.py
├── course_materials/
│   ├── 人工智能基础.md
│   └── 数据伦理与安全.md
├── data/                       # 首次运行后生成 learning.db
├── skills/
│   ├── retrieval/service.py    # TF-IDF + 关键词检索
│   ├── qa/service.py           # 证据门槛与问答
│   ├── exercise/service.py     # 练习生成与判分
│   └── profile/service.py      # 学习画像与 CSV
└── tests/test_core.py
```

## 本地运行

建议使用 Python 3.10 或更高版本：

```powershell
cd zhijiao_banxue
python -m venv .venv
.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
streamlit run app.py
```

打开终端显示的本地地址即可。默认选择 `MockProvider（本地演示）`，无需 API Key 或网络连接。

如果选择“OpenAI 兼容接口”，请在侧栏填写 API Key、Base URL（通常以 `/v1` 结尾）和模型名称。密钥只保存在当前 Streamlit 会话内，不写入数据库。

## 添加课程资料

将 UTF-8 编码的 `.md` 文件放入 `course_materials`。Markdown 标题会被识别为章节，章节正文会成为检索片段。修改资料后重启应用或清除 Streamlit 缓存以重建索引。

## 数据与边界

- SQLite 文件位于 `data/learning.db`。
- 画像薄弱度公式为 `0.6 × (1 - 累计正确率) + 0.4 × (1 - 最近正确率)`；少于 3 题只标记“数据不足”。
- 本 MVP 没有真实登录，不应录入真实身份信息。
- MockProvider 用于展示完整产品流程，其文本组织能力有限；生产用途可切换到兼容接口。
- 练习题由当前答疑内容生成，第一版采用可解释的本地模板。

## 测试

```powershell
python -m pytest -q
```
