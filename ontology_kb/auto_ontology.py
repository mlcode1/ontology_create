"""
自动本体发现
============
从数据中自动识别实体类型和关系类型，无需用户预定义 ontology.yaml。

工作原理:
  1. 结构化数据 (JSON/CSV) — 从字段名和值推断实体类型
  2. 非结构化文本 (MD/TXT) — 从标题层级、段落模式推断实体类型
  3. 关系发现 — 从文本中提取 "X动词Y" 模式，自动归纳关系类型

发现的本体可以:
  - 直接使用（自动模式）
  - 导出为 YAML 供人工微调（渐进模式）
  - 与已有本体合并（增量模式）
"""

from __future__ import annotations

import re
import json
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Set, Tuple


@dataclass
class DiscoveredType:
    """自动发现的实体类型"""
    name: str
    sample_count: int = 0
    attributes: Dict[str, str] = field(default_factory=dict)  # attr_name -> inferred_type
    samples: List[str] = field(default_factory=list)  # 样本名称


@dataclass
class DiscoveredRelation:
    """自动发现的关系类型"""
    name: str
    source_types: Set[str] = field(default_factory=set)
    target_types: Set[str] = field(default_factory=set)
    sample_count: int = 0
    samples: List[Tuple[str, str]] = field(default_factory=list)


