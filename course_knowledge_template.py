from __future__ import annotations

from pydantic import BaseModel, Field


class KnowledgePoint(BaseModel):
    """教材中可独立学习的最小知识单元；必须由原文块和逐字证据支撑。"""

    model_config = {"is_entity": True, "graph_id_fields": ["title"]}

    title: str = Field(min_length=1, description="简洁、准确的知识点名称")
    keywords: list[str] = Field(default_factory=list, description="用于检索的核心关键词")
    source_block_ids: list[str] = Field(
        min_length=1,
        description="支撑该知识点的 ZHIJIAO_BLOCK 标识；只能逐字复制输入中的标识",
    )
    evidence_quotes: list[str] = Field(
        min_length=1,
        description="从上述原文块逐字复制的短证据，不得改写或概括",
    )


class Section(BaseModel):
    """章内语义连贯的分节；原文没有规范标题时允许生成简洁标题。"""

    model_config = {"is_entity": True, "graph_id_fields": ["title"]}

    title: str = Field(min_length=1, description="保留原文标题；无标题时按语义生成")
    knowledge_points: list[KnowledgePoint] = Field(
        min_length=1,
        description="本节知识点",
        json_schema_extra={"edge_label": "CONTAINS_KNOWLEDGE_POINT"},
    )


class Chapter(BaseModel):
    """教材的一级知识章节；优先保留原文已有章标题。"""

    model_config = {"is_entity": True, "graph_id_fields": ["title"]}

    title: str = Field(min_length=1, description="保留原文章标题；无标题时按语义生成")
    sections: list[Section] = Field(
        min_length=1,
        description="本章分节",
        json_schema_extra={"edge_label": "CONTAINS_SECTION"},
    )


class CourseKnowledgeTree(BaseModel):
    """从一份教师共享课程资料抽取的、可追溯且待教师审核的知识树。"""

    model_config = {"is_entity": True, "graph_id_fields": ["document_title"]}

    document_title: str = Field(min_length=1, description="输入中给出的资料名称")
    chapters: list[Chapter] = Field(
        min_length=1,
        description="覆盖全文知识正文的课程章节",
        json_schema_extra={"edge_label": "CONTAINS_CHAPTER"},
    )
    question_block_ids: list[str] = Field(
        default_factory=list,
        description="题目、答案或解析对应的 ZHIJIAO_BLOCK 标识",
    )
    excluded_block_ids: list[str] = Field(
        default_factory=list,
        description="页眉页脚、装饰、版权等不进入知识库的块标识",
    )
    unclassified_block_ids: list[str] = Field(
        default_factory=list,
        description="无法可靠归类、必须交给教师复核的块标识",
    )
