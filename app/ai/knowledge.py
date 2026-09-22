"""知识库：资料切分、入库与检索（全部落在数据库里）。

为什么不用 Chroma：本项目的知识体量是"用户上传的几份理财资料"，
把文档与切片存进数据库（``knowledge_document`` / ``knowledge_chunk``）更贴合
数据库课程设计的主题，也让多用户隔离与审计直接在 SQL 里完成。

检索策略（自动选择）：
* 有向量：``embedding`` 列存 float32 二进制，检索时算余弦相似度；
* 无向量：中文二元切分 + BM25 打分（纯 Python，零依赖、零下载）。

向量能力是可选增强：安装 ``requirements-rag.txt`` 里的 fastembed 后，
``EMBEDDING_PROVIDER=local`` 即可启用，未安装时自动退回 BM25。
"""

from __future__ import annotations

import math
import re
import struct
from collections import Counter
from dataclasses import dataclass
from typing import Any, Iterable

from ..config import Settings
from ..db.base import DatabaseAdapter, Tx
from ..db.repository import LedgerRepository  # noqa: F401  (类型提示用)

CHUNK_SIZE = 320
CHUNK_OVERLAP = 60
DEFAULT_TOP_K = 5

_CJK = re.compile(r"[\u4e00-\u9fff]")
_TOKEN = re.compile(r"[a-zA-Z0-9]+")
_PUNCT = re.compile(r"[\s，。；：！？、（）()《》\"'’“”\-—/\\|,.!?;:\[\]{}<>@#$%^&*+=~`]+")


def tokenize(text: str) -> list[str]:
    """中文二元切分 + 英文数字整词，兼顾召回与零依赖。"""
    lowered = (text or "").lower()
    tokens: list[str] = []
    for word in _TOKEN.findall(lowered):
        tokens.append(word)
    for segment in _PUNCT.split(lowered):
        characters = [char for char in segment if _CJK.match(char)]
        if len(characters) == 1:
            tokens.append(characters[0])
        for index in range(len(characters) - 1):
            tokens.append(characters[index] + characters[index + 1])
    return tokens


def split_text(text: str, chunk_size: int = CHUNK_SIZE, overlap: int = CHUNK_OVERLAP) -> list[str]:
    """按标点优先切分，尽量保持语义完整。"""
    normalized = re.sub(r"\r\n?", "\n", text or "").strip()
    if not normalized:
        return []
    paragraphs = [part.strip() for part in re.split(r"\n{2,}", normalized) if part.strip()]
    chunks: list[str] = []
    buffer = ""
    for paragraph in paragraphs:
        if len(buffer) + len(paragraph) + 1 <= chunk_size:
            buffer = f"{buffer}\n{paragraph}".strip()
            continue
        if buffer:
            chunks.append(buffer)
        while len(paragraph) > chunk_size:
            chunks.append(paragraph[:chunk_size])
            paragraph = paragraph[chunk_size - overlap :]
        buffer = paragraph
    if buffer:
        chunks.append(buffer)
    return [chunk for chunk in chunks if chunk.strip()]


@dataclass
class RetrievedChunk:
    chunk_id: int
    document_id: int
    title: str
    chunk_index: int
    content: str
    score: float
    mode: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "chunk_id": self.chunk_id,
            "document_id": self.document_id,
            "title": self.title,
            "chunk_index": self.chunk_index,
            "content": self.content,
            "score": round(self.score, 4),
            "mode": self.mode,
        }