class AutoOntologyDiscovery:
    """自动本体发现器"""

    def __init__(self, ontology=None):
        """
        自动本体发现器。

        参数:
          ontology: 可选的已有 Ontology 对象。若提供，则从其读取
                    entity/relation keywords 作为推断依据；否则使用内置默认。
        """
        self.discovered_entities: Dict[str, DiscoveredType] = {}
        self.discovered_relations: Dict[str, DiscoveredRelation] = {}

        # 内置默认关键词（当未传入 ontology 或 ontology 中缺失时使用）
        _builtin_type_keywords = {
            "Product": ["产品", "平台", "系统", "引擎", "服务", "应用", "product", "platform"],
            "Department": ["部门", "团队", "组", "中心", "部", "室", "department", "team"],
            "Person": ["人员", "负责人", "主管", "工程师", "经理", "person", "谁"],
            "Process": ["流程", "制度", "规范", "流程", "procedure", "process"],
            "Document": ["文档", "手册", "说明", "报告", "指南", "document", "manual"],
            "System": ["系统", "工具", "框架", "数据库", "kubernetes", "gitlab", "hadoop"],
            "FAQ": ["问题", "faq", "怎么", "如何", "为什么", "能否"],
            "Project": ["项目", "计划", "project"],
            "Event": ["事件", "会议", "变更", "event"],
            "Metric": ["指标", "kpi", "sla", "metric"],
        }
        _builtin_relation_verbs = {
            "manages": ["管理", "负责", "主管", "带领"],
            "belongs_to": ["属于", "隶属", "归属", "隶属于"],
            "develops": ["开发", "维护", "研发", "实现"],
            "depends_on": ["依赖", "基于", "依靠", "需要"],
            "uses": ["使用", "采用", "利用"],
            "documents": ["记录", "描述", "说明", "详见"],
            "collaborates_with": ["协作", "配合", "协同"],
            "reports_to": ["汇报", "报告"],
            "located_in": ["位于", "在"],
            "created_by": ["创建", "发起", "提出"],
            "assigned_to": ["分配", "指派", "安排"],
            "contains": ["包含", "包括", "涵盖"],
        }

        # 从已有本体读取关键词（若有），与内置默认合并
        if ontology is not None:
            ont_entity_kws = {
                name: et.keywords
                for name, et in ontology.entity_types.items()
                if et.keywords
            }
            ont_relation_kws = {
                name: rt.keywords
                for name, rt in ontology.relation_types.items()
                if rt.keywords
            }
            # 合并：本体优先，内置兜底
            self._type_keywords = {**_builtin_type_keywords, **ont_entity_kws}
            self._relation_verbs = {**_builtin_relation_verbs, **ont_relation_kws}
        else:
            self._type_keywords = _builtin_type_keywords
            self._relation_verbs = _builtin_relation_verbs

    def discover_from_structured(self, data: Any, source: str = ""):
        """从结构化数据(JSON/CSV行)中发现实体类型"""
        if isinstance(data, dict):
            self._discover_from_dict(data)
        elif isinstance(data, list):
            for item in data:
                if isinstance(item, dict):
                    self._discover_from_dict(item)

    def _discover_from_dict(self, d: dict):
        """从字典中发现实体类型"""
        # 有 entity_type 字段 — 直接使用
        if "entity_type" in d:
            etype = d["entity_type"]
            name = d.get("name", d.get("title", "unknown"))
            self._register_entity(etype, name, d)
            return

        # 没有 entity_type — 推断
        etype = self._infer_type_from_dict(d)
        name = d.get("name", d.get("title", d.get("question", "unknown")))
        self._register_entity(etype, name, d)

    def _infer_type_from_dict(self, d: dict) -> str:
        """从字典字段推断实体类型"""
        keys = set(d.keys())
        text = " ".join(str(v) for v in d.values() if isinstance(v, (str, int, float)))

        # 基于字段名匹配
        if "question" in keys and "answer" in keys:
            return "FAQ"
        if "department" in keys or "parent" in keys or "head" in keys and "level" in keys:
            return "Department"
        if "employee_id" in keys or "title" in keys and "department" in keys:
            return "Person"
        if "version" in keys and "status" in keys:
            return "Product"
        if "category" in keys and "description" in keys:
            return "Process"
        if "doc_type" in keys or "source_file" in keys:
            return "Document"
        if "url" in keys and "type" in keys:
            return "System"

        # 基于内容关键词匹配
        for etype, keywords in self._type_keywords.items():
            if any(kw in text.lower() for kw in keywords):
                return etype

        return "Entity"  # 通用兜底类型

    def _register_entity(self, etype: str, name: str, d: dict):
        """注册发现的实体类型"""
        if etype not in self.discovered_entities:
            self.discovered_entities[etype] = DiscoveredType(name=etype)

        dt = self.discovered_entities[etype]
        dt.sample_count += 1
        if len(dt.samples) < 10:
            dt.samples.append(name)

        # 发现属性
        for key, value in d.items():
            if key in ("entity_type", "name", "title", "eid"):
                continue
            if key not in dt.attributes:
                dt.attributes[key] = self._infer_attr_type(value)

    def _infer_attr_type(self, value: Any) -> str:
        """推断属性类型"""
        if isinstance(value, bool):
            return "boolean"
        if isinstance(value, int):
            return "integer"
        if isinstance(value, float):
            return "float"
        if isinstance(value, list):
            return "list"
        if isinstance(value, dict):
            return "object"
        if isinstance(value, str):
            if len(value) > 200:
                return "text"
            return "string"
        return "string"

    def discover_from_text(self, text: str, source: str = ""):
        """从非结构化文本中发现实体和关系"""
        # 1. 从标题推断实体
        for m in re.finditer(r"^#{1,6}\s+(.+)$", text, re.MULTILINE):
            title = m.group(1).strip()
            etype = self._infer_type_from_text(title)
            self._register_entity_simple(etype, title)

        # 2. 从正文中的关键词推断实体
        for etype, keywords in self._type_keywords.items():
            for kw in keywords:
                # 找到关键词附近的实体名
                for m in re.finditer(rf"{kw}[:：\s]*([^\s,，。。；;\n]+)", text):
                    name = m.group(1).strip().strip("：:的")
                    if name and len(name) > 1:
                        self._register_entity_simple(etype, name)

        # 3. 从关系动词模式发现关系
        sentences = re.split(r"[。\n；;]", text)
        for sent in sentences:
            sent = sent.strip()
            if len(sent) < 5:
                continue
            for rel_name, verbs in self._relation_verbs.items():
                for verb in verbs:
                    pattern = rf"(.+?){verb}(.+?)"
                    m = re.search(pattern, sent)
                    if m:
                        src_name = m.group(1).strip()
                        tgt_name = m.group(2).strip()
                        # 推断类型
                        src_type = self._infer_type_from_text(src_name)
                        tgt_type = self._infer_type_from_text(tgt_name)
                        self._register_relation(rel_name, src_type, tgt_type, src_name, tgt_name)

    def _infer_type_from_text(self, text: str) -> str:
        """从文本片段推断实体类型"""
        for etype, keywords in self._type_keywords.items():
            if any(kw in text for kw in keywords):
                return etype
        # 如果是标题且较短，可能是实体名
        if len(text) <= 20:
            return "Entity"
        return "Document"

    def _register_entity_simple(self, etype: str, name: str):
        if etype not in self.discovered_entities:
            self.discovered_entities[etype] = DiscoveredType(name=etype)
        dt = self.discovered_entities[etype]
        dt.sample_count += 1
        if len(dt.samples) < 10:
            dt.samples.append(name)

    def _register_relation(
        self, name: str, src_type: str, tgt_type: str, src_name: str, tgt_name: str
    ):
        if name not in self.discovered_relations:
            self.discovered_relations[name] = DiscoveredRelation(name=name)
        dr = self.discovered_relations[name]
        dr.source_types.add(src_type)
        dr.target_types.add(tgt_type)
        dr.sample_count += 1
        if len(dr.samples) < 10:
            dr.samples.append((src_name, tgt_name))

    def export_yaml(self) -> str:
        """将发现的本体导出为 YAML 格式"""
        lines = ["# Auto-discovered Ontology", ""]

        lines.append("entity_types:")
        for etype, dt in sorted(self.discovered_entities.items()):
            lines.append(f"  {etype}:")
            lines.append(f"    description: \"Auto-discovered ({dt.sample_count} samples)\"")
            if dt.attributes:
                lines.append("    attributes:")
                for attr_name, attr_type in sorted(dt.attributes.items()):
                    lines.append(f"      {attr_name}: {{ type: {attr_type}, required: false }}")
            lines.append("")

        lines.append("relation_types:")
        for rname, dr in sorted(self.discovered_relations.items()):
            lines.append(f"  {rname}:")
            lines.append(f"    description: \"Auto-discovered ({dr.sample_count} samples)\"")
            src_list = list(dr.source_types) if dr.source_types else ["any"]
            tgt_list = list(dr.target_types) if dr.target_types else ["any"]
            lines.append(f"    domain: {src_list[0] if len(src_list) == 1 else src_list}")
            lines.append(f"    range: {tgt_list[0] if len(tgt_list) == 1 else tgt_list}")
            lines.append("")

        return "\n".join(lines)

    def merge_into(self, ontology) -> int:
        """
        将发现的本体合并到已有 Ontology 对象中。
        返回新增的类型数量。
        """
        from .ontology import EntityType, RelationType, AttributeConstraint

        added = 0
        for etype, dt in self.discovered_entities.items():
            if etype not in ontology.entity_types:
                et = EntityType(name=etype, description=f"Auto-discovered ({dt.sample_count} samples)")
                for attr_name, attr_type in dt.attributes.items():
                    et.attributes[attr_name] = AttributeConstraint(
                        name=attr_name, type=attr_type, required=False
                    )
                ontology.entity_types[etype] = et
                added += 1

        for rname, dr in self.discovered_relations.items():
            if rname not in ontology.relation_types:
                src = list(dr.source_types)[0] if len(dr.source_types) == 1 else (
                    list(dr.source_types) if dr.source_types else "any"
                )
                tgt = list(dr.target_types)[0] if len(dr.target_types) == 1 else (
                    list(dr.target_types) if dr.target_types else "any"
                )
                ontology.relation_types[rname] = RelationType(
                    name=rname,
                    description=f"Auto-discovered ({dr.sample_count} samples)",
                    domain=src,
                    range=tgt,
                )
                added += 1

        return added

    def summary(self) -> str:
        lines = [
            f"Auto-Discovered Ontology:",
            f"  Entity types: {len(self.discovered_entities)}",
        ]
        for name, dt in sorted(self.discovered_entities.items()):
            lines.append(f"    - {name}: {dt.sample_count} samples, {len(dt.attributes)} attrs")
            if dt.samples:
                lines.append(f"      samples: {dt.samples[:5]}")
        lines.append(f"  Relation types: {len(self.discovered_relations)}")
        for name, dr in sorted(self.discovered_relations.items()):
            lines.append(f"    - {name}: {dr.sample_count} samples ({dr.source_types} -> {dr.target_types})")
        return "\n".join(lines)
