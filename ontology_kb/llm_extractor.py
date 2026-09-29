"""
LLM 辅助抽取器
==============
将非结构化文本交给 LLM 抽取实体和关系，返回结构化三元组。

设计原则:
  1. 不绑定特定 LLM — 通过 LLMRegistry 或 callable 接口接入
  2. 本体引导 prompt — 把已知本体类型告诉 LLM，提升抽取一致性
  3. 降级策略 — LLM 失败或未配置时，自动降级到规则抽取
  4. 结果校验 — LLM 输出经过本体柔性校验，丢弃明显错误
  5. 默认关闭 — 不配 --llm-config 时走纯规则模式

使用方式:
  # 方式1: 通过 LLMRegistry（推荐，支持多 provider 链式降级）
  from ontology_kb.llm_registry import LLMRegistry
  registry = LLMRegistry.from_yaml("llm_config.yaml")
  extractor = LLMExtractor(ontology, llm_registry=registry)

  # 方式2: 传入自定义 LLM 函数（向后兼容）
  def my_llm(prompt: str) -> str:
      return call_chatgpt(prompt)
  extractor = LLMExtractor(ontology, llm_func=my_llm)

  # 方式3: 不传 LLM，自动降级到规则模式（默认行为）
  extractor = LLMExtractor(ontology)
"""

from __future__ import annotations

import json
import re
import logging
from typing import TYPE_CHECKING, Callable, Dict, List, Optional, Tuple

from .schema import Entity, Relation
from .ontology import Ontology
from .extractor import OntologyGuidedExtractor, ExtractionResult

if TYPE_CHECKING:
    from .llm_registry import LLMRegistry

logger = logging.getLogger(__name__)


class LLMExtractor:
    """LLM 辅助的实体关系抽取器"""

    def __init__(
        self,
        ontology: Ontology,
        llm_func: Optional[Callable[[str], str]] = None,
        llm_registry: Optional["LLMRegistry"] = None,
    ):
        """
        参数:
          ontology: 本体定义
          llm_func: 自定义 LLM 函数（向后兼容，二选一）
          llm_registry: LLM 注册中心（推荐，二选一）
        """
        self.ontology = ontology
        self.llm_func = llm_func
        self.llm_registry = llm_registry
        # 降级到规则抽取器
        self._rule_extractor = OntologyGuidedExtractor(ontology)

        if llm_registry and llm_func:
            logger.warning("Both llm_func and llm_registry provided; using llm_registry")

    def extract_from_structured(self, data: dict, source_file: str = "") -> ExtractionResult:
        """从结构化数据抽取 — 直接委托给规则抽取器"""
        return self._rule_extractor.extract_from_structured(data, source_file)

    def extract_from_text(
        self,
        text: str,
        source_file: str = "",
        known_entities: Optional[Dict[Tuple[str, str], Entity]] = None,
    ) -> ExtractionResult:
        """
        从文本抽取实体和关系。

        调用优先级:
          1. llm_registry（推荐） — 多 provider 链式降级
          2. llm_func（向后兼容） — 单函数调用
          3. 规则模式 — 无 LLM 时的兜底
        """
        prompt = self._build_prompt(text)

        # 优先使用 LLMRegistry（支持多 provider 链式降级）
        if self.llm_registry is not None:
            return self._try_llm_extract(prompt, text, source_file)

        # 向后兼容：直接使用 llm_func
        if self.llm_func is None:
            # 降级到规则模式
            return self._rule_extractor.extract_from_text(
                text, source_file, known_entities
            )

        # LLM 模式（单函数）
        try:
            response = self.llm_func(prompt)
            return self._parse_llm_response(response, text, source_file)
        except Exception as e:
            logger.warning("LLM call failed: %s, falling back to rule-based extraction", e)
            return self._rule_extractor.extract_from_text(
                text, source_file, known_entities
            )

    def _try_llm_extract(
        self,
        prompt: str,
        text: str,
        source_file: str,
    ) -> ExtractionResult:
        """
        通过 LLMRegistry 调用 LLM，失败时降级到规则模式。

        LLMRegistry.call() 内部已经做了 provider 链式降级，
        返回 None 表示所有 provider 都失败了。
        """
        if not self.llm_registry.is_enabled:
            return self._rule_extractor.extract_from_text(
                text, source_file, None
            )

        try:
            response = self.llm_registry.call(prompt)
            if response is None:
                # 所有 provider 都失败
                return self._rule_extractor.extract_from_text(
                    text, source_file, None
                )
            return self._parse_llm_response(response, text, source_file)
        except Exception as e:
            logger.warning("LLMRegistry call failed: %s, falling back to rule-based extraction", e)
            return self._rule_extractor.extract_from_text(
                text, source_file, None
            )

    def _build_prompt(self, text: str) -> str:
        """构建本体引导的抽取 prompt"""
        entity_types = self.ontology.all_entity_types()
        relation_types = self.ontology.all_relation_types()

        return f"""请从以下文本中抽取实体和关系，返回 JSON 格式。

已知本体类型（请尽量使用这些类型，如果是新类型也可以创建）:
  实体类型: {entity_types}
  关系类型: {relation_types}

输出格式:
```json
{{
  "entities": [
    {{"entity_type": "Product", "name": "实体名称", "attributes": {{"key": "value"}}}}
  ],
  "relations": [
    {{"relation_type": "develops", "source": "源实体名", "target": "目标实体名"}}
  ]
}}
```

规则:
1. 实体名称要准确，不要截断
2. 关系的 source 和 target 必须是已抽取的实体名称
3. attributes 只包含文本中明确提到的信息
4. 如果文本中没有明确的实体关系，返回空数组

文本:
{text[:3000]}
"""

    def _parse_llm_response(
        self, response: str, original_text: str, source_file: str
    ) -> ExtractionResult:
        """解析 LLM 返回的 JSON"""
        result = ExtractionResult()

        # 尝试从 response 中提取 JSON
        json_str = self._extract_json(response)
        if not json_str:
            return self._rule_extractor.extract_from_text(
                original_text, source_file=source_file
            )

        try:
            data = json.loads(json_str)
        except json.JSONDecodeError:
            return self._rule_extractor.extract_from_text(
                original_text, source_file=source_file
            )

        # 解析实体
        for e_data in data.get("entities", []):
            etype = e_data.get("entity_type", "Entity")
            ename = e_data.get("name", "")
            if not ename:
                continue
            attrs = e_data.get("attributes", {})
            entity = Entity(entity_type=etype, name=ename, attributes=attrs)
            result.add_entity(entity, source=source_file)

        # 解析关系
        entity_map = {e.name: e for e in result.entities}
        for r_data in data.get("relations", []):
            rel_type = r_data.get("relation_type", "")
            src_name = r_data.get("source", "")
            tgt_name = r_data.get("target", "")
            src_entity = entity_map.get(src_name)
            tgt_entity = entity_map.get(tgt_name)
            if src_entity and tgt_entity:
                rel = Relation(
                    relation_type=rel_type,
                    source_eid=src_entity.eid,
                    target_eid=tgt_entity.eid,
                )
                result.add_relation(rel, source=source_file)

        return result

    @staticmethod
    def _extract_json(text: str) -> Optional[str]:
        """从文本中提取 JSON 块"""
        # 尝试 ```json ... ``` 格式
        m = re.search(r"```json\s*(.+?)\s*```", text, re.DOTALL)
        if m:
            return m.group(1)
        # 尝试直接找 { ... }
        m = re.search(r"\{.*\}", text, re.DOTALL)
        if m:
            return m.group(0)
        return None
