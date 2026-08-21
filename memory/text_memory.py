"""文本记忆：策略逻辑、复盘报告与研报片段的检索存储。

优先使用 ChromaDB（requirements 中的可选依赖）；未安装或离线不可用时，
自动降级为基于 TF-IDF 的余弦相似度检索，保证核心流程零依赖可跑。
"""

from __future__ import annotations

import json
import math
import re
import uuid
from collections import Counter
from pathlib import Path
from typing import Iterable, Optional

_WORD_RE = re.compile(r"[\w\u4e00-\u9fff]+", re.UNICODE)


def _tokenize(text: str) -> list[str]:
    return [w.lower() for w in _WORD_RE.findall(text or "") if w.strip()]


class TextMemoryStore:
    """轻量文本记忆，支持增量写入与相似度检索。"""

    def __init__(
        self,
        path: str | Path = "data/memory_text.json",
        chroma_dir: str | Path = "data/chroma",
    ):
        self.path = Path(path)
        self.chroma_dir = Path(chroma_dir)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._docs: dict[str, dict] = {}
        self._load()
        self._chroma = None
        self._try_init_chroma()

    # ------------------------------------------------------------------ #
    # 持久化
    # ------------------------------------------------------------------ #
    def _load(self) -> None:
        if not self.path.exists():
            return
        try:
            self._docs = json.loads(self.path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            self._docs = {}

    def _save(self) -> None:
        self.path.write_text(
            json.dumps(self._docs, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    # ------------------------------------------------------------------ #
    # ChromaDB 可选后端
    # ------------------------------------------------------------------ #
    def _try_init_chroma(self) -> None:
        try:  # pragma: no cover - 取决于环境
            import chromadb

            client = chromadb.PersistentClient(path=str(self.chroma_dir))
            self._chroma = client.get_or_create_collection("quant_memory")
        except Exception:
            self._chroma = None

    def _chroma_add(self, doc_id: str, text: str, metadata: dict) -> None:
        if self._chroma is None:
            return
        try:  # pragma: no cover - 远端 embedding 失败时静默降级
            self._chroma.upsert(ids=[doc_id], documents=[text], metadatas=[metadata])
        except Exception:
            pass

    def _chroma_search(self, query: str, k: int) -> Optional[list[dict]]:
        if self._chroma is None:
            return None
        try:  # pragma: no cover
            raw = self._chroma.query(query_texts=[query], n_results=k)
            ids = (raw.get("ids") or [[]])[0]
            docs = (raw.get("documents") or [[]])[0]
            distances = (raw.get("distances") or [[]])[0]
            metadatas = (raw.get("metadatas") or [[]])[0]
            return [
                {
                    "doc_id": doc_id,
                    "text": text,
                    "metadata": metadata or {},
                    "score": 1.0 - float(distance) if distance is not None else None,
                }
                for doc_id, text, distance, metadata in zip(
                    ids, docs, distances, metadatas
                )
            ]
        except Exception:
            return None

    # ------------------------------------------------------------------ #
    # 对外接口
    # ------------------------------------------------------------------ #
    def add_document(
        self,
        text: str,
        metadata: Optional[dict] = None,
        doc_id: Optional[str] = None,
    ) -> str:
        doc_id = doc_id or uuid.uuid4().hex
        self._docs[doc_id] = {
            "text": text,
            "metadata": metadata or {},
        }
        self._chroma_add(doc_id, text, metadata or {})
        self._save()
        return doc_id

    def search(
        self,
        query: str,
        k: int = 3,
        threshold: float = 0.0,
    ) -> list[dict]:
        chroma_hits = self._chroma_search(query, k)
        if chroma_hits:
            return [
                hit
                for hit in chroma_hits
                if hit.get("score") is None or hit["score"] >= threshold
            ][:k]
        return self._fallback_search(query, k=k, threshold=threshold)

    def _fallback_search(
        self, query: str, k: int = 3, threshold: float = 0.0
    ) -> list[dict]:
        if not self._docs:
            return []
        query_tokens = Counter(_tokenize(query))
        if not query_tokens:
            return []
        docs = list(self._docs.items())
        doc_tokens = [Counter(_tokenize(text)) for _, item in docs for text in [item["text"]]]
        df = Counter()
        for tokens in doc_tokens:
            for token in set(tokens):
                df[token] += 1
        n_docs = len(docs)
        doc_vectors = []
        for tokens in doc_tokens:
            weights = {
                token: count * math.log((1.0 + n_docs) / (1.0 + df[token]))
                for token, count in tokens.items()
            }
            norm = math.sqrt(sum(v * v for v in weights.values()))
            doc_vectors.append(weights if norm == 0 else {k: v / norm for k, v in weights.items()})
        query_weights = {
            token: count * math.log((1.0 + n_docs) / (1.0 + df[token]))
            for token, count in query_tokens.items()
        }
        qnorm = math.sqrt(sum(v * v for v in query_weights.values()))
        if qnorm == 0:
            return []
        query_vec = {k: v / qnorm for k, v in query_weights.items()}

        scored = []
        for (doc_id, item), vector in zip(docs, doc_vectors):
            score = sum(query_vec.get(token, 0.0) * weight for token, weight in vector.items())
            if score >= threshold:
                scored.append(
                    {
                        "doc_id": doc_id,
                        "text": item["text"],
                        "metadata": item.get("metadata", {}),
                        "score": round(float(score), 6),
                    }
                )
        scored.sort(key=lambda x: x["score"], reverse=True)
        return scored[:k]

    def list_documents(self) -> Iterable[dict]:
        return [
            {"doc_id": doc_id, "text": item["text"], "metadata": item.get("metadata", {})}
            for doc_id, item in self._docs.items()
        ]
