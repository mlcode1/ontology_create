#!/usr/bin/env python3
"""
构建知识库
===========
从 data/ 目录摄入知识，构建图谱和向量索引。

用法:
  # 自动模式（推荐）— 无需预定义本体，自动发现，柔性校验
  python scripts/build_kb.py --mode auto --data data/

  # 严格模式 — 使用 ontology.yaml 约束
  python scripts/build_kb.py --mode strict --ontology ontology.yaml --data data/

  # 自动模式 + 导出发现的本体
  python scripts/build_kb.py --mode auto --export-ontology discovered_ontology.yaml

  # 启用 LLM 辅助抽取（默认关闭，通过 --llm-config 开启）
  python scripts/build_kb.py --mode auto --data data/ --llm-config llm_config.yaml
"""

import argparse
import os
import sys
import json

project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, project_root)

from ontology_kb import Ontology, GraphStore, VectorStore
from ontology_kb.default_ontology import build_default_ontology
from ontology_kb.ingest import IngestionPipeline


def main():
    parser = argparse.ArgumentParser(description="构建本体知识库")
    parser.add_argument("--ontology", default=None, help="本体定义文件路径（auto模式可省略）")
    parser.add_argument("--data", default="data", help="数据目录")
    parser.add_argument("--output", default="kb_store", help="输出目录")
    parser.add_argument("--mode", choices=["auto", "strict"], default="auto",
                        help="auto=自动发现本体+柔性校验, strict=严格校验")
    parser.add_argument("--export-ontology", default=None,
                        help="导出自动发现的本体到指定YAML文件")
    parser.add_argument("--llm-config", default=None,
                        help="LLM 配置文件路径（默认关闭，不指定则使用纯规则抽取）")
    args = parser.parse_args()

    ontology_path = os.path.join(project_root, args.ontology) if args.ontology and not os.path.isabs(args.ontology) else (args.ontology or "")
    data_dir = os.path.join(project_root, args.data) if not os.path.isabs(args.data) else args.data
    output_dir = os.path.join(project_root, args.output) if not os.path.isabs(args.output) else args.output

    print("=" * 60)
    print(f"本体知识库构建器 (mode={args.mode})")
    print("=" * 60)

    # 1. 加载本体
    if args.mode == "strict" and ontology_path:
        print(f"\n[1] 加载本体: {ontology_path}")
        ontology = Ontology.load(ontology_path)
        print(ontology.summary())
    else:
        print(f"\n[1] 自动模式 — 使用默认本体 + 自动发现补充")
        ontology = build_default_ontology()
        print(ontology.summary())

    # 1.5. 加载 LLM 配置（默认关闭）
    llm_registry = None
    if args.llm_config:
        llm_config_path = os.path.join(project_root, args.llm_config) if not os.path.isabs(args.llm_config) else args.llm_config
        print(f"\n[1.5] 加载 LLM 配置: {llm_config_path}")
        from ontology_kb.llm_registry import LLMRegistry
        llm_registry = LLMRegistry.from_yaml(llm_config_path)
        if llm_registry.is_enabled:
            print(f"  LLM 已启用，可用 providers: {llm_registry.active_adapters}")
        else:
            print(f"  LLM 已配置但无可用 provider，将使用纯规则抽取")
    else:
        print(f"\n[1.5] LLM 未配置，使用纯规则抽取")

    # 2. 初始化存储
    print(f"\n[2] 初始化存储")
    graph = GraphStore()
    vector = VectorStore()

    # 3. 摄入数据
    print(f"\n[3] 摄入数据: {data_dir}")
    pipeline = IngestionPipeline(ontology, graph, vector, mode=args.mode, llm_registry=llm_registry)

    if os.path.isfile(data_dir):
        stats = pipeline.ingest_file(data_dir)
        print(f"  文件: {stats}")
    elif os.path.isdir(data_dir):
        stats = pipeline.ingest_directory(data_dir)
        print(f"  摄入 {stats['files']} 个文件")
        print(f"  实体: {stats['entities']}")
        print(f"  关系: {stats['relations']}")
        if stats["errors"]:
            print(f"  错误: {stats['errors']}")
    else:
        print(f"  数据路径不存在: {data_dir}")
        return

    # 4. 构建索引 + 合并自动发现的本体
    print(f"\n[4] 构建索引")
    pipeline.finalize()
    print(f"  向量索引文档数: {vector.doc_count()}")

    # 5. 统计
    print(f"\n[5] 知识库统计")
    graph_stats = graph.stats()
    print(f"  总实体: {graph_stats['total_entities']}")
    print(f"  总关系: {graph_stats['total_relations']}")
    print(f"  实体类型分布: {json.dumps(graph_stats['entity_type_counts'], ensure_ascii=False)}")
    print(f"  关系类型分布: {json.dumps(graph_stats['relation_type_counts'], ensure_ascii=False)}")

    # 6. 持久化
    print(f"\n[6] 保存到: {output_dir}")
    os.makedirs(output_dir, exist_ok=True)
    graph.save(os.path.join(output_dir, "graph.json"))
    vector.save(os.path.join(output_dir, "vector_index.json"))

    meta = {
        "ontology_file": ontology_path or "auto-discovered",
        "data_dir": data_dir,
        "mode": args.mode,
        "llm_config": args.llm_config,
        "stats": graph_stats,
    }
    with open(os.path.join(output_dir, "meta.json"), "w", encoding="utf-8") as f:
        json.dump(meta, f, ensure_ascii=False, indent=2)

    # 7. 导出发现的本体
    if args.export_ontology:
        export_path = os.path.join(project_root, args.export_ontology) if not os.path.isabs(args.export_ontology) else args.export_ontology
        pipeline.export_discovered_ontology(export_path)
        print(f"  自动发现的本体已导出到: {export_path}")

    print(f"\n构建完成! 知识库已保存到 {output_dir}/")
    print(f"  使用 scripts/query_kb.py 查询知识库")


if __name__ == "__main__":
    main()
