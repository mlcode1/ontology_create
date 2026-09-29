"""
语义检索 (TF-IDF 向量存储)
==========================
轻量级语义检索，无需下载模型。
使用 TF-IDF + 余弦相似度匹配文档与查询。

在混合查询引擎中，向量检索负责"找候选"，
图谱遍历负责"精确定位"，两者互补。
"""

from __future__ import annotations

from typing import Dict, List, Optional, Tuple
import re
import math
import json
import os


def _tokenize(text: str) -> List[str]:
    """简单分词：中英文混合"""
    # 英文：按非字母数字分割
    # 中文：按字分割
    tokens = []
    # 先提取英文单词
    en_tokens = re.findall(r"[a-zA-Z0-9_]+", text)
    tokens.extend(t.lower() for t in en_tokens)
    # 再提取中文字符（每两个字一组作为bigram提升匹配）
    cn_chars = re.findall(r"[\u4e00-\u9fff]", text)
    for ch in cn_chars:
        tokens.append(ch)
    # 中文bigram
    for i in range(len(cn_chars) - 1):
        tokens.append(cn_chars[i] + cn_chars[i + 1])
    return tokens


class VectorStore:
    """TF-IDF 向量存储"""

    def __init__(self):
        # doc_id -> 文本
        self._documents: Dict[str, str] = {}
        # doc_id -> token list
        self._doc_tokens: Dict[str, List[str]] = {}
        # term -> document frequency
        self._df: Dict[str, int] = {}
        # doc_id -> {term: tf-idf weight}
        self._tfidf: Dict[str, Dict[str, float]] = {}
        # doc_id -> metadata
        self._metadata: Dict[str, dict] = {}

    def add_document(self, doc_id: str, text: str, metadata: Optional[dict] = None):
        """添加文档到索引"""
        self._documents[doc_id] = text
        self._metadata[doc_id] = metadata or {}
        self._doc_tokens[doc_id] = _tokenize(text)
        # 增量更新会有误差，标记需要重建
        self._dirty = True

    def remove_document(self, doc_id: str):
        self._documents.pop(doc_id, None)
        self._doc_tokens.pop(doc_id, None)
        self._tfidf.pop(doc_id, None)
        self._metadata.pop(doc_id, None)
        self._dirty = True

    def build_index(self):
        """构建 TF-IDF 索引"""
        N = len(self._documents)
        if N == 0:
            return

        # 计算 DF
        self._df.clear()
        for doc_id, tokens in self._doc_tokens.items():
            unique_terms = set(tokens)
            for term in unique_terms:
                self._df[term] = self._df.get(term, 0) + 1

        # 计算 TF-IDF
        self._tfidf.clear()
        for doc_id, tokens in self._doc_tokens.items():
            tf: Dict[str, int] = {}
            for t in tokens:
                tf[t] = tf.get(t, 0) + 1

            weights = {}
            doc_len = len(tokens)
            for term, count in tf.items():
                tf_val = count / doc_len if doc_len > 0 else 0
                idf_val = math.log((N + 1) / (self._df.get(term, 0) + 1)) + 1
                weights[term] = tf_val * idf_val
            self._tfidf[doc_id] = weights

        self._dirty = False

    def search(self, query: str, top_k: int = 10) -> List[Tuple[str, float, dict]]:
        """
        搜索查询，返回 [(doc_id, score, metadata), ...]
        """
        if self._dirty:
            self.build_index()

        if not self._tfidf:
            return []

        query_tokens = _tokenize(query)
        query_tf: Dict[str, int] = {}
        for t in query_tokens:
            query_tf[t] = query_tf.get(t, 0) + 1

        N = len(self._documents)
        query_weights = {}
        for term, count in query_tf.items():
            tf_val = count / len(query_tokens) if query_tokens else 0
            idf_val = math.log((N + 1) / (self._df.get(term, 0) + 1)) + 1
            query_weights[term] = tf_val * idf_val

        # 余弦相似度
        scores = []
        for doc_id, doc_weights in self._tfidf.items():
            score = self._cosine(query_weights, doc_weights)
            if score > 0:
                scores.append((doc_id, score, self._metadata.get(doc_id, {})))

        scores.sort(key=lambda x: x[1], reverse=True)
        return scores[:top_k]

    @staticmethod
    def _cosine(vec_a: Dict[str, float], vec_b: Dict[str, float]) -> float:
        if not vec_a or not vec_b:
            return 0.0
        # 点积
        common = set(vec_a.keys()) & set(vec_b.keys())
        dot = sum(vec_a[t] * vec_b[t] for t in common)
        # 模长
        norm_a = math.sqrt(sum(v * v for v in vec_a.values()))
        norm_b = math.sqrt(sum(v * v for v in vec_b.values()))
        if norm_a == 0 or norm_b == 0:
            return 0.0
        return dot / (norm_a * norm_b)

    def save(self, path: str):
        data = {
            "documents": self._documents,
            "metadata": self._metadata,
        }
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)

    def load(self, path: str):
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        self._documents = data.get("documents", {})
        self._metadata = data.get("metadata", {})
        self._doc_tokens = {did: _tokenize(text) for did, text in self._documents.items()}
        self._dirty = True
        self.build_index()

    def doc_count(self) -> int:
        return len(self._documents)
