# Ontology Skill - 本体增强知识库引擎

基于本体（Ontology）的企业知识库构建与查询系统。通过定义实体类型、关系类型和属性约束，将非结构化/半结构化文档转化为可推理的知识图谱 + 语义向量索引。

## 项目结构

```
ontology_skill/
├── ontology.yaml              # 本体定义（实体类型、关系类型、属性约束）
├── llm_config.yaml.example    # LLM 配置示例（可选，默认纯规则抽取）
├── ontology_kb/               # 核心引擎
│   ├── schema.py              # 数据模型 (Entity, Relation, Triple)
│   ├── ontology.py            # 本体加载与校验
│   ├── default_ontology.py    # 默认本体（企业知识领域）
│   ├── auto_ontology.py       # 自动本体发现
│   ├── graph.py               # 图谱存储 (NetworkX)
│   ├── vectorstore.py         # 语义检索 (TF-IDF)
│   ├── extractor.py           # 规则抽取器（本体引导）
│   ├── llm_extractor.py       # LLM 辅助抽取器
│   ├── llm_registry.py        # LLM Provider 注册中心
│   ├── ingest.py              # 知识摄入管道
│   └── query.py               # 混合查询引擎
├── scripts/
│   ├── build_kb.py            # 构建知识库
│   └── query_kb.py            # 查询知识库
├── data/
│   ├── documents/             # 非结构化文档 (Markdown/TXT)
│   └── structured/            # 结构化数据 (JSON/CSV)
└── kb_store/                  # 构建产物（图谱 + 向量索引）
```

## 快速开始

### 1. 安装依赖

```bash
pip install networkx pyyaml scikit-learn
```

### 2. 准备数据

将文档放入 `data/` 目录：
- `data/documents/` — 放 Markdown / TXT 文件（非结构化知识）
- `data/structured/` — 放 JSON / CSV 文件（结构化知识）

### 3. 构建知识库

```bash
# 自动模式（推荐）— 无需预定义本体，自动发现实体类型
python scripts/build_kb.py --mode auto --data data/

# 严格模式 — 使用 ontology.yaml 约束
python scripts/build_kb.py --mode strict --ontology ontology.yaml --data data/

# 自动模式 + LLM 辅助抽取
python scripts/build_kb.py --mode auto --data data/ --llm-config llm_config.yaml
```

### 4. 查询知识库

```bash
# 自然语言问答
python scripts/query_kb.py "A产品由谁开发？"

# 实体查询
python scripts/query_kb.py --entity "产品A" --type Product

# 关系查询
python scripts/query_kb.py --relation develops --entity "研发部"

# 路径查询（两个实体之间的关系链）
python scripts/query_kb.py --path "产品A" "技术VP"
```

## 本体定义

`ontology.yaml` 定义了知识库的"骨架"：

| 实体类型 | 描述 | 示例 |
|---------|------|------|
| Product | 产品或产品线 | CRM系统、支付平台 |
| Department | 部门或团队 | 研发部、产品部 |
| Person | 人员 | 张三、李四 |
| Process | 业务流程或制度 | 需求评审流程、上线审批 |
| Document | 文档或知识条目 | 技术设计文档、API手册 |
| FAQ | 常见问题 | "如何申请权限？" |
| System | IT系统或工具 | Jira、Confluence |

**关系类型**：manages / belongs_to / develops / depends_on / documented_in / uses / related_to 等。

## 两种运行模式

| 模式 | 说明 | 适用场景 |
|------|------|---------|
| **auto** | 自动发现本体 + 柔性校验，LLM/规则抽取 | 新领域探索、快速原型 |
| **strict** | 预定义本体 + 严格校验 | 生产环境、数据质量要求高 |

## LLM 集成（可选）

默认使用纯规则抽取，无需 LLM。如需启用 LLM 辅助：

```bash
cp llm_config.yaml.example llm_config.yaml
# 编辑 llm_config.yaml，配置 API Key 环境变量
export OPENAI_API_KEY=sk-xxx
python scripts/build_kb.py --mode auto --data data/ --llm-config llm_config.yaml
```

支持 OpenAI、Azure OpenAI、本地 Ollama/vLLM 等兼容接口，多 Provider 链式降级。

## 数据格式

### Markdown（非结构化）
直接放入 `data/documents/`，系统自动抽取实体和关系。

### JSON（结构化）
```json
{
  "items": [
    {
      "entity_type": "Product",
      "name": "CRM系统",
      "version": "3.0",
      "status": "released",
      "owner": "张三"
    }
  ],
  "relations": [
    {
      "relation_type": "manages",
      "source": "张三",
      "target": "CRM系统"
    }
  ]
}
```

### CSV（结构化）
需包含 `entity_type` 和 `name` 列，其余列作为属性。

## 许可证

MIT License