class Embedder:
    """可选向量化。未安装依赖或模型不可用时静默返回 None。"""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self._model = None
        self._failed = False

    @property
    def available(self) -> bool:
        if self.settings.embedding_provider not in ("local",):
            return False
        if self._failed:
            return False
        return self._load() is not None

    def _load(self):
        if self._model is not None:
            return self._model
        try:
            from fastembed import TextEmbedding
        except ImportError:
            self._failed = True
            return None
        try:
            self.settings.embedding_cache_path.mkdir(parents=True, exist_ok=True)
            self._model = TextEmbedding(
                model_name=self.settings.local_embedding_model,
                cache_dir=str(self.settings.embedding_cache_path),
            )
        except Exception:  # noqa: BLE001 - 下载失败不应影响主流程
            self._failed = True
            return None
        return self._model

    def encode(self, texts: Iterable[str]) -> list[list[float]] | None:
        model = self._load()
        if model is None:
            return None
        try:
            return [list(vector) for vector in model.embed(list(texts))]
        except Exception:  # noqa: BLE001
            self._failed = True
            return None


class KnowledgeBase:
    def __init__(self, adapter: DatabaseAdapter, settings: Settings) -> None:
        self.adapter = adapter
        self.settings = settings
        self.embedder = Embedder(settings)

    @property
    def ph(self) -> str:
        return "?" if self.adapter.mode == "sqlite" else "%s"

    # ------------------------------------------------------------------ #
    # 入库
    # ------------------------------------------------------------------ #
    def ingest(self, user_id: int, *, title: str, content: str, source: str = "manual") -> dict[str, Any]:
        chunks = split_text(content)
        if not chunks:
            return {"status": "empty", "document_id": None, "chunk_count": 0, "message": "没有可入库的内容"}

        vectors = self.embedder.encode(chunks) if self.embedder.available else None
        placeholder = self.ph
        with self.adapter.write_transaction() as tx:
            document_id = tx.insert(
                "knowledge_document",
                {
                    "user_id": user_id,
                    "title": title.strip() or "未命名资料",
                    "source": source[:200],
                    "chunk_count": len(chunks),
                },
            )
            for index, chunk in enumerate(chunks):
                payload: dict[str, Any] = {
                    "document_id": document_id,
                    "user_id": user_id,
                    "chunk_index": index,
                    "content": chunk,
                    "embedding": _pack_vector(vectors[index]) if vectors else None,
                    "embedding_model": self.settings.local_embedding_model if vectors else None,
                }
                tx.insert("knowledge_chunk", payload)
        return {
            "status": "added",
            "document_id": document_id,
            "chunk_count": len(chunks),
            "mode": "embedding" if vectors else "bm25",
            "message": f"已入库《{title}》共 {len(chunks)} 个片段",
        }

    def list_documents(self, user_id: int) -> list[dict[str, Any]]:
        rows = self.adapter.query(
            f"SELECT document_id, title, source, chunk_count, create_time FROM knowledge_document "
            f"WHERE user_id = {self.ph} ORDER BY document_id DESC",
            (user_id,),
        )
        for row in rows:
            if hasattr(row.get("create_time"), "isoformat"):
                row["create_time"] = row["create_time"].isoformat()
        return rows

    def delete_document(self, user_id: int, document_id: int) -> int:
        with self.adapter.write_transaction() as tx:
            # 显式先删切片，兼容未开启 ON DELETE CASCADE 的 PostgreSQL 老结构
            tx.execute(
                f"DELETE FROM knowledge_chunk WHERE document_id = {self.ph} AND user_id = {self.ph}",
                (document_id, user_id),
            )
            return tx.execute(
                f"DELETE FROM knowledge_document WHERE document_id = {self.ph} AND user_id = {self.ph}",
                (document_id, user_id),
            )

    # ------------------------------------------------------------------ #
    # 检索
    # ------------------------------------------------------------------ #
    def search(self, user_id: int, question: str, top_k: int = DEFAULT_TOP_K) -> list[RetrievedChunk]:
        rows = self.adapter.query(
            "SELECT c.chunk_id, c.document_id, c.chunk_index, c.content, c.embedding, d.title "
            "FROM knowledge_chunk c JOIN knowledge_document d ON d.document_id = c.document_id "
            f"WHERE c.user_id = {self.ph}",
            (user_id,),
        )
        if not rows:
            return []

        query_vector = None
        if self.embedder.available:
            encoded = self.embedder.encode([question])
            if encoded:
                query_vector = encoded[0]

        stored_vectors = [row.get("embedding") for row in rows]
        if query_vector is not None and any(stored_vectors):
            return self._vector_search(rows, query_vector, top_k)
        return self._bm25_search(rows, question, top_k)

    def _vector_search(self, rows: list[dict[str, Any]], query_vector: list[float], top_k: int) -> list[RetrievedChunk]:
        scored: list[RetrievedChunk] = []
        for row in rows:
            vector = _unpack_vector(row.get("embedding"))
            if not vector:
                continue
            scored.append(
                RetrievedChunk(
                    chunk_id=int(row["chunk_id"]),
                    document_id=int(row["document_id"]),
                    title=row.get("title") or "",
                    chunk_index=int(row["chunk_index"]),
                    content=row["content"],
                    score=_cosine(query_vector, vector),
                    mode="embedding",
                )
            )
        scored.sort(key=lambda item: item.score, reverse=True)
        return scored[:top_k]

    def _bm25_search(self, rows: list[dict[str, Any]], question: str, top_k: int) -> list[RetrievedChunk]:
        query_tokens = tokenize(question)
        if not query_tokens:
            return []
        documents = [tokenize(row["content"]) for row in rows]
        lengths = [len(tokens) or 1 for tokens in documents]
        average_length = sum(lengths) / len(lengths)
        document_frequency: Counter[str] = Counter()
        for tokens in documents:
            for token in set(tokens):
                document_frequency[token] += 1

        total_documents = len(documents)
        k1, b = 1.5, 0.75
        scored: list[RetrievedChunk] = []
        for index, (row, tokens) in enumerate(zip(rows, documents)):
            frequencies = Counter(tokens)
            score = 0.0
            for token in query_tokens:
                frequency = frequencies.get(token)
                if not frequency:
                    continue
                idf = math.log(1 + (total_documents - document_frequency[token] + 0.5) / (document_frequency[token] + 0.5))
                denominator = frequency + k1 * (1 - b + b * lengths[index] / average_length)
                score += idf * (frequency * (k1 + 1)) / denominator
            if score > 0:
                scored.append(
                    RetrievedChunk(
                        chunk_id=int(row["chunk_id"]),
                        document_id=int(row["document_id"]),
                        title=row.get("title") or "",
                        chunk_index=int(row["chunk_index"]),
                        content=row["content"],
                        score=score,
                        mode="bm25",
                    )
                )
        scored.sort(key=lambda item: item.score, reverse=True)
        return scored[:top_k]

    def build_context(self, chunks: list[RetrievedChunk]) -> str:
        return "\n\n".join(
            f"[片段{index}] 《{chunk.title}》#{chunk.chunk_index}\n{chunk.content}"
            for index, chunk in enumerate(chunks, start=1)
        )


def _pack_vector(vector: list[float]) -> bytes:
    return struct.pack(f"<{len(vector)}f", *vector)


def _unpack_vector(blob: Any) -> list[float]:
    if not blob:
        return []
    if isinstance(blob, memoryview):
        blob = bytes(blob)
    if not isinstance(blob, (bytes, bytearray)):
        return []
    count = len(blob) // 4
    if count == 0:
        return []
    return list(struct.unpack(f"<{count}f", blob[: count * 4]))


def _cosine(left: list[float], right: list[float]) -> float:
    if len(left) != len(right) or not left:
        return 0.0
    dot = sum(a * b for a, b in zip(left, right))
    norm_left = math.sqrt(sum(a * a for a in left))
    norm_right = math.sqrt(sum(b * b for b in right))
    if norm_left == 0 or norm_right == 0:
        return 0.0
    return dot / (norm_left * norm_right)


__all__ = [
    "CHUNK_OVERLAP",
    "CHUNK_SIZE",
    "Embedder",
    "KnowledgeBase",
    "RetrievedChunk",
    "split_text",
    "tokenize",
]
