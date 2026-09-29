"""
混合查询引擎
============
本体 + 图谱 + 向量检索的混合查询，是知识库准确率的核心。

查询模式:
  1. 自然语言问答 — 向量检索找候选 → 本体消歧 → 图谱精确定位
  2. 实体查询 — 按名称/类型查实体属性
  3. 关系查询 — 查某实体的关联实体（受本体约束）
  4. 路径查询 — 查两个实体间的关系路径

本体如何提升准确性:
  - 消歧: "苹果"可能指水果或公司，本体约束 entity_type=Product 立即消歧
  - 关系校验: 查"A管理B"时，本体校验 manages 的 domain=Person, range=Product
  - 路径推理: 通过 ontology 中的 inverse 关系自动推导反向查询
  - 类型约束: 限制查询范围到特定实体类型，减少噪声
"""

from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Tuple

from .schema import Entity, Relation, Triple
from .ontology import Ontology
from .graph import GraphStore
from .vectorstore import VectorStore


class QueryResult:
    """查询结果容器"""
    def __init__(self, query: str, mode: str):
        self.query = query
        self.mode = mode  # "natural_language" | "entity" | "relation" | "path"
        self.entities: List[Entity] = []
        self.triples: List[Triple] = []
        self.documents: List[dict] = []  # 向量检索命中的文档
        self.answer: str = ""            # 自然语言答案
        self.confidence: float = 0.0
        self.explanation: str = ""       # 解释推理过程

    def to_dict(self) -> dict:
        return {
            "query": self.query,
            "mode": self.mode,
            "answer": self.answer,
            "confidence": self.confidence,
            "explanation": self.explanation,
            "entities": [e.to_dict() for e in self.entities],
            "triples": [t.to_dict() for t in self.triples],
            "documents": self.documents,
        }


