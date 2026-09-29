"""
默认本体构建器
==============
将原先散布在 extractor.py / query.py / auto_ontology.py 中的
所有硬编码列表统一到一个 Ontology 对象中。

调用 build_default_ontology() 返回填充好的 Ontology。
也可导出为 YAML 供人工微调。

设计目标:
  - 新增文档类型只需扩展本文件或 YAML，不改 extractor/query 代码
  - 抽取器和查询引擎完全从 Ontology 对象读取元数据
"""

from __future__ import annotations

from .ontology import (
    Ontology,
    EntityType,
    RelationType,
    AttributeConstraint,
    TableTemplate,
)


def build_default_ontology() -> Ontology:
    """构建包含默认元数据的本体"""
    ont = Ontology()

    # ---- 实体类型 ----

    ont.entity_types["Product"] = EntityType(
        name="Product",
        description="产品/模块",
        keywords=["系统", "平台", "引擎", "模块", "子系统", "应用", "服务",
                  "产品", "网关", "中控", "助手"],
        query_keywords=["产品", "系统", "平台", "product", "模块"],
    )
    ont.entity_types["Department"] = EntityType(
        name="Department",
        description="部门/团队",
        keywords=["部门", "团队", "组", "中心", "部", "室", "院"],
        query_keywords=["部门", "团队", "组", "中心", "department"],
    )
    ont.entity_types["Person"] = EntityType(
        name="Person",
        description="人员",
        keywords=["负责人", "主管", "经理", "工程师", "总监", "组长"],
        query_keywords=["谁", "人员", "人", "负责人", "person"],
    )
    ont.entity_types["Process"] = EntityType(
        name="Process",
        description="流程/制度",
        keywords=["流程", "制度", "规范", "标准"],
        query_keywords=["流程", "制度", "规范", "process"],
    )
    ont.entity_types["Document"] = EntityType(
        name="Document",
        description="文档",
        keywords=["文档", "手册", "指南", "说明", "报告"],
        query_keywords=["文档", "说明", "手册", "document"],
    )
    ont.entity_types["FAQ"] = EntityType(
        name="FAQ",
        description="常见问题",
        query_keywords=["问题", "faq", "常见问题", "怎么", "如何"],
    )
    ont.entity_types["System"] = EntityType(
        name="System",
        description="系统/工具/框架",
        keywords=["redis", "mqtt", "kubernetes", "gitlab", "hadoop", "tensorflow",
                  "pytorch", "opencv", "echarts", "react", "keil", "spark",
                  "集群", "中间件", "服务器", "数据库", "框架", "库"],
        query_keywords=["系统", "工具", "平台", "system"],
    )

    # ---- 关系类型 ----

    ont.relation_types["manages"] = RelationType(
        name="manages",
        description="管理/负责",
        domain=["Person", "Department"],
        range=["Product", "Department"],
        keywords=["管理", "负责", "主管", "责任人", "负责人"],
        query_keywords=["管理", "负责", "主管", "责任人", "负责人"],
        chinese_predicate="管理",
        chinese_connector="的是",
    )
    ont.relation_types["belongs_to"] = RelationType(
        name="belongs_to",
        description="属于/隶属",
        domain=["Person", "Department"],
        range="Department",
        keywords=["属于", "隶属", "归属", "隶属于"],
        query_keywords=["属于", "隶属", "哪个部门"],
        chinese_predicate="属于",
        chinese_connector="",
    )
    ont.relation_types["develops"] = RelationType(
        name="develops",
        description="开发/维护",
        domain=["Department", "Person"],
        range="Product",
        keywords=["开发", "维护", "研发", "实现"],
        query_keywords=["开发", "维护", "研发"],
        chinese_predicate="开发",
        chinese_connector="的是",
    )
    ont.relation_types["depends_on"] = RelationType(
        name="depends_on",
        description="依赖/基于",
        domain=["Product", "System"],
        range=["Product", "System"],
        keywords=["依赖", "基于", "依靠", "需要"],
        query_keywords=["依赖", "基于", "需要"],
        chinese_predicate="依赖",
        chinese_connector="的是",
    )
    ont.relation_types["uses"] = RelationType(
        name="uses",
        description="使用/采用",
        domain=["Department", "Person", "Product"],
        range=["System", "Product"],
        keywords=["使用", "采用", "利用"],
        query_keywords=["使用", "采用"],
        chinese_predicate="使用",
        chinese_connector="",
    )
    ont.relation_types["documented_in"] = RelationType(
        name="documented_in",
        description="记录在/详见",
        domain=["Product", "Process", "System"],
        range="Document",
        keywords=["记录", "描述", "说明", "详见"],
        query_keywords=["文档", "说明", "记录在"],
        chinese_predicate="记录在",
        chinese_connector="",
    )
    ont.relation_types["answers"] = RelationType(
        name="answers",
        description="解答/回答",
        keywords=["解答", "回答"],
        query_keywords=["解答", "回答", "怎么", "如何"],
        chinese_predicate="解答",
        chinese_connector="",
    )
    ont.relation_types["reports_to"] = RelationType(
        name="reports_to",
        description="汇报给",
        domain="Person",
        range="Person",
        keywords=["汇报", "报告"],
        query_keywords=["汇报", "报告"],
        chinese_predicate="汇报给",
        chinese_connector="",
    )
    ont.relation_types["contains"] = RelationType(
        name="contains",
        description="包含/包括",
        domain="Department",
        range=["Person", "Department"],
        keywords=["包含", "包括", "涵盖"],
        query_keywords=["包含", "包括"],
        chinese_predicate="包含",
        chinese_connector="",
    )
    ont.relation_types["has_type"] = RelationType(
        name="has_type",
        description="字段类型",
        domain="Entity",
        range="Entity",
    )
    ont.relation_types["has_attribute"] = RelationType(
        name="has_attribute",
        description="属性",
        domain="Entity",
        range="Entity",
    )
    ont.relation_types["has_value"] = RelationType(
        name="has_value",
        description="配置值",
        domain="Entity",
        range="Entity",
    )
    ont.relation_types["described_as"] = RelationType(
        name="described_as",
        description="描述/说明",
        domain="Entity",
        range="Entity",
    )

    # ---- 表头模板 ----

    ont.table_templates = [
        TableTemplate(["模块", "人员"], ["Product", "Person"], "manages"),
        TableTemplate(["产品", "服务", "数据库"], ["Product", "System", "System"], "depends_on"),
        TableTemplate(["产品", "服务"], ["Product", "System"], "depends_on"),
        TableTemplate(["组件", "人员"], ["Product", "Person"], "manages"),
        TableTemplate(["功能", "负责人"], ["Product", "Person"], "manages"),
        TableTemplate(["模块", "负责人"], ["Product", "Person"], "manages"),
        TableTemplate(["产品", "负责人"], ["Product", "Person"], "manages"),
        TableTemplate(["系统", "人员"], ["System", "Person"], "manages"),
        TableTemplate(["系统", "依赖"], ["System", "System"], "depends_on"),
        TableTemplate(["名称", "值"], ["Entity", "attribute"], "has_attribute"),
        TableTemplate(["字段", "类型"], ["Entity", "attribute"], "has_type"),
        TableTemplate(["字段", "类型", "描述"], ["Entity", "attribute", "attribute"], "has_type"),
        TableTemplate(["配置项", "说明"], ["Entity", "attribute"], "described_as"),
        TableTemplate(["配置项", "值"], ["Entity", "attribute"], "has_value"),
        TableTemplate(["参数", "类型", "说明"], ["Entity", "attribute", "attribute"], "has_type"),
        TableTemplate(["名称", "类型"], ["Entity", "attribute"], "has_type"),
    ]

    # ---- 全局元数据 ----

    ont.section_keywords = ["在研", "维护"]
    ont.extraction_stopwords = [
        "属于", "管理", "负责", "开发", "维护", "使用", "依赖",
        "优先", "全自动", "半自动", "如果不行再选下一个方案",
        "方案", "规避", "临时", "通用", "手动",
    ]
    ont.person_cell_keywords = ["人员", "负责人"]

    return ont
