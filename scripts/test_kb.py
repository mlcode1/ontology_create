#!/usr/bin/env python3
"""
测试脚本：构建知识库并验证查询功能
=================================
使用模拟数据测试本体知识库的完整流程：
1. 构建知识库（摄入数据）
2. 验证实体数量
3. 测试各种查询模式

用法:
  python scripts/test_kb.py
"""

import argparse
import os
import sys
import json

project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, project_root)

from ontology_kb import Ontology, GraphStore, VectorStore, QueryEngine
from ontology_kb.default_ontology import build_default_ontology
from ontology_kb.ingest import IngestionPipeline


def main():
    parser = argparse.ArgumentParser(description="测试知识库构建与查询")
    parser.add_argument("--data", default="data", help="测试数据目录")
    parser.add_argument("--output", default="kb_store", help="输出目录")
    args = parser.parse_args()

    data_dir = os.path.join(project_root, args.data) if not os.path.isabs(args.data) else args.data
    output_dir = os.path.join(project_root, args.output) if not os.path.isabs(args.output) else args.output

    print("=" * 70)
    print("知识库测试脚本")
    print("=" * 70)

    # 1. 初始化本体和存储
    print("\n[1] 初始化本体和存储")
    ontology = build_default_ontology()
    print(ontology.summary())
    
    graph = GraphStore()
    vector = VectorStore()

    # 2. 摄入测试数据
    print(f"\n[2] 摄入测试数据: {data_dir}")
    pipeline = IngestionPipeline(ontology, graph, vector, mode="auto")
    
    if not os.path.exists(data_dir):
        print(f"错误: 数据目录不存在: {data_dir}")
        return
    
    stats = pipeline.ingest_directory(data_dir)
    print(f"  ✓ 摄入 {stats['files']} 个文件")
    print(f"  ✓ 提取 {stats['entities']} 个实体")
    print(f"  ✓ 提取 {stats['relations']} 个关系")
    if stats['errors']:
        print(f"  ⚠ 错误: {len(stats['errors'])} 个")
        for err in stats['errors'][:3]:
            print(f"    - {err}")

    # 3. 构建索引
    print(f"\n[3] 构建索引")
    pipeline.finalize()
    print(f"  ✓ 向量索引文档数: {vector.doc_count()}")

    # 4. 知识库统计
    print(f"\n[4] 知识库统计")
    graph_stats = graph.stats()
    print(f"  总实体: {graph_stats['total_entities']}")
    print(f"  总关系: {graph_stats['total_relations']}")
    print(f"  实体类型分布:")
    for etype, count in sorted(graph_stats['entity_type_counts'].items()):
        print(f"    - {etype}: {count}")
    print(f"  关系类型分布:")
    for rtype, count in sorted(graph_stats['relation_type_counts'].items()):
        print(f"    - {rtype}: {count}")

    # 5. 测试查询
    print(f"\n[5] 测试查询功能")
    engine = QueryEngine(ontology, graph, vector)
    
    test_queries = [
        # 自然语言问答
        ("自然语言问答", "CRM系统由谁开发？"),
        ("自然语言问答", "支付平台依赖哪些系统？"),
        ("自然语言问答", "研发部有哪些成员？"),
        
        # 实体查询
        ("实体查询", ("CRM系统", "Product")),
        ("实体查询", ("研发部", "Department")),
        ("实体查询", ("张三", "Person")),
        
        # 关系查询
        ("关系查询", ("支付平台", "depends_on", None)),
        ("关系查询", ("研发部", "develops", None)),
    ]
    
    for query_type, query_param in test_queries:
        print(f"\n  [{query_type}]")
        try:
            if query_type == "自然语言问答":
                result = engine.ask(query_param)
                print(f"  问题: {query_param}")
                print(f"  置信度: {result.confidence:.1%}")
                print(f"  答案: {result.answer[:100]}..." if len(result.answer) > 100 else f"  答案: {result.answer}")
            elif query_type == "实体查询":
                entity_name, entity_type = query_param
                result = engine.query_entity(entity_name, entity_type)
                print(f"  查询: {entity_name} ({entity_type})")
                print(f"  结果数: {len(result.triples)}")
                if result.triples:
                    print(f"  示例: {result.triples[0]}")
            elif query_type == "关系查询":
                entity, relation, target_type = query_param
                result = engine.query_relation(entity, relation, target_type)
                print(f"  查询: {entity} --{relation}--> ?")
                print(f"  结果数: {len(result.triples)}")
                if result.triples:
                    print(f"  示例: {result.triples[0]}")
        except Exception as e:
            print(f"  ⚠ 查询失败: {e}")

    # 6. 保存结果
    print(f"\n[6] 保存到: {output_dir}")
    os.makedirs(output_dir, exist_ok=True)
    graph.save(os.path.join(output_dir, "graph.json"))
    vector.save(os.path.join(output_dir, "vector_index.json"))
    
    meta = {
        "test": True,
        "stats": graph_stats,
    }
    with open(os.path.join(output_dir, "meta.json"), "w", encoding="utf-8") as f:
        json.dump(meta, f, ensure_ascii=False, indent=2)
    
    print(f"  ✓ 图谱已保存: graph.json")
    print(f"  ✓ 向量索引已保存: vector_index.json")
    print(f"  ✓ 元数据已保存: meta.json")

    print("\n" + "=" * 70)
    print("测试完成!")
    print("=" * 70)
    print(f"\n后续操作:")
    print(f"  - 使用 scripts/query_kb.py 进行交互式查询")
    print(f"  - 查看 {output_dir}/ 目录下的构建产物")


if __name__ == "__main__":
    main()
