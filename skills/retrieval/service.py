import re
from dataclasses import asdict, dataclass
from pathlib import Path

from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics.pairwise import cosine_similarity


@dataclass
class Evidence:
    source_file: str
    section: str
    text: str
    score: float

    def to_dict(self) -> dict:
        return asdict(self)


class CourseRetriever:
    def __init__(self, materials_dir: Path | str):
        self.materials_dir = Path(materials_dir)
        self.chunks = self._load_chunks()
        self.vectorizer = TfidfVectorizer(analyzer="char", ngram_range=(1, 3), min_df=1)
        self.matrix = self.vectorizer.fit_transform([c["text"] for c in self.chunks]) if self.chunks else None

    def _load_chunks(self) -> list[dict]:
        chunks: list[dict] = []
        for path in sorted(self.materials_dir.glob("*.md")):
            section = "导言"
            buffer: list[str] = []
            for line in path.read_text(encoding="utf-8").splitlines() + ["# END"]:
                if line.startswith("#"):
                    if buffer:
                        text = "\n".join(buffer).strip()
                        if text:
                            chunks.append({"source_file": path.name, "section": section, "text": text})
                    section = line.lstrip("#").strip()
                    buffer = []
                elif line.strip():
                    buffer.append(line.strip())
        return chunks

    @staticmethod
    def _keywords(text: str) -> set[str]:
        runs = re.findall(r"[\u4e00-\u9fff]+", text)
        chinese = {
            run[index:index + size]
            for run in runs
            for size in (2, 3, 4)
            for index in range(max(len(run) - size + 1, 0))
        }
        words = set(re.findall(r"[A-Za-z0-9_-]{2,}", text.lower()))
        return chinese | words

    def search(self, query: str, top_k: int = 4) -> list[Evidence]:
        if not query.strip() or self.matrix is None:
            return []
        tfidf_scores = cosine_similarity(self.vectorizer.transform([query]), self.matrix)[0]
        query_keys = self._keywords(query)
        ranked = []
        for index, chunk in enumerate(self.chunks):
            chunk_keys = self._keywords(chunk["text"] + " " + chunk["section"])
            keyword_score = len(query_keys & chunk_keys) / max(len(query_keys), 1)
            score = 0.72 * float(tfidf_scores[index]) + 0.28 * keyword_score
            ranked.append((score, chunk))
        ranked.sort(key=lambda item: item[0], reverse=True)
        return [Evidence(c["source_file"], c["section"], c["text"], round(s, 4))
                for s, c in ranked[:top_k] if s > 0]
