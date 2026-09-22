"""Conservative query coverage gate before generation; similarity alone is insufficient."""
import re
from skills.retrieval import Evidence

_QUESTION_WORDS = re.compile(
    r"请问|请解释|请说明|解释一下|介绍一下|告诉我|什么是|是什么|为什么|怎么样|怎么|如何|"
    r"有什么作用|有什么用|有哪些|有什么|有何|哪些|什么|多少|是否|能否|可以|能够|"
    r"请|说明|解释|介绍|简述|概述|含义|定义|作用|区别|联系|意义|原因|特点|"
    r"[的了呢吗呀啊么与和及是能]"
)
_ENGLISH_QUESTION_WORDS = set('what why how is are a an the of to and or does do can please explain describe define'.split())

def supported_evidence(question: str, candidates: list[Evidence], min_score: float) -> list[Evidence]:
    """Require substantive query spans, including unexpected modifiers, in source text.

    Unknown paraphrases fail closed. This lexical gate is not semantic entailment
    and does not claim to guarantee the truth of a model's generated answer.
    """
    evidence = [item for item in candidates if item.score >= min_score]
    if not evidence:
        return []
    text = '\n'.join(item.text for item in evidence).lower()
    chinese = re.findall(r'[\u4e00-\u9fff]+', _QUESTION_WORDS.sub(' ', question))
    english = [word for word in re.findall(r'[a-zA-Z][a-zA-Z0-9_-]*', question.lower())
               if word not in _ENGLISH_QUESTION_WORDS]
    if not chinese and not english:
        return []
    for word in english:
        if not re.search(r'(?<![a-zA-Z0-9_])' + re.escape(word) + r'(?![a-zA-Z0-9_])', text):
            return []
    for run in chinese:
        if len(run) == 1:
            if run not in text:
                return []
            continue
        covered = set()
        for index in range(len(run) - 1):
            if run[index:index + 2] in text:
                covered.update((index, index + 1))
        if len(covered) / len(run) < 0.85:
            return []
    return evidence
