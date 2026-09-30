"""
知识摄入管道
=============
从多种格式（Markdown / JSON / CSV）摄入知识，
经过抽取器后存入图谱和向量索引。

支持两种模式:
  - auto模式 (默认): 自动发现本体，柔性校验，LLM/规则抽取
  - strict模式: 使用预定义本体，严格校验

摄入流程:
  文件 → 解析 → [自动发现本体] → 抽取实体/关系 → [柔性校验] → 写入图谱 → 写入向量索引
"""

from __future__ import annotations

import json
import csv
import os
import re
from typing import Callable, Dict, List, Optional, Tuple

from .schema import Entity, Relation
from .ontology import Ontology
from .graph import GraphStore
from .vectorstore import VectorStore
from .extractor import OntologyGuidedExtractor, ExtractionResult
from .auto_ontology import AutoOntologyDiscovery
from .llm_extractor import LLMExtractor


class IngestionPipeline:
    """
    知识摄入管道

    参数:
      ontology: 本体定义（可为空 Ontology，auto模式下会自动发现）
      graph_store: 图谱存储
      vector_store: 向量索引
      mode: "auto"=自动发现+柔性校验, "strict"=严格校验
      llm_func: 可选的 LLM 函数，用于辅助抽取非结构化文本（向后兼容）
      llm_registry: 可选的 LLM 注册中心，支持多 provider 链式降级（推荐）
    """

    def __init__(
        self,
        ontology: Ontology,
        graph_store: GraphStore,
        vector_store: VectorStore,
        mode: str = "auto",
        llm_func: Optional[Callable[[str], str]] = None,
        llm_registry: Optional["LLMRegistry"] = None,
    ):
        self.ontology = ontology
        self.graph = graph_store
        self.vector = vector_store
        self.mode = mode
        self.strict = (mode == "strict")

        # 抽取器: 优先 LLM，降级到规则
        self.extractor = LLMExtractor(
            ontology, llm_func=llm_func, llm_registry=llm_registry
        )

        # 自动本体发现器（从 ontology 读取关键词配置）
        self.discovery = AutoOntologyDiscovery(ontology)

        self._ingested_files: List[str] = []

    def ingest_file(self, file_path: str) -> dict:
        """
        摄入单个文件，返回统计信息。
        根据扩展名自动选择解析器。
        """
        ext = os.path.splitext(file_path)[1].lower()

        if ext == ".md" or ext == ".markdown" or ext == ".txt":
            return self._ingest_markdown(file_path)
        elif ext == ".json":
            return self._ingest_json(file_path)
        elif ext == ".csv":
            return self._ingest_csv(file_path)
        else:
            return {"error": f"Unsupported file type: {ext}"}

    def ingest_directory(self, dir_path: str) -> dict:
        """摄入目录下所有支持的文件"""
        stats = {"files": 0, "entities": 0, "relations": 0, "errors": []}
        for root, dirs, files in os.walk(dir_path):
            for fname in sorted(files):
                fpath = os.path.join(root, fname)
                ext = os.path.splitext(fname)[1].lower()
                if ext in (".md", ".markdown", ".txt", ".json", ".csv"):
                    result = self.ingest_file(fpath)
                    if "error" in result:
                        stats["errors"].append(result["error"])
                    else:
                        stats["files"] += 1
                        stats["entities"] += result.get("entities", 0)
                        stats["relations"] += result.get("relations", 0)
        return stats

    def _ingest_markdown(self, file_path: str) -> dict:
        """摄入 Markdown 文件"""
        with open(file_path, "r", encoding="utf-8") as f:
            text = f.read()

        # auto模式: 先进行本体发现
        if not self.strict:
            self.discovery.discover_from_text(text, source=file_path)

        # 构建已知实体索引
        known_entities = self._build_known_entity_index()

        # 抽取 (LLM或规则)
        extraction = self.extractor.extract_from_text(
            text, source_file=file_path, known_entities=known_entities
        )

        # 写入图谱
        entity_count = 0
        relation_count = 0
        eid_map: Dict[str, str] = {}

        for entity in extraction.entities:
            # 柔性校验
            errors = self.ontology.validate_entity(
                entity.entity_type, entity.attributes, strict=self.strict
            )
            if errors:
                continue
            eid = self.graph.add_entity(entity)
            eid_map[entity.eid] = eid
            entity_count += 1

        for rel in extraction.relations:
            src_eid = eid_map.get(rel.source_eid, rel.source_eid)
            tgt_eid = eid_map.get(rel.target_eid, rel.target_eid)
            if src_eid and tgt_eid:
                rel.source_eid = src_eid
                rel.target_eid = tgt_eid
                # 柔性校验关系
                src_entity = self.graph.get_entity(src_eid)
                tgt_entity = self.graph.get_entity(tgt_eid)
                if src_entity and tgt_entity:
                    errors = self.ontology.validate_relation(
                        rel.relation_type,
                        src_entity.entity_type,
                        tgt_entity.entity_type,
                        strict=self.strict,
                    )
                    if not errors:
                        if self.graph.add_relation(rel):
                            relation_count += 1

        # 写入向量索引
        doc_id = os.path.relpath(file_path)
        self.vector.add_document(doc_id, text, {
            "file": file_path,
            "type": "markdown",
            "entities": [e.name for e in extraction.entities],
        })

        self._ingested_files.append(file_path)
        return {
            "file": file_path,
            "entities": entity_count,
            "relations": relation_count,
        }

    def _ingest_json(self, file_path: str) -> dict:
        """摄入 JSON 文件"""
        with open(file_path, "r", encoding="utf-8") as f:
            data = json.load(f)

        # auto模式: 本体发现
        if not self.strict:
            self.discovery.discover_from_structured(data, source=file_path)

        # 支持 {"items": [...], "relations": [...]} 或 [...] 或 {...}
        items = data if isinstance(data, list) else data.get("items", [data])
        top_relations = data.get("relations", []) if isinstance(data, dict) else []

        entity_count = 0
        relation_count = 0

        # Phase 1: 摄入所有实体
        for item in items:
            if not isinstance(item, dict):
                continue

            extraction = self.extractor.extract_from_structured(item, source_file=file_path)

            eid_map: Dict[str, str] = {}
            for entity in extraction.entities:
                errors = self.ontology.validate_entity(
                    entity.entity_type, entity.attributes, strict=self.strict
                )
                if errors:
                    continue
                eid = self.graph.add_entity(entity)
                eid_map[entity.eid] = eid
                entity_count += 1

            # 构建文本用于向量索引
            text = json.dumps(item, ensure_ascii=False)
            doc_id = f"{file_path}#{entity_count}"
            self.vector.add_document(doc_id, text, {
                "file": file_path,
                "type": "json",
                "data": item,
            })

        # Phase 2: 处理顶层关系（实体已全部入库，可按名称查找）
        for r_data in top_relations:
            rel_type = r_data.get("relation_type", "")
            src_name = r_data.get("source", "")
            tgt_name = r_data.get("target", "")
            src_entity = self._find_entity_by_name(src_name, r_data.get("source_type"))
            tgt_entity = self._find_entity_by_name(tgt_name, r_data.get("target_type"))
            if src_entity and tgt_entity:
                errors = self.ontology.validate_relation(
                    rel_type, src_entity.entity_type, tgt_entity.entity_type,
                    strict=self.strict,
                )
                if not errors:
                    rel = Relation(
                        relation_type=rel_type,
                        source_eid=src_entity.eid,
                        target_eid=tgt_entity.eid,
                    )
                    if self.graph.add_relation(rel):
                        relation_count += 1

        self._ingested_files.append(file_path)
        return {
            "file": file_path,
            "entities": entity_count,
            "relations": relation_count,
        }

    def _ingest_csv(self, file_path: str) -> dict:
        """摄入 CSV 文件"""
        entity_count = 0
        relation_count = 0

        with open(file_path, "r", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for row in reader:
                # CSV 行需要有 entity_type 和 name 列
                etype = row.get("entity_type", "Document")
                ename = row.get("name", row.get("title", ""))
                if not ename:
                    continue

                attrs = {k: v for k, v in row.items()
                         if k not in ("entity_type", "name", "eid")}

                entity = Entity(entity_type=etype, name=ename, attributes=attrs)
                errors = self.ontology.validate_entity(
                    entity.entity_type, entity.attributes, strict=self.strict
                )
                if errors:
                    continue
                self.graph.add_entity(entity)
                entity_count += 1

                # 向量索引
                text = " ".join(f"{k}:{v}" for k, v in row.items())
                doc_id = f"{file_path}#{entity_count}"
                self.vector.add_document(doc_id, text, {
                    "file": file_path,
                    "type": "csv",
                })

        self._ingested_files.append(file_path)
        return {
            "file": file_path,
            "entities": entity_count,
            "relations": relation_count,
        }

    def _build_known_entity_index(self) -> Dict[Tuple[str, str], Entity]:
        """从图谱中构建已知实体索引"""
        index = {}
        for entity in self.graph.all_entities():
            index[(entity.entity_type, entity.name)] = entity
        return index

    def _find_entity_by_name(
        self, name: str, entity_type: Optional[str] = None
    ) -> Optional[Entity]:
        """按名称查找实体"""
        entities = self.graph.find_entities(name_pattern=name)
        if entity_type:
            entities = [e for e in entities if e.entity_type == entity_type]
        return entities[0] if entities else None

    def finalize(self):
        """摄入完成后：合并自动发现的本体 + 构建向量索引"""
        if not self.strict:
            added = self.discovery.merge_into(self.ontology)
            if added > 0:
                print(f"  自动发现 {added} 个新类型并合并到本体")
        self.vector.build_index()

    def export_discovered_ontology(self, path: str):
        """导出自动发现的本体到 YAML 文件（供人工微调）"""
        yaml_str = self.discovery.export_yaml()
        with open(path, "w", encoding="utf-8") as f:
            f.write(yaml_str)