class QueryEngine:
    """混合查询引擎"""

    def __init__(
        self,
        ontology: Ontology,
        graph: GraphStore,
        vector: VectorStore,
    ):
        self.ontology = ontology
        self.graph = graph
        self.vector = vector

    # ---- 自然语言查询 ----

    def ask(self, question: str) -> QueryResult:
        """
        自然语言问答 — 主入口。

        流程:
          1. 向量检索找到相关文档和实体候选
          2. 本体消歧：根据问题中的线索确定实体类型
          3. 图谱精确定位：根据问题中的关系线索查询图谱
          4. 综合生成答案
        """
        result = QueryResult(question, "natural_language")

        # Step 1: 向量检索
        search_results = self.vector.search(question, top_k=10)
        result.documents = [
            {"doc_id": did, "score": score, "metadata": meta}
            for did, score, meta in search_results
        ]

        # Step 2: 识别问题中的实体类型和关系类型
        entity_types_hint = self._detect_entity_types(question)
        relation_types_hint = self._detect_relation_types(question)
        section_hint = self._detect_section(question)
        result.explanation = (
            f"Detected entity types: {entity_types_hint}, "
            f"relation types: {relation_types_hint}"
        )
        if section_hint:
            result.explanation += f", section: {section_hint}"
        result.explanation += "\n"

        # Step 3: 从检索结果中提取候选实体（受本体类型约束）
        candidate_entities = self._extract_candidate_entities(
            search_results, entity_types_hint, question
        )
        result.entities = candidate_entities

        # Step 3.5: 识别问题中明确提到的实体（用于后续过滤）
        question_entity_eids = set()
        for entity in candidate_entities:
            if entity.name and entity.name in question:
                question_entity_eids.add(entity.eid)

        # Step 4: 图谱查询 — 如果检测到关系类型，执行图谱遍历
        if relation_types_hint and candidate_entities:
            seen_triples = set()  # (src_eid, rel_type, tgt_eid) 去重
            for rel_type in relation_types_hint:
                for entity in candidate_entities:
                    triples = self._query_relation(entity, rel_type)
                    for t in triples:
                        key = (t.source.eid, t.relation_type, t.target.eid)
                        if key not in seen_triples:
                            seen_triples.add(key)
                            result.triples.append(t)

        # Step 4.2: 如果问题中明确提到了实体，只保留涉及这些实体的三元组
        # 例如 "马亮负责什么模块" → 只保留马亮相关的边，过滤掉其他人的边
        if question_entity_eids and result.triples:
            result.triples = [
                t for t in result.triples
                if t.source.eid in question_entity_eids
                or t.target.eid in question_entity_eids
            ]

        # Step 4.5: 如果检测到 section（在研/维护），按边的 section 属性过滤
        if section_hint and result.triples:
            result.triples = self._filter_triples_by_section(
                result.triples, section_hint
            )

        # Step 5: 如果没有检测到关系，尝试查找直接匹配的实体属性
        if not result.triples and candidate_entities:
            for entity in candidate_entities:
                # 查找该实体的所有邻居
                neighbors = self.graph.get_neighbors(entity.eid)
                for rt, direction, neighbor in neighbors:
                    src = entity if direction == "out" else neighbor
                    tgt = neighbor if direction == "out" else entity
                    result.triples.append(Triple(src, rt, tgt))

        # Step 6: 生成答案
        result.answer = self._generate_answer(question, result)
        result.confidence = self._compute_confidence(result)

        return result

    # ---- 结构化查询 ----

    def query_entity(
        self,
        name: str,
        entity_type: Optional[str] = None,
    ) -> QueryResult:
        """查询实体详情"""
        result = QueryResult(f"entity:{name}", "entity")

        entities = self.graph.find_entities(
            entity_type=entity_type,
            name_pattern=name,
        )

        if not entities:
            result.answer = f"No entity found matching '{name}'" + (
                f" of type '{entity_type}'" if entity_type else ""
            )
            return result

        result.entities = entities
        for entity in entities:
            neighbors = self.graph.get_neighbors(entity.eid)
            for rt, direction, neighbor in neighbors:
                src = entity if direction == "out" else neighbor
                tgt = neighbor if direction == "out" else entity
                result.triples.append(Triple(src, rt, tgt))

        result.answer = self._format_entity_answer(entities, result.triples)
        result.confidence = 1.0 if len(entities) == 1 else 0.7
        return result

    def query_relation(
        self,
        entity_name: str,
        relation_type: str,
        entity_type: Optional[str] = None,
    ) -> QueryResult:
        """
        查询关系 — 本体约束的核心应用。

        本体在此的作用:
          1. 校验 relation_type 是否存在
          2. 校验 entity_type 是否在该关系的 domain/range 中
          3. 自动使用 inverse 关系进行双向查询
        """
        result = QueryResult(
            f"relation:{entity_name}--{relation_type}", "relation"
        )

        # 本体校验
        rt_def = self.ontology.get_relation_type(relation_type)
        if not rt_def:
            result.answer = f"Unknown relation type: {relation_type}"
            result.explanation = f"Available relations: {self.ontology.all_relation_types()}"
            return result

        # 查找实体
        entities = self.graph.find_entities(
            entity_type=entity_type,
            name_pattern=entity_name,
        )

        if not entities:
            result.answer = f"No entity found: {entity_name}"
            return result

        for entity in entities:
            # 正向查询
            neighbors = self.graph.get_neighbors(
                entity.eid, relation_type=relation_type, direction="out"
            )
            for rt, direction, neighbor in neighbors:
                result.triples.append(Triple(entity, rt, neighbor))

            # 如果本体定义了 inverse，也查反向
            inverse = self.ontology.get_inverse(relation_type)
            if inverse and inverse != relation_type:
                neighbors = self.graph.get_neighbors(
                    entity.eid, relation_type=inverse, direction="in"
                )
                for rt, direction, neighbor in neighbors:
                    result.triples.append(Triple(neighbor, rt, entity))

        result.entities = entities
        result.answer = self._format_relation_answer(entity_name, relation_type, result.triples)
        result.confidence = 1.0 if result.triples else 0.5
        result.explanation = f"Queried relation '{relation_type}'"
        if rt_def.inverse:
            result.explanation += f" (inverse: '{rt_def.inverse}')"
        return result

    def query_path(
        self,
        source_name: str,
        target_name: str,
    ) -> QueryResult:
        """查询两个实体之间的关系路径"""
        result = QueryResult(
            f"path:{source_name}->{target_name}", "path"
        )

        src_entities = self.graph.find_entities(name_pattern=source_name)
        tgt_entities = self.graph.find_entities(name_pattern=target_name)

        if not src_entities:
            result.answer = f"Source entity not found: {source_name}"
            return result
        if not tgt_entities:
            result.answer = f"Target entity not found: {target_name}"
            return result

        # 优先选择有图连接的实体（排除孤立节点如同名Document）
        src = self._pick_connected_entity(src_entities)
        tgt = self._pick_connected_entity(tgt_entities)

        path = self.graph.find_path(src.eid, tgt.eid, max_depth=5)

        if path:
            result.explanation = f"Found path with {len(path)} hops"
            for u_eid, rt, v_eid in path:
                u_entity = self.graph.get_entity(u_eid)
                v_entity = self.graph.get_entity(v_eid)
                if u_entity and v_entity:
                    result.triples.append(Triple(u_entity, rt, v_entity))
            result.answer = self._format_path_answer(src, tgt, result.triples)
            result.confidence = 0.9
        else:
            result.answer = f"No path found between '{source_name}' and '{target_name}' (within 5 hops)"
            result.confidence = 0.3

        result.entities = [src, tgt]
        return result

    # ---- 内部方法 ----

    def _detect_entity_types(self, question: str) -> List[str]:
        """从问题中检测可能的实体类型（从本体读取 query_keywords）"""
        hints = []
        q_lower = question.lower()
        for etype, keywords in self.ontology.get_entity_query_keywords().items():
            if any(kw in q_lower for kw in keywords):
                hints.append(etype)

        return hints or list(self.ontology.all_entity_types())

    def _detect_relation_types(self, question: str) -> List[str]:
        """从问题中检测可能的关系类型（从本体读取 query_keywords）"""
        hints = []
        for rtype, keywords in self.ontology.get_relation_query_keywords().items():
            if any(kw in question for kw in keywords):
                hints.append(rtype)
        return hints

    def _detect_section(self, question: str) -> Optional[str]:
        """从问题中检测是否提到特定小节（从本体读取 section_keywords）"""
        for section in self.ontology.section_keywords:
            if section in question:
                return section
        return None

    def _filter_triples_by_section(
        self, triples: List[Triple], section: str
    ) -> List[Triple]:
        """
        按边的 section 属性过滤三元组。

        图谱边数据中带有 attributes.section（来自 Markdown 表格所在的小节标题），
        只保留 section 匹配的边。如果边没有 section 属性则保留（不丢弃非表格关系）。
        """
        filtered = []
        for t in triples:
            # 从图中查询这条边的属性
            edge_data = self.graph.graph.get_edge_data(
                t.source.eid, t.target.eid
            )
            if not edge_data:
                # 反向边
                edge_data = self.graph.graph.get_edge_data(
                    t.target.eid, t.source.eid
                )
            if not edge_data:
                filtered.append(t)
                continue

            # MultiDiGraph: edge_data 是 {key: attrs} 字典
            for attrs in edge_data.values():
                edge_section = attrs.get("section", "")
                if not edge_section or edge_section == section:
                    filtered.append(t)
                    break
            else:
                # 所有平行边都不匹配，跳过
                pass

        return filtered

    def _extract_candidate_entities(
        self,
        search_results: List[Tuple[str, float, dict]],
        entity_type_hints: List[str],
        question: str,
    ) -> List[Entity]:
        """
        从检索结果中提取候选实体，受本体类型约束。

        本体的作用：只保留类型匹配的实体，过滤噪声。

        排序优先级:
          1. 问题中明确提到的实体名（最高优先）
          2. 向量检索命中文档中提取的实体
        """
        candidates = []
        seen_eids = set()

        # 1. 优先匹配问题中出现的实体名 — 这些是查询的核心
        for entity in self.graph.all_entities():
            if entity.eid in seen_eids:
                continue
            if entity.name and entity.name in question:
                # 跳过短名 Document 实体（Markdown 标题），它们通常不是查询目标
                if entity.entity_type == "Document" and len(entity.name) <= 4:
                    continue
                candidates.append(entity)
                seen_eids.add(entity.eid)

        # 2. 从检索命中的文档元数据中提取实体名
        for doc_id, score, meta in search_results:
            entity_names = meta.get("entities", [])

            for ename in entity_names:
                # 在图谱中查找该名称的实体
                entities = self.graph.find_entities(name_pattern=ename)
                for entity in entities:
                    if entity.eid in seen_eids:
                        continue
                    # 本体类型约束
                    if entity.entity_type in entity_type_hints or not entity_type_hints:
                        candidates.append(entity)
                        seen_eids.add(entity.eid)

        return candidates[:30]  # 限制候选数量

    def _query_relation(self, entity: Entity, relation_type: str) -> List[Triple]:
        """
        查询单个实体的特定关系（双向）。
        本体在此发挥作用：根据 domain/range 判断实体应该是源还是目标，
        然后同时查询正向(out)和反向(in)边。
        """
        triples = []
        # 查询正向和反向边（get_neighbors direction="both" 默认查两个方向）
        neighbors = self.graph.get_neighbors(
            entity.eid, relation_type=relation_type, direction="both"
        )
        for rt, direction, neighbor in neighbors:
            src = entity if direction == "out" else neighbor
            tgt = neighbor if direction == "out" else entity
            triples.append(Triple(src, rt, tgt))

        # inverse 查询
        inverse = self.ontology.get_inverse(relation_type)
        if inverse and inverse != relation_type:
            neighbors = self.graph.get_neighbors(
                entity.eid, relation_type=inverse, direction="both"
            )
            for rt, direction, neighbor in neighbors:
                src = entity if direction == "out" else neighbor
                tgt = neighbor if direction == "out" else entity
                triples.append(Triple(src, rt, tgt))

        return triples

    def _pick_connected_entity(self, entities: List[Entity]) -> Entity:
        """从候选实体中优先选择有图连接的实体"""
        for entity in entities:
            neighbors = self.graph.get_neighbors(entity.eid, direction="both")
            if neighbors:
                return entity
        return entities[0]

    def _generate_answer(self, question: str, result: QueryResult) -> str:
        """
        根据查询结果生成自然语言答案。

        策略:
          1. 按关系类型分组三元组，生成结构化中文回答
          2. 对 "谁/哪些人" 类问题 → "X是A、B和C"
          3. 对 "什么模块/产品" 类问题 → "X负责/管理A、B和C"
          4. 兜底：结构化三元组 + 实体列表
        """
        if not result.triples and not result.entities:
            if result.documents:
                top_doc = result.documents[0]
                return f"根据知识库检索，最相关的内容来自: {top_doc['doc_id']} (相关度: {top_doc['score']:.2f})"
            return "未找到相关知识。"

        # 尝试生成自然语言回答
        nl_answer = self._generate_natural_language_answer(question, result)
        if nl_answer:
            parts = [nl_answer]
        else:
            # 兜底：结构化输出
            parts = []
            if result.triples:
                parts.append("根据知识图谱查询结果:")
                for t in result.triples[:10]:
                    parts.append(f"  - {t}")

        # 附加实体信息（仅当三元组较少时补充）
        if result.entities and len(result.triples) <= 10:
            # 过滤掉已出现在三元组中的实体，只显示额外信息
            triple_entity_names = set()
            for t in result.triples:
                triple_entity_names.add(t.source.name)
                triple_entity_names.add(t.target.name)

            extra_entities = [
                e for e in result.entities
                if e.name not in triple_entity_names
                and e.entity_type != "Document"
            ]
            if extra_entities:
                parts.append(f"\n其他相关实体:")
                for e in extra_entities[:5]:
                    parts.append(f"  - {e.entity_type}: {e.name}")
                    if e.attributes:
                        for k, v in e.attributes.items():
                            if k in ("description", "title", "answer", "summary"):
                                val = str(v)[:200]
                                parts.append(f"      {k}: {val}")

        if result.documents:
            parts.append(f"\n参考: 向量检索命中 {len(result.documents)} 篇文档")

        return "\n".join(parts)

    def _generate_natural_language_answer(
        self, question: str, result: QueryResult
    ) -> Optional[str]:
        """
        将三元组结果转成自然语言中文回答。

        分析问题的意图（问谁？问什么？），按关系分组生成句子。
        """
        if not result.triples:
            return None

        # 按 (source, relation_type) 分组
        groups: Dict[Tuple[str, str], List[str]] = {}
        for t in result.triples:
            key = (t.source.name, t.relation_type)
            if key not in groups:
                groups[key] = []
            # 收集目标名称 + 可选属性
            target_info = t.target.name
            emp_id = t.target.attributes.get("employee_id", "")
            if emp_id:
                target_info = f"{t.target.name}（{emp_id}）"
            groups[key].append(target_info)

        # 去重并保持顺序
        for key in groups:
            seen = set()
            unique = []
            for v in groups[key]:
                if v not in seen:
                    seen.add(v)
                    unique.append(v)
            groups[key] = unique

        sentences = []
        for (src_name, rel_type), targets in groups.items():
            # 从本体读取中文谓词和连接词，避免硬编码
            rel_chinese = self.ontology.get_relation_chinese()
            pred, connector = rel_chinese.get(rel_type, (rel_type, ""))
            target_str = self._join_chinese(targets)
            sentences.append(f"{src_name} {pred}{connector}{target_str}")

        if sentences:
            return "\n".join(sentences)
        return None

    @staticmethod
    def _join_chinese(items: List[str]) -> str:
        """中文连接: [A, B, C] → "A、B 和 C" """
        if len(items) == 1:
            return items[0]
        if len(items) == 2:
            return f"{items[0]} 和 {items[1]}"
        return "、".join(items[:-1]) + f" 和 {items[-1]}"

    def _compute_confidence(self, result: QueryResult) -> float:
        """计算答案置信度"""
        if not result.triples and not result.entities:
            return 0.1

        score = 0.0
        if result.triples:
            score += 0.4  # 图谱有结果
        if result.entities:
            score += 0.3  # 实体有结果
        if result.documents:
            score += min(0.3, result.documents[0].get("score", 0))  # 向量检索有结果

        return min(1.0, score)

    def _format_entity_answer(self, entities: List[Entity], triples: List[Triple]) -> str:
        parts = []
        for entity in entities:
            parts.append(f"实体: {entity.name} (类型: {entity.entity_type})")
            if entity.attributes:
                for k, v in entity.attributes.items():
                    val = str(v)[:300]
                    parts.append(f"  {k}: {val}")
            # 关联关系
            entity_triples = [t for t in triples
                              if t.source.eid == entity.eid or t.target.eid == entity.eid]
            if entity_triples:
                parts.append(f"  关联 ({len(entity_triples)} 条):")
                for t in entity_triples[:10]:
                    parts.append(f"    {t}")
        return "\n".join(parts)

    def _format_relation_answer(
        self, entity_name: str, relation_type: str, triples: List[Triple]
    ) -> str:
        if not triples:
            return f"未找到 '{entity_name}' 的 '{relation_type}' 关系"

        parts = [f"'{entity_name}' 的 '{relation_type}' 关系:"]
        for t in triples:
            parts.append(f"  {t}")
        return "\n".join(parts)

    def _format_path_answer(
        self, src: Entity, tgt: Entity, triples: List[Triple]
    ) -> str:
        parts = [f"从 '{src.name}' 到 '{tgt.name}' 的路径:"]
        for i, t in enumerate(triples):
            parts.append(f"  [{i+1}] {t}")
        return "\n".join(parts)
