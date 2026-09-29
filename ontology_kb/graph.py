"""
图谱存储 (NetworkX)
===================
使用 NetworkX 有向图存储实体和关系，
支持 JSON 文件持久化（无需外部数据库）。

图存储是"本体+图谱"查询的基础：
  - 实体 = 节点 (携带类型与属性)
  - 关系 = 有向边 (携带类型与属性)
  - 查询 = 图遍历 (BFS/DFS/路径查找)
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Set, Tuple
import json
import os

try:
    import networkx as nx
except ImportError:
    raise ImportError("networkx is required: pip install networkx")

from .schema import Entity, Relation


class GraphStore:
    """基于 NetworkX 的图谱存储"""

    def __init__(self):
        self.graph = nx.MultiDiGraph()
        # eid -> Entity 的索引
        self._entity_index: Dict[str, Entity] = {}
        # (entity_type, name) -> eid 的索引，用于去重和查找
        self._name_index: Dict[Tuple[str, str], str] = {}

    # ---- 实体操作 ----

    def add_entity(self, entity: Entity) -> str:
        """添加实体，如果已存在则合并属性"""
        existing = self._entity_index.get(entity.eid)
        if existing:
            # 合并属性
            for k, v in entity.attributes.items():
                if k not in existing.attributes:
                    existing.attributes[k] = v
            return entity.eid

        self._entity_index[entity.eid] = entity
        self._name_index[(entity.entity_type, entity.name)] = entity.eid
        self.graph.add_node(
            entity.eid,
            entity_type=entity.entity_type,
            name=entity.name,
            **entity.attributes,
        )
        return entity.eid

    def get_entity(self, eid: str) -> Optional[Entity]:
        return self._entity_index.get(eid)

    def get_entity_by_name(self, entity_type: str, name: str) -> Optional[Entity]:
        eid = self._name_index.get((entity_type, name))
        return self._entity_index.get(eid) if eid else None

    def find_entities(
        self,
        entity_type: Optional[str] = None,
        name_pattern: Optional[str] = None,
    ) -> List[Entity]:
        """按类型和/或名称模式查找实体"""
        results = []
        for entity in self._entity_index.values():
            if entity_type and entity.entity_type != entity_type:
                continue
            if name_pattern and name_pattern.lower() not in entity.name.lower():
                continue
            results.append(entity)
        return results

    def all_entities(self) -> List[Entity]:
        return list(self._entity_index.values())

    # ---- 关系操作 ----

    def add_relation(self, relation: Relation) -> bool:
        """添加关系边"""
        if relation.source_eid not in self._entity_index:
            return False
        if relation.target_eid not in self._entity_index:
            return False
        self.graph.add_edge(
            relation.source_eid,
            relation.target_eid,
            relation_type=relation.relation_type,
            **relation.attributes,
        )
        return True

    def get_relations(
        self,
        source_eid: Optional[str] = None,
        target_eid: Optional[str] = None,
        relation_type: Optional[str] = None,
    ) -> List[Relation]:
        """查询关系"""
        results = []
        edges = self.graph.edges(data=True, keys=True)

        for u, v, key, data in edges:
            if source_eid and u != source_eid:
                continue
            if target_eid and v != target_eid:
                continue
            if relation_type and data.get("relation_type") != relation_type:
                continue
            rt = data.pop("relation_type", None) if False else data.get("relation_type")
            results.append(Relation(
                relation_type=data.get("relation_type", ""),
                source_eid=u,
                target_eid=v,
                attributes={k: val for k, val in data.items() if k != "relation_type"},
            ))
        return results

    def get_neighbors(
        self,
        eid: str,
        relation_type: Optional[str] = None,
        direction: str = "out",  # "out", "in", "both"
    ) -> List[Tuple[str, str, Entity]]:
        """
        获取邻居节点。
        返回 [(relation_type, direction, neighbor_entity), ...]
        """
        results = []
        if direction in ("out", "both"):
            for _, target, data in self.graph.out_edges(eid, data=True):
                rt = data.get("relation_type", "")
                if relation_type and rt != relation_type:
                    continue
                neighbor = self._entity_index.get(target)
                if neighbor:
                    results.append((rt, "out", neighbor))

        if direction in ("in", "both"):
            for source, _, data in self.graph.in_edges(eid, data=True):
                rt = data.get("relation_type", "")
                if relation_type and rt != relation_type:
                    continue
                neighbor = self._entity_index.get(source)
                if neighbor:
                    results.append((rt, "in", neighbor))

        return results

    def find_path(
        self,
        source_eid: str,
        target_eid: str,
        max_depth: int = 5,
    ) -> Optional[List[Tuple[str, str, str]]]:
        """
        查找两个实体之间的最短路径（无向搜索，可沿关系双向遍历）。
        返回 [(eid, relation_type, eid), ...] 或 None
        """
        # 转为无向图进行路径查找
        undirected = self.graph.to_undirected()
        try:
            path = nx.shortest_path(undirected, source_eid, target_eid)
        except (nx.NetworkXNoPath, nx.NodeNotFound):
            return None

        if len(path) > max_depth + 1:
            return None

        result = []
        for i in range(len(path) - 1):
            u, v = path[i], path[i + 1]
            # 检查正向边
            edge_data = self.graph.get_edge_data(u, v)
            if edge_data:
                rt = list(edge_data.values())[0].get("relation_type", "")
                direction = "forward"
            else:
                # 检查反向边
                edge_data = self.graph.get_edge_data(v, u)
                if edge_data:
                    rt = list(edge_data.values())[0].get("relation_type", "")
                    direction = "reverse"
                else:
                    rt = ""
                    direction = "unknown"
            result.append((u, rt, v))
        return result

    def entity_count(self) -> int:
        return len(self._entity_index)

    def relation_count(self) -> int:
        return self.graph.number_of_edges()

    # ---- 持久化 ----

    def save(self, path: str):
        """保存到 JSON 文件"""
        data = {
            "entities": [e.to_dict() for e in self._entity_index.values()],
            "relations": [],
        }
        for u, v, d in self.graph.edges(data=True):
            rt = d.get("relation_type", "")
            attrs = {k: val for k, val in d.items() if k != "relation_type"}
            data["relations"].append({
                "relation_type": rt,
                "source_eid": u,
                "target_eid": v,
                "attributes": attrs,
            })

        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)

    def load(self, path: str):
        """从 JSON 文件加载"""
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)

        self.graph = nx.MultiDiGraph()
        self._entity_index.clear()
        self._name_index.clear()

        for e_dict in data.get("entities", []):
            entity = Entity.from_dict(e_dict)
            self._entity_index[entity.eid] = entity
            self._name_index[(entity.entity_type, entity.name)] = entity.eid
            self.graph.add_node(
                entity.eid,
                entity_type=entity.entity_type,
                name=entity.name,
                **entity.attributes,
            )

        for r_dict in data.get("relations", []):
            self.graph.add_edge(
                r_dict["source_eid"],
                r_dict["target_eid"],
                relation_type=r_dict["relation_type"],
                **r_dict.get("attributes", {}),
            )

    def stats(self) -> dict:
        type_counts: Dict[str, int] = {}
        for e in self._entity_index.values():
            type_counts[e.entity_type] = type_counts.get(e.entity_type, 0) + 1

        rel_counts: Dict[str, int] = {}
        for _, _, d in self.graph.edges(data=True):
            rt = d.get("relation_type", "")
            rel_counts[rt] = rel_counts.get(rt, 0) + 1

        return {
            "total_entities": self.entity_count(),
            "total_relations": self.relation_count(),
            "entity_type_counts": type_counts,
            "relation_type_counts": rel_counts,
        }
