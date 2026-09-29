"""
Ontology-enhanced Knowledge Base Engine
========================================
基于本体的知识库引擎，通过实体类型约束和关系类型约束
提升知识查询的准确性。

核心组件:
  - schema:     数据模型 (Entity, Relation, Triple)
  - ontology:   本体加载与校验
  - graph:      图谱存储 (NetworkX)
  - vectorstore: 语义检索 (TF-IDF)
  - ingest:     知识摄入管道
  - extractor:  实体关系抽取
  - query:      混合查询引擎
"""

from .schema import Entity, Relation, Triple
from .ontology import Ontology
from .default_ontology import build_default_ontology
from .graph import GraphStore
from .vectorstore import VectorStore
from .query import QueryEngine
from .auto_ontology import AutoOntologyDiscovery
from .llm_extractor import LLMExtractor
from .llm_registry import LLMRegistry, LLMAdapter, register_provider
from .ingest import IngestionPipeline

__version__ = "0.3.0"
__all__ = [
    "Entity", "Relation", "Triple",
    "Ontology", "build_default_ontology", "GraphStore", "VectorStore",
    "QueryEngine", "AutoOntologyDiscovery", "LLMExtractor",
    "LLMRegistry", "LLMAdapter", "register_provider",
    "IngestionPipeline",
]
