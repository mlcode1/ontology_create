"""
数据模型定义
============
Entity  — 知识库中的实体节点
Relation— 实体间的关系边
Triple  — (主语, 谓词, 宾语) 三元组
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, Optional
import hashlib
import json


def _stable_id(*parts: str) -> str:
    """根据组成部分生成稳定ID"""
    raw = "|".join(parts)
    return hashlib.md5(raw.encode("utf-8")).hexdigest()[:16]


@dataclass
class Entity:
    """知识库实体节点"""
    entity_type: str               # 本体中定义的实体类型
    name: str                      # 实体名称 (主标签)
    attributes: Dict[str, Any] = field(default_factory=dict)
    eid: str = field(default="")

    def __post_init__(self):
        if not self.eid:
            self.eid = _stable_id(self.entity_type, self.name)

    def to_dict(self) -> dict:
        return {
            "eid": self.eid,
            "entity_type": self.entity_type,
            "name": self.name,
            "attributes": self.attributes,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "Entity":
        return cls(
            entity_type=d["entity_type"],
            name=d["name"],
            attributes=d.get("attributes", {}),
            eid=d.get("eid", ""),
        )

    def __repr__(self):
        return f"Entity({self.entity_type}:{self.name})"

    def __hash__(self):
        return hash(self.eid)

    def __eq__(self, other):
        return isinstance(other, Entity) and self.eid == other.eid


@dataclass
class Relation:
    """实体间的关系边"""
    relation_type: str             # 本体中定义的关系类型
    source_eid: str
    target_eid: str
    attributes: Dict[str, Any] = field(default_factory=dict)

    @property
    def rid(self) -> str:
        return _stable_id(self.source_eid, self.relation_type, self.target_eid)

    def to_dict(self) -> dict:
        return {
            "relation_type": self.relation_type,
            "source_eid": self.source_eid,
            "target_eid": self.target_eid,
            "attributes": self.attributes,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "Relation":
        return cls(
            relation_type=d["relation_type"],
            source_eid=d["source_eid"],
            target_eid=d["target_eid"],
            attributes=d.get("attributes", {}),
        )


@dataclass
class Triple:
    """三元组 — 查询结果的基本单元"""
    source: Entity
    relation_type: str
    target: Entity

    def to_dict(self) -> dict:
        return {
            "source": self.source.to_dict(),
            "relation_type": self.relation_type,
            "target": self.target.to_dict(),
        }

    def __repr__(self):
        return f"({self.source.name}) --[{self.relation_type}]--> ({self.target.name})"
