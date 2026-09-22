import pytest

from skills.memory.markdown_cards import split_markdown_cards
from skills.memory.markdown_cards import markdown_card_title, split_numbered_sections


def test_textbook_numbering_keeps_explanations_and_subitems():
    introduction = '6.4.3数据模型的优化\n\n数据库逻辑设计的结果不是唯一的。\n\n'
    first = '1. 确定范式级别\n\n考查关系模式的函数依赖关系，确定范式等级。\n\n'
    second = '2. 实施规范化处理\n\n利用规范化理论逐一考察关系模式。\n\n(1) 在需求分析阶段，用数据依赖分析联系。\n(2) 在概念结构设计阶段，消除冗余。\n(3) 在逻辑结构设计阶段，分解模式。\n\n'
    third = '3. 模式评价与改进\n\n(1) 模式评价。检查用户需求。\n\n检查数据库的效率。'
    source = introduction + first + second + third
    assert split_markdown_cards(source) == [introduction + first, second, third]
    assert markdown_card_title(introduction + first, '默认') == '1. 确定范式级别'


@pytest.mark.parametrize('numbers', [('1. ', '2. '), ('1、', '2、'), ('（一）', '（二）'), ('(1)', '(2)'), ('一、', '二、')])
def test_numbered_points_without_blank_lines(numbers):
    first = numbers[0] + '第一个知识点\n这里是相应解释。\n'
    second = numbers[1] + '第二个知识点\n这里是另一段解释。'
    assert split_markdown_cards(first + second) == [first, second]


@pytest.mark.parametrize('wrapper', [('```text\n', '\n```'), ('$$\n', '\n$$'), ('\\[\n', '\n\\]')])
def test_numbering_inside_code_or_math_does_not_split(wrapper):
    source = wrapper[0] + '1. 第一项\n\n2. 第二项' + wrapper[1]
    assert split_numbered_sections(source) == [source]


def test_sections_and_decimal_values():
    first = '6.4.3数据模型的优化\n说明及数值如下。\n3.14 是圆周率近似值。\n'
    second = '6.4.4模式评价\n解释。'
    assert split_numbered_sections(first + second) == [first, second]


def test_short_card_keeps_heading_and_math():
    source = '# 面积\n\n## 圆\n\n面积 $S=\\pi r^2$。'
    assert split_markdown_cards(source) == [source]


@pytest.mark.parametrize('splitter', [split_numbered_sections, split_markdown_cards])
def test_screenshot_heading_only_fragments_join_real_knowledge(splitter):
    headings = '# 第 6 章\n\n## 6.4逻辑结构设计\n\n6.4逻辑结构设计\n\n'
    first = '6.4.1逻辑结构设计的任务\n\n将概念结构转换为数据库支持的数据模型。\n\n'
    second = '6.4.2关系模型转换\n\n一个实体转换为一个关系模式。'
    parts = splitter(headings + first + second)
    assert parts == [headings + first, second]
    assert ''.join(parts) == headings + first + second
    assert markdown_card_title(parts[0], '原知识点') == '6.4.1逻辑结构设计的任务'


def test_only_headings_never_expand_into_multiple_cards():
    source = '# 第 6 章\n\n## 6.4逻辑结构设计\n\n6.4逻辑结构设计\n\n'
    assert split_numbered_sections(source) == [source]
    assert split_markdown_cards(source, target=10) == [source]


def test_markdown_heading_chain_and_trailing_heading_preserve_text():
    source = '# 章\n\n## 节\n\n### 定义\n\n' + '这是有意义的完整解释。' * 100 + '\n\n## 后续章节\n'
    parts = split_markdown_cards(source, target=100)
    assert ''.join(parts) == source
    assert all('完整解释' in part for part in parts)


@pytest.mark.parametrize('block', [
    '$$\nx^2\n\n+y^2\n$$',
    '\\[\nx^2\n\n+y^2\n\\]',
    '```python\n# 标题不是分割点\n\nprint(1)\n```',
    '| 项目 | 值 |\n| --- | --- |\n| 面积 | 12 |',
])
def test_split_preserves_structured_blocks_and_every_character(block):
    source = '# 大标题\n\n' + '前文说明。' * 150 + '\n\n## 小标题\n\n' + block + '\n\n' + '后文解释。' * 150
    cards = split_markdown_cards(source)
    assert len(cards) > 1
    assert ''.join(cards) == source
    assert any(block in card for card in cards)
    assert all(card.strip() and card.strip() not in ('# 大标题', '## 小标题') for card in cards)


def test_long_plain_paragraph_splits_at_sentences():
    source = '这是一个完整句子。' * 300
    cards = split_markdown_cards(source)
    assert len(cards) > 1
    assert ''.join(cards) == source
    assert all(len(card) <= 800 and card.endswith('。') for card in cards)
