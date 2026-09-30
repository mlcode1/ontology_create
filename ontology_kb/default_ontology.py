"""
默认本体构建器
==============
从 ontology.yaml 加载完整本体定义。

如果 ontology.yaml 存在，则从中加载所有配置；
如果不存在，则构建最小化的内置默认本体（兜底）。

设计目标:
  - 单一配置源：所有本体定义集中在 ontology.yaml
  - 向后兼容：build_default_ontology() 接口不变
  - 优雅降级：YAML 缺失时使用内置默认，不报错
"""

from __future__ import annotations

import os
from pathlib import Path

from .ontology import (
    Ontology,
    EntityType,
    RelationType,
    AttributeConstraint,
    TableTemplate,
)


def build_default_ontology() -> Ontology:
    """
    构建默认本体。

    优先从项目根目录的 ontology.yaml 加载；
    如果文件不存在，则构建最小化的内置默认。
    """
    # 尝试加载 ontology.yaml
    yaml_path = _find_ontology_yaml()
    if yaml_path and os.path.exists(yaml_path):
        return Ontology.load(yaml_path)

    # 兜底：构建最小化内置默认
    return _build_minimal_default()


def _find_ontology_yaml() -> str:
    """查找 ontology.yaml 的路径"""
    # 从当前文件位置向上查找项目根目录
    current = Path(__file__).parent
    for _ in range(5):  # 最多向上 5 层
        candidate = current / "ontology.yaml"
        if candidate.exists():
            return str(candidate)
        parent = current.parent
        if parent == current:
            break
        current = parent

    # 也检查常见的相对路径
    candidates = [
        Path("ontology.yaml"),
        Path("../ontology.yaml"),
        Path("../../ontology.yaml"),
    ]
    for candidate in candidates:
        if candidate.exists():
            return str(candidate.resolve())

    return ""


def _build_minimal_default() -> Ontology:
    """
    构建最小化的内置默认本体。

    仅在 ontology.yaml 缺失时使用，包含最基本的实体和关系类型。
    实际使用中应该始终提供 ontology.yaml。
    """
    ont = Ontology()

    # 最小化实体类型（仅包含最常用的）
    ont.entity_types["Product"] = EntityType(
        name="Product",
        description="产品/模块",
        keywords=["系统", "平台", "产品"],
        query_keywords=["产品", "系统"],
    )
    ont.entity_types["Person"] = EntityType(
        name="Person",
        description="人员",
        keywords=["负责人", "主管", "人员"],
        query_keywords=["谁", "人员"],
    )
    ont.entity_types["Department"] = EntityType(
        name="Department",
        description="部门",
        keywords=["部门", "团队"],
        query_keywords=["部门"],
    )

    # 最小化关系类型
    ont.relation_types["manages"] = RelationType(
        name="manages",
        description="管理",
        domain="Person",
        range="Product",
        keywords=["管理", "负责"],
        chinese_predicate="管理",
        chinese_connector="的是",
    )
    ont.relation_types["belongs_to"] = RelationType(
        name="belongs_to",
        description="属于",
        domain="Person",
        range="Department",
        keywords=["属于", "隶属"],
        chinese_predicate="属于",
        chinese_connector="",
    )

    return ont
