#!/usr/bin/env python3
"""
查询知识库
===========
支持四种查询模式:
  1. 自然语言问答:  python query_kb.py "A产品由谁开发？"
  2. 实体查询:      python query_kb.py --entity "产品A"
  3. 关系查询:      python query_kb.py --relation develops --entity "产品A"
  4. 路径查询:      python query_kb.py --path "产品A" "部门B"

用法:
  python scripts/query_kb.py "问题" [--store kb_store/]
  python scripts/query_kb.py --entity "名称" [--type Product]
  python scripts/query_kb.py --relation develops --entity "名称"
  python scripts/query_kb.py --path "源实体" "目标实体"
"""

import argparse
import os
import sys
import json

project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, project_root)

from ontology_kb import Ontology, GraphStore, VectorStore, QueryEngine
from ontology_kb.default_ontology import build_default_ontology


def load_kb(store_dir: str):
    """加载已构建的知识库"""
    store_dir = os.path.join(project_root, store_dir) if not os.path.isabs(store_dir) else store_dir

    meta_path = os.path.join(store_dir, "meta.json")
    ontology_path = None
    if os.path.exists(meta_path):
        with open(meta_path, "r", encoding="utf-8") as f:
            meta = json.load(f)
        ontology_path = meta.get("ontology_file", "")
        # auto-discovered 模式下 ontology_path 可能是 "auto-discovered"
        if ontology_path and ontology_path != "auto-discovered" and os.path.exists(ontology_path):
            pass
        elif ontology_path and ontology_path != "auto-discovered":
            candidate = os.path.join(project_root, ontology_path) if not os.path.isabs(ontology_path) else ontology_path
            ontology_path = candidate if os.path.exists(candidate) else None
        else:
            ontology_path = None

    if ontology_path and os.path.exists(ontology_path):
        ontology = Ontology.load(ontology_path)
    else:
        # 使用默认本体（已包含全部实体/关系/模板元数据）
        ontology = build_default_ontology()

    graph = GraphStore()
    graph.load(os.path.join(store_dir, "graph.json"))
    vector = VectorStore()
    vector.load(os.path.join(store_dir, "vector_index.json"))

    # auto模式: 从图谱实体类型补充默认本体中未覆盖的类型
    from ontology_kb.auto_ontology import AutoOntologyDiscovery
    discovery = AutoOntologyDiscovery(ontology)
    for entity in graph.all_entities():
        discovery.discover_from_structured({
            "entity_type": entity.entity_type,
            "name": entity.name,
            **entity.attributes,
        })
    discovery.merge_into(ontology)
    # 从图谱边补充默认本体中未覆盖的关系类型
    from ontology_kb.ontology import RelationType
    seen_rels = set()
    for u, v, d in graph.graph.edges(data=True):
        rt = d.get("relation_type", "")
        if rt and rt not in seen_rels:
            src_entity = graph.get_entity(u)
            tgt_entity = graph.get_entity(v)
            if src_entity and tgt_entity:
                ontology.relation_types[rt] = RelationType(
                    name=rt,
                    domain=src_entity.entity_type,
                    range=tgt_entity.entity_type,
                )
            seen_rels.add(rt)

    return ontology, graph, vector


def main():
    parser = argparse.ArgumentParser(description="查询本体知识库")
    parser.add_argument("question", nargs="?", help="自然语言问题")
    parser.add_argument("--entity", help="实体查询: 实体名称")
    parser.add_argument("--type", help="实体类型过滤")
    parser.add_argument("--relation", help="关系查询: 关系类型")
    parser.add_argument("--path", nargs=2, metavar=("SOURCE", "TARGET"), help="路径查询")
    parser.add_argument("--store", default="kb_store", help="知识库存储目录")
    parser.add_argument("--json", action="store_true", help="输出JSON格式")
    args = parser.parse_args()

    # 加载知识库
    ontology, graph, vector = load_kb(args.store)
    engine = QueryEngine(ontology, graph, vector)

    # 执行查询
    if args.path:
        result = engine.query_path(args.path[0], args.path[1])
    elif args.relation and args.entity:
        result = engine.query_relation(args.entity, args.relation, args.type)
    elif args.entity:
        result = engine.query_entity(args.entity, args.type)
    elif args.question:
        result = engine.ask(args.question)
    else:
        parser.print_help()
        return

    # 输出结果
    if args.json:
        print(json.dumps(result.to_dict(), ensure_ascii=False, indent=2))
    else:
        print("=" * 60)
        print(f"查询: {result.query}")
        print(f"模式: {result.mode}")
        print(f"置信度: {result.confidence:.1%}")
        if result.explanation:
            print(f"推理: {result.explanation}")
        print("-" * 60)
        print(result.answer)

        # 如果有多余的三元组详情，折叠显示
        if len(result.triples) > 0 and result.mode == "natural_language":
            # 只显示未在答案中体现的额外三元组
            pass
        elif result.mode != "natural_language" and result.triples:
            # 结构化查询模式，额外显示三元组
            for t in result.triples:
                print(f"  {t}")

        print("=" * 60)


if __name__ == "__main__":
    main()
