"""
本体加载与校验
==============
从 YAML 文件加载本体定义，提供：
  - 实体类型查询
  - 关系类型查询
  - 属性约束校验
  - 关系合法性校验 (domain/range 检查)
  - 抽取/查询所需的元数据 (关键词、模板、停用词等)

本体是整个系统的"单一事实来源"：
  - 抽取器从本体读取表头模板、关系正则、类型关键词
  - 查询引擎从本体读取意图检测关键词、section 关键词、中文谓词
  - 新增文档类型只需扩展 YAML，不改代码
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Set
import os

try:
    import yaml
except ImportError:
    raise ImportError("PyYAML is required: pip install pyyaml")


@dataclass
class AttributeConstraint:
    name: str
    type: str
    required: bool = False
    unique: bool = False
    values: Optional[List[str]] = None  # enum 时的可选值

    @classmethod
    def from_dict(cls, name: str, d: dict) -> "AttributeConstraint":
        return cls(
            name=name,
            type=d.get("type", "string"),
            required=d.get("required", False),
            unique=d.get("unique", False),
            values=d.get("values"),
        )


@dataclass
class EntityType:
    name: str
    description: str = ""
    attributes: Dict[str, AttributeConstraint] = field(default_factory=dict)
    # 抽取/查询元数据
    keywords: List[str] = field(default_factory=list)       # 名称关键词，用于类型推断
    query_keywords: List[str] = field(default_factory=list) # 查询意图关键词，用于检测问题中的实体类型


@dataclass
class RelationType:
    name: str
    description: str = ""
    domain: Any = None    # str 或 List[str] 或 "any"
    range: Any = None     # str 或 List[str] 或 "any"
    inverse: Optional[str] = None
    # 抽取/查询元数据
    keywords: List[str] = field(default_factory=list)       # 文本中的关系动词，用于正则匹配
    query_keywords: List[str] = field(default_factory=list) # 查询意图关键词，用于检测问题中的关系类型
    chinese_predicate: str = ""                             # 中文谓词，如 "管理"
    chinese_connector: str = ""                             # 中文连接词，如 "的是"


@dataclass
class TableTemplate:
    """表头语义模板"""
    headers: List[str]          # 表头关键词
    column_types: List[str]     # 每列类型 ("Product", "Person", "attribute" 等)
    relation_type: str          # 主实体到关联实体的关系类型


class Ontology:
    """本体管理器 — 单一事实来源"""

    def __init__(self):
        self.entity_types: Dict[str, EntityType] = {}
        self.relation_types: Dict[str, RelationType] = {}
        # 全局抽取/查询元数据
        self.table_templates: List[TableTemplate] = []
        self.section_keywords: List[str] = []           # 如 ["在研", "维护"]
        self.extraction_stopwords: List[str] = []       # 抽取时跳过的停用实体名
        self.person_cell_keywords: List[str] = []       # 人员列标记关键词（如 "人员", "负责人"）

    @classmethod
    def load(cls, path: str) -> "Ontology":
        """从 YAML 文件加载完整本体定义"""
        with open(path, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}

        ont = cls()

        # 实体类型
        for et_name, et_data in (data.get("entity_types") or {}).items():
            et = EntityType(
                name=et_name,
                description=et_data.get("description", ""),
                keywords=et_data.get("keywords", []),
                query_keywords=et_data.get("query_keywords", []),
            )
            for attr_name, attr_def in (et_data.get("attributes") or {}).items():
                et.attributes[attr_name] = AttributeConstraint.from_dict(attr_name, attr_def)
            ont.entity_types[et_name] = et

        # 关系类型
        for rt_name, rt_data in (data.get("relation_types") or {}).items():
            rt = RelationType(
                name=rt_name,
                description=rt_data.get("description", ""),
                domain=rt_data.get("domain", "any"),
                range=rt_data.get("range", "any"),
                inverse=rt_data.get("inverse"),
                keywords=rt_data.get("keywords", []),
                query_keywords=rt_data.get("query_keywords", []),
                chinese_predicate=rt_data.get("chinese_predicate", ""),
                chinese_connector=rt_data.get("chinese_connector", ""),
            )
            ont.relation_types[rt_name] = rt

        # 表头模板
        for tpl_data in data.get("table_templates") or []:
            ont.table_templates.append(TableTemplate(
                headers=tpl_data["headers"],
                column_types=tpl_data["column_types"],
                relation_type=tpl_data["relation_type"],
            ))

        # 全局元数据
        ont.section_keywords = data.get("section_keywords", [])
        ont.extraction_stopwords = data.get("extraction_stopwords", [])
        ont.person_cell_keywords = data.get("person_cell_keywords", [])

        return ont

    def to_dict(self) -> dict:
        """序列化为可 YAML 导出的字典"""
        data: dict = {"entity_types": {}, "relation_types": {}, "table_templates": []}

        for et_name, et in self.entity_types.items():
            et_dict: dict = {"description": et.description}
            if et.keywords:
                et_dict["keywords"] = et.keywords
            if et.query_keywords:
                et_dict["query_keywords"] = et.query_keywords
            if et.attributes:
                et_dict["attributes"] = {}
                for an, ac in et.attributes.items():
                    et_dict["attributes"][an] = {"type": ac.type}
                    if ac.required:
                        et_dict["attributes"][an]["required"] = True
            data["entity_types"][et_name] = et_dict

        for rt_name, rt in self.relation_types.items():
            rt_dict: dict = {"description": rt.description}
            rt_dict["domain"] = rt.domain
            rt_dict["range"] = rt.range
            if rt.inverse:
                rt_dict["inverse"] = rt.inverse
            if rt.keywords:
                rt_dict["keywords"] = rt.keywords
            if rt.query_keywords:
                rt_dict["query_keywords"] = rt.query_keywords
            if rt.chinese_predicate:
                rt_dict["chinese_predicate"] = rt.chinese_predicate
            if rt.chinese_connector:
                rt_dict["chinese_connector"] = rt.chinese_connector
            data["relation_types"][rt_name] = rt_dict

        for tpl in self.table_templates:
            data["table_templates"].append({
                "headers": tpl.headers,
                "column_types": tpl.column_types,
                "relation_type": tpl.relation_type,
            })

        if self.section_keywords:
            data["section_keywords"] = self.section_keywords
        if self.extraction_stopwords:
            data["extraction_stopwords"] = self.extraction_stopwords
        if self.person_cell_keywords:
            data["person_cell_keywords"] = self.person_cell_keywords

        return data

    def export_yaml(self, path: str):
        """导出本体到 YAML 文件"""
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            yaml.dump(self.to_dict(), f, allow_unicode=True, default_flow_style=False, sort_keys=False)

    # ---- 查询方法 ----

    def get_entity_type(self, name: str) -> Optional[EntityType]:
        return self.entity_types.get(name)

    def get_relation_type(self, name: str) -> Optional[RelationType]:
        return self.relation_types.get(name)

    def all_entity_types(self) -> List[str]:
        return list(self.entity_types.keys())

    def all_relation_types(self) -> List[str]:
        return list(self.relation_types.keys())

    def get_inverse(self, relation_type: str) -> Optional[str]:
        rt = self.relation_types.get(relation_type)
        return rt.inverse if rt else None

    def relations_for_entity_type(self, entity_type: str) -> List[str]:
        """返回该实体类型可以参与的所有关系类型"""
        result = []
        for rt_name, rt in self.relation_types.items():
            if self._type_matches(entity_type, rt.domain) or self._type_matches(entity_type, rt.range):
                result.append(rt_name)
        return result

    # ---- 抽取/查询元数据查询 ----

    def get_entity_keywords(self) -> Dict[str, List[str]]:
        """返回 {entity_type: keywords}，用于类型推断"""
        return {name: et.keywords for name, et in self.entity_types.items() if et.keywords}

    def get_entity_query_keywords(self) -> Dict[str, List[str]]:
        """返回 {entity_type: query_keywords}，用于查询意图检测"""
        return {name: et.query_keywords for name, et in self.entity_types.items() if et.query_keywords}

    def get_relation_keywords(self) -> Dict[str, List[str]]:
        """返回 {relation_type: keywords}，用于文本抽取"""
        return {name: rt.keywords for name, rt in self.relation_types.items() if rt.keywords}

    def get_relation_query_keywords(self) -> Dict[str, List[str]]:
        """返回 {relation_type: query_keywords}，用于查询意图检测"""
        return {name: rt.query_keywords for name, rt in self.relation_types.items() if rt.query_keywords}

    def get_relation_chinese(self) -> Dict[str, tuple]:
        """返回 {relation_type: (predicate, connector)}，用于自然语言生成"""
        result = {}
        for name, rt in self.relation_types.items():
            if rt.chinese_predicate:
                result[name] = (rt.chinese_predicate, rt.chinese_connector)
        return result

    def find_table_template(self, headers: tuple) -> Optional[TableTemplate]:
        """按表头关键词匹配模板"""
        norm = tuple(h.strip() for h in headers)
        for tpl in self.table_templates:
            if all(kw in norm for kw in tpl.headers):
                return tpl
        return None

    # ---- 校验方法 ----

    @staticmethod
    def _type_matches(entity_type: str, constraint: Any) -> bool:
        if constraint == "any" or constraint is None:
            return True
        if isinstance(constraint, str):
            return entity_type == constraint
        if isinstance(constraint, list):
            return entity_type in constraint
        return False

    def validate_entity(
        self, entity_type: str, attributes: Dict[str, Any], strict: bool = False
    ) -> List[str]:
        """校验实体属性是否符合本体约束"""
        errors = []
        et = self.entity_types.get(entity_type)
        if et is None:
            if strict:
                errors.append(f"Unknown entity type: {entity_type}")
            return errors

        for attr_name, constraint in et.attributes.items():
            if attr_name == "name":
                continue
            if constraint.required and attr_name not in attributes:
                errors.append(f"Missing required attribute '{attr_name}' for {entity_type}")

        for attr_name, value in attributes.items():
            constraint = et.attributes.get(attr_name)
            if constraint is None:
                continue
            if constraint.type == "enum" and constraint.values:
                if value not in constraint.values:
                    errors.append(
                        f"Attribute '{attr_name}' value '{value}' not in allowed values {constraint.values}"
                    )

        return errors

    def validate_relation(
        self, relation_type: str, source_type: str, target_type: str, strict: bool = False
    ) -> List[str]:
        """校验关系是否合法 (domain/range 检查)"""
        errors = []
        rt = self.relation_types.get(relation_type)
        if rt is None:
            if strict:
                errors.append(f"Unknown relation type: {relation_type}")
            return errors

        if not self._type_matches(source_type, rt.domain):
            if strict:
                errors.append(
                    f"Relation '{relation_type}' domain mismatch: "
                    f"source type '{source_type}' not in domain {rt.domain}"
                )
            self._extend_relation_constraint(rt, "range", target_type)

        return errors

    @staticmethod
    def _extend_relation_constraint(rt: RelationType, field_name: str, new_type: str):
        """柔性扩展关系的 domain/range 约束"""
        current = getattr(rt, field_name)
        if current == "any" or current is None:
            return
        if isinstance(current, str):
            if current != new_type:
                setattr(rt, field_name, [current, new_type])
        elif isinstance(current, list):
            if new_type not in current:
                current.append(new_type)

    def summary(self) -> str:
        lines = [
            f"Ontology Summary",
            f"  Entity types: {len(self.entity_types)}",
        ]
        for name, et in self.entity_types.items():
            lines.append(f"    - {name}: {et.description} ({len(et.attributes)} attrs)")
        lines.append(f"  Relation types: {len(self.relation_types)}")
        for name, rt in self.relation_types.items():
            lines.append(f"    - {name}: {rt.domain} -> {rt.range}")
        if self.table_templates:
            lines.append(f"  Table templates: {len(self.table_templates)}")
        if self.section_keywords:
            lines.append(f"  Section keywords: {self.section_keywords}")
        return "\n".join(lines)
