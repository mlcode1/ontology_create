"""
LLM 注册中心
============
可插拔的 LLM 管理模块，支持多 provider 链式降级。

设计原则:
  1. 配置驱动 — YAML 定义 provider 列表，环境变量注入密钥
  2. 优先级链式降级 — provider1 失败 → provider2 → ... → 规则模式
  3. 默认关闭 — 不配 --llm-config 时走纯规则模式
  4. 向后兼容 — 仍支持直接传 llm_func: Callable[[str], str]
"""

from __future__ import annotations

import os
import time
import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional

try:
    import yaml
except ImportError:
    yaml = None  # type: ignore

logger = logging.getLogger(__name__)


@dataclass
class LLMConfig:
    """单个 LLM provider 的配置"""
    name: str
    provider_type: str  # "openai" / "azure" / "mock" / "custom"
    model: str = ""
    api_key_env: str = ""       # API key 的环境变量名
    api_base_env: str = ""      # API base URL 的环境变量名（Azure 等需要）
    priority: int = 0           # 越小越优先
    timeout: int = 30           # 超时秒数
    extra: Dict[str, str] = field(default_factory=dict)  # 额外环境变量


class LLMAdapter(ABC):
    """LLM 适配器抽象基类 — 所有 LLM 实现需要遵循的协议"""

    @property
    @abstractmethod
    def name(self) -> str:
        """LLM 实现名称"""
        ...

    @abstractmethod
    def is_available(self) -> bool:
        """检查是否可用（API key 是否存在、依赖是否安装）"""
        ...

    @abstractmethod
    def call(self, prompt: str) -> str:
        """
        调用 LLM。
        成功时返回响应文本；失败时抛出异常（由注册中心捕获并降级）。
        """
        ...


class MockLLM(LLMAdapter):
    """Mock LLM — 测试用，始终返回空字符串以触发规则降级"""

    @property
    def name(self) -> str:
        return "mock"

    def is_available(self) -> bool:
        return True

    def call(self, prompt: str) -> str:
        logger.info("MockLLM: returning empty response to trigger rule fallback")
        return ""


# 内置 provider 构造器注册表
_BUILTIN_PROVIDERS: Dict[str, Callable[[LLMConfig], LLMAdapter]] = {
    "mock": lambda cfg: MockLLM(),
}


def register_provider(provider_type: str, factory: Callable[[LLMConfig], LLMAdapter]):
    """
    注册新的 LLM provider 构造器。

    使用方式:
        from ontology_kb.llm_registry import register_provider, LLMAdapter, LLMConfig

        class MyCustomLLM(LLMAdapter):
            ...

        register_provider("my_custom", lambda cfg: MyCustomLLM(cfg))

    然后在 YAML 中配置 provider_type: "my_custom" 即可。
    """
    _BUILTIN_PROVIDERS[provider_type] = factory


def _build_adapter(cfg: LLMConfig) -> Optional[LLMAdapter]:
    """根据配置构建 adapter，失败时返回 None"""
    factory = _BUILTIN_PROVIDERS.get(cfg.provider_type)
    if factory is None:
        logger.warning("Unknown LLM provider type: %s", cfg.provider_type)
        return None
    try:
        return factory(cfg)
    except Exception as e:
        logger.error("Failed to create LLM adapter '%s': %s", cfg.name, e)
        return None


class LLMRegistry:
    """
    LLM 注册中心 — 管理多个 LLM provider 并按优先级链式调用。

    调用流程:
      1. 按 priority 排序 provider
      2. 过滤 is_available() == True 的 provider
      3. 依次调用，成功即返回
      4. 全部失败时返回 None（由 LLMExtractor 降级到规则模式）
    """

    def __init__(self, adapters: Optional[List[LLMAdapter]] = None):
        self._adapters: List[LLMAdapter] = []
        if adapters:
            # 按优先级排序，过滤不可用的
            available = sorted(
                [a for a in adapters if a.is_available()],
                key=lambda a: 0,  # 已在外部排序
            )
            self._adapters = available
        if self._adapters:
            logger.info("LLMRegistry: %d adapter(s) available: %s",
                        len(self._adapters),
                        ", ".join(a.name for a in self._adapters))
        else:
            logger.info("LLMRegistry: no adapters available, will use rule-based extraction")

    @classmethod
    def from_yaml(cls, path: str) -> "LLMRegistry":
        """
        从 YAML 配置文件加载 LLM 注册中心。

        配置文件格式见 llm_config.yaml.example
        """
        if yaml is None:
            raise ImportError("PyYAML is required for LLM config: pip install pyyaml")

        with open(path, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}

        llm_data = data.get("llm", {})
        provider_configs = []

        for p in llm_data.get("providers", []):
            cfg = LLMConfig(
                name=p.get("name", ""),
                provider_type=p.get("type", ""),
                model=p.get("model", ""),
                api_key_env=p.get("api_key_env", ""),
                api_base_env=p.get("api_base_env", ""),
                priority=p.get("priority", 99),
                timeout=p.get("timeout", 30),
                extra={k: v for k, v in p.items()
                       if k not in ("name", "type", "model", "api_key_env",
                                    "api_base_env", "priority", "timeout")},
            )
            provider_configs.append(cfg)

        # 按优先级排序
        provider_configs.sort(key=lambda c: c.priority)

        # 构建 adapter
        adapters = []
        for cfg in provider_configs:
            adapter = _build_adapter(cfg)
            if adapter:
                adapters.append(adapter)

        return cls(adapters)

    @property
    def is_enabled(self) -> bool:
        """是否有可用的 LLM provider"""
        return len(self._adapters) > 0

    def call(self, prompt: str) -> Optional[str]:
        """
        链式调用 LLM。

        返回:
          - 成功时返回 LLM 响应文本
          - 所有 provider 都失败时返回 None（触发规则降级）
        """
        if not self._adapters:
            return None

        for adapter in self._adapters:
            try:
                result = adapter.call(prompt)
                if result:
                    logger.debug("LLMRegistry: '%s' responded successfully", adapter.name)
                    return result
                logger.debug("LLMRegistry: '%s' returned empty, trying next", adapter.name)
            except Exception as e:
                logger.warning("LLMRegistry: '%s' failed: %s, trying next", adapter.name, e)

        logger.warning("LLMRegistry: all providers failed, falling back to rule-based extraction")
        return None

    @property
    def active_adapters(self) -> List[str]:
        """当前可用的 adapter 名称列表"""
        return [a.name for a in self._adapters]

    def __repr__(self) -> str:
        adapters = self.active_adapters or ["(none)"]
        return f"LLMRegistry(adapters={adapters})"
