"""
实体关系抽取
============
从非结构化文本中抽取实体和关系，受本体约束引导。

抽取策略：
  1. 规则匹配 — 基于本体中已定义的实体名称进行精确匹配
  2. 模式匹配 — 基于正则模式识别 "X属于Y", "X开发Y" 等关系
  3. 属性提取 — 从结构化数据中直接映射

本体的作用：
  - 限定抽取的实体类型和关系类型，避免抽取噪声
  - 校验抽取结果，丢弃不符合 domain/range 约束的三元组
"""

from __future__ import annotations

import re
from typing import Dict, List, Optional, Tuple

from .schema import Entity, Relation
from .ontology import Ontology


class ExtractionResult:
    """单次抽取的结果"""
    def __init__(self):
        self.entities: List[Entity] = []
        self.relations: List[Relation] = []
        # 抽取来源信息 (用于溯源)
        self.provenance: List[dict] = []

    def add_entity(self, entity: Entity, source: str = ""):
        self.entities.append(entity)
        if source:
            self.provenance.append({
                "entity": entity.name,
                "type": entity.entity_type,
                "source": source,
            })

    def add_relation(self, relation: Relation, source: str = ""):
        self.relations.append(relation)
        if source:
            self.provenance.append({
                "relation": relation.relation_type,
                "source_eid": relation.source_eid,
                "target_eid": relation.target_eid,
                "source": source,
            })


class _NullEntityType:
    """空实体类型兜底，用于 ontology.get_entity_type 返回 None 时"""
    keywords: list = []


_NullET = _NullEntityType()


class OntologyGuidedExtractor:
    """本体引导的实体关系抽取器"""

    def __init__(self, ontology: Ontology):
        self.ontology = ontology

    def _build_relation_patterns(self) -> List[Tuple[str, re.Pattern, str, str]]:
        """
        从本体关系类型的 keywords 构建正则匹配模式。
        keywords 列表用于生成 (?:kw1|kw2|...) 正则。
        长关键词在前，避免短关键词吞掉长关键词的前缀。
        """
        patterns = []
        for rt_name, rt in self.ontology.relation_types.items():
            if not rt.keywords:
                continue
            # 按长度降序排列，长匹配优先
            kws = sorted(rt.keywords, key=len, reverse=True)
            kw_pattern = "|".join(re.escape(kw) for kw in kws)
            # 正向: "X kw Y"
            pattern = re.compile(
                rf"([\u4e00-\u9fffA-Za-z0-9]{{2,20}})\s*(?:{kw_pattern})\s*([\u4e00-\u9fffA-Za-z0-9]{{2,20}})"
            )
            patterns.append((rt_name, pattern, "source", "target"))
            # 反向: "X由Y kw" — 仅对 develops 类关系生成
            if rt_name in ("develops", "manages"):
                rev_pattern = re.compile(
                    rf"([\u4e00-\u9fffA-Za-z0-9]{{2,20}})[，,\s]*由\s*([\u4e00-\u9fffA-Za-z0-9]{{2,20}})\s*(?:{kw_pattern})"
                )
                patterns.append((f"{rt_name}_reverse", rev_pattern, "target", "source"))
        return patterns

    def extract_from_text(
        self,
        text: str,
        source_file: str = "",
        known_entities: Optional[Dict[Tuple[str, str], Entity]] = None,
    ) -> ExtractionResult:
        """
        从文本中抽取实体和关系。

        参数:
          text: 输入文本
          source_file: 来源文件（用于溯源）
          known_entities: 已知实体索引 {(entity_type, name): Entity}
        """
        result = ExtractionResult()
        known_entities = known_entities or {}

        # 1. 匹配已知实体（基于名称在文本中出现）
        found_entities: Dict[str, Entity] = {}  # eid -> Entity
        for (etype, ename), entity in known_entities.items():
            if ename in text:
                found_entities[entity.eid] = entity

        # 2. 从结构化标记中抽取实体和关系（如 Markdown 表格、标题、FAQ）
        structured_entities, structured_relations = self._extract_structured_entities(text, source_file)
        for entity in structured_entities:
            found_entities[entity.eid] = entity
            result.add_entity(entity, source=source_file)
        # 表格关系直接加入结果（不依赖正则模式匹配）
        for rel in structured_relations:
            result.add_relation(rel, source=source_file)

        # 3. 关系模式匹配 — 在句子级别进行，从本体动态构建
        relation_patterns = self._build_relation_patterns()
        stopwords = set(self.ontology.extraction_stopwords)

        sentences = re.split(r"[。\n；;]", text)
        for sent in sentences:
            sent = sent.strip()
            if len(sent) < 3:
                continue
            for rel_type_raw, pattern, _, _ in relation_patterns:
                m = pattern.search(sent)
                if m:
                    # 处理 reverse 模式: "X由Y开发" → develops(Y, X)
                    is_reverse = rel_type_raw.endswith("_reverse")
                    rel_type = rel_type_raw.replace("_reverse", "") if is_reverse else rel_type_raw

                    src_name = self._clean_entity_name(m.group(1))
                    tgt_name = self._clean_entity_name(m.group(2))
                    if is_reverse:
                        src_name, tgt_name = tgt_name, src_name

                    # 跳过太短或明显是停用词的匹配
                    if len(src_name) < 2 or len(tgt_name) < 2:
                        continue
                    if src_name in stopwords or tgt_name in stopwords:
                        continue

                    # 尝试匹配到已知实体
                    src_entity = self._find_best_match(src_name, found_entities)
                    tgt_entity = self._find_best_match(tgt_name, found_entities)

                    # develops_reverse 特殊处理: "X是...，由Y开发"
                    # 正则捕获的 group1 可能是描述而非实体名，尝试从中提取真实实体名
                    if is_reverse and tgt_entity is None:
                        tgt_entity = self._extract_entity_from_text(m.group(1), found_entities)
                        if tgt_entity and tgt_entity.eid not in found_entities:
                            found_entities[tgt_entity.eid] = tgt_entity
                            result.add_entity(tgt_entity, source=source_file)

                    # 如果匹配不到已知实体，尝试推断类型并创建新实体
                    if not src_entity:
                        src_entity = self._infer_entity(src_name, rel_type, "source", sent)
                        if src_entity:
                            found_entities[src_entity.eid] = src_entity
                            result.add_entity(src_entity, source=source_file)
                    if not tgt_entity:
                        tgt_entity = self._infer_entity(tgt_name, rel_type, "target", sent)
                        if tgt_entity:
                            found_entities[tgt_entity.eid] = tgt_entity
                            result.add_entity(tgt_entity, source=source_file)

                    # 校验并添加关系
                    if src_entity and tgt_entity:
                        errors = self.ontology.validate_relation(
                            rel_type, src_entity.entity_type, tgt_entity.entity_type
                        )
                        if not errors:
                            rel = Relation(
                                relation_type=rel_type,
                                source_eid=src_entity.eid,
                                target_eid=tgt_entity.eid,
                            )
                            result.add_relation(rel, source=source_file)

        return result

    def _extract_entity_from_text(
        self, text: str, entities: Dict[str, Entity]
    ) -> Optional[Entity]:
        """
        从一段文本中提取最可能的实体。
        用于 develops_reverse 模式中 group1 含描述的情况，
        例如 "云端网关负责设备接入" → 提取 "云端网关"。

        策略:
          1. 优先匹配已知实体（最长匹配）
          2. 按实体后缀关键词启发式提取
        """
        # 1. 优先找已知实体（最长匹配，跳过短名 Document 实体）
        best = None
        best_len = 0
        for entity in entities.values():
            if entity.entity_type == "Document" and len(entity.name) <= 4:
                continue
            if entity.name in text and len(entity.name) > best_len:
                best = entity
                best_len = len(entity.name)
        if best:
            return best

        # 2. 启发式: 找以产品/系统关键词结尾的实体名
        #    选择文本中最早出现的后缀匹配（实体名通常在句首）
        _ENTITY_SUFFIXES = [
            "中控系统", "管理系统", "监控系统", "控制系统", "系统", "平台",
            "引擎", "模块", "子系统", "网关", "服务", "应用",
        ]
        best_name = ""
        best_pos = len(text) + 1  # 位置越靠前越优先
        for suffix in _ENTITY_SUFFIXES:
            idx = text.find(suffix)
            if idx == -1:
                continue
            # 向前扩展实体名: 取连续的中文/英文/数字字符
            start = idx
            while start > 0 and (
                "\u4e00" <= text[start - 1] <= "\u9fff"
                or text[start - 1].isalnum()
            ):
                start -= 1
            name = text[start:idx + len(suffix)]
            if len(name) < 2:
                continue
            # 排除明显是描述的片段
            if name.startswith(("是", "的", "和", "与", "由", "为")):
                continue
            # 优先选择位置最靠前的匹配（实体名通常在句首）
            if start < best_pos:
                best_name = name
                best_pos = start

        if best_name:
            inferred_type = self._guess_type_by_name(best_name)
            if inferred_type in ("Product", "System"):
                return Entity(entity_type=inferred_type, name=best_name)

        return None

    def _find_best_match(self, name: str, entities: Dict[str, Entity]) -> Optional[Entity]:
        """在已发现的实体中找最佳匹配"""
        name = name.strip().strip("的")
        # 精确匹配优先
        for entity in entities.values():
            if name == entity.name:
                return entity
        # 包含匹配（较长的名称优先）
        # 跳过 Document 类型的短名称实体（如 Markdown 标题 "产品"、"团队"），
        # 它们是结构化标记而非语义实体，子串匹配会产生误匹配
        candidates = []
        for entity in entities.values():
            if entity.entity_type == "Document" and len(entity.name) <= 4:
                continue
            # 要求匹配方名称至少3个字符，避免短词误匹配
            if len(entity.name) < 3 and len(name) > len(entity.name) * 2:
                continue
            if entity.name in name or name in entity.name:
                candidates.append(entity)
        if candidates:
            # 选择名称最长的（最具体的）
            return max(candidates, key=lambda e: len(e.name))
        return None

    @staticmethod
    def _clean_entity_name(name: str) -> str:
        """清理正则匹配出的实体名称"""
        name = name.strip()
        # 去除开头/结尾的停用词
        stop_chars = "的了是和在由对向被把将给为以用也"
        while name and name[0] in stop_chars:
            name = name[1:]
        while name and name[-1] in stop_chars:
            name = name[:-1]
        return name.strip()

    def _infer_entity(
        self, name: str, rel_type: str, role: str, context: str
    ) -> Optional[Entity]:
        """
        根据关系类型和上下文推断实体类型。
        本体约束在此发挥作用：如果关系是 develops，domain 是 Department/Person，
        则 source 推断为 Department。

        auto模式下本体可能为空，此时使用关键词启发式推断类型。
        """
        rt = self.ontology.get_relation_type(rel_type)

        if rt:
            constraint = rt.domain if role == "source" else rt.range
        else:
            # auto模式: 本体中没有该关系定义，用关系名推断
            constraint = self._guess_constraint(rel_type, role)

        if constraint == "any" or constraint is None:
            # 无法从关系约束推断，用关键词推断
            inferred_type = self._guess_type_by_name(name)
        elif isinstance(constraint, str):
            inferred_type = constraint
        elif isinstance(constraint, list):
            # 根据名字特征选择
            if "Person" in constraint and self._looks_like_person(name):
                inferred_type = "Person"
            elif "Department" in constraint and self._looks_like_department(name):
                inferred_type = "Department"
            elif "Product" in constraint and self._looks_like_product(name):
                inferred_type = "Product"
            elif "System" in constraint and self._looks_like_system(name):
                inferred_type = "System"
            elif "Document" in constraint:
                inferred_type = "Document"
            else:
                # 用关键词再试一次
                guessed = self._guess_type_by_name(name)
                inferred_type = guessed if guessed in constraint else constraint[0]
        else:
            return None

        # 构建属性
        attributes = {}
        if inferred_type == "Document":
            attributes["content"] = context
        elif inferred_type == "FAQ":
            attributes["question"] = name
            attributes["answer"] = context

        return Entity(entity_type=inferred_type, name=name, attributes=attributes)

    # ---- 类型推断启发式（从本体读取关键词） ----

    def _guess_type_by_name(self, name: str) -> str:
        """根据本体中实体类型的关键词推断实体类型"""
        name_lower = name.lower()
        # System 类型优先匹配（避免 "redis系统" 被分到 Product）
        for etype, keywords in self.ontology.get_entity_keywords().items():
            if etype == "System":
                for kw in keywords:
                    if kw in name_lower:
                        return etype
        # 其余类型按本体定义顺序匹配
        for etype, keywords in self.ontology.get_entity_keywords().items():
            if etype == "System":
                continue
            for kw in keywords:
                if kw in name_lower if kw.isascii() else kw in name:
                    return etype
        return "Entity"

    def _guess_constraint(self, rel_type: str, role: str):
        """从本体关系类型推断 domain/range 约束"""
        rt = self.ontology.get_relation_type(rel_type)
        if rt:
            return rt.domain if role == "source" else rt.range
        return "any"

    def _looks_like_person(self, name: str) -> bool:
        et = self.ontology.get_entity_type("Person")
        person_kws = et.keywords if et else []
        product_kws = (self.ontology.get_entity_type("Product") or _NullET).keywords
        dept_kws = (self.ontology.get_entity_type("Department") or _NullET).keywords
        if len(name) == 2 or len(name) == 3:
            return not any(kw in name for kw in product_kws + dept_kws)
        return any(kw in name for kw in person_kws)

    def _looks_like_department(self, name: str) -> bool:
        et = self.ontology.get_entity_type("Department")
        return bool(et and any(kw in name for kw in et.keywords))

    def _looks_like_product(self, name: str) -> bool:
        et = self.ontology.get_entity_type("Product")
        return bool(et and any(kw in name for kw in et.keywords))

    def _looks_like_system(self, name: str) -> bool:
        et = self.ontology.get_entity_type("System")
        return bool(et and any(kw in name.lower() for kw in et.keywords))

    def _extract_structured_entities(
        self, text: str, source_file: str
    ) -> Tuple[List[Entity], List[Relation]]:
        """
        从文本中的结构化标记抽取实体和关系。

        支持:
          - Markdown 标题 → Document 实体
          - FAQ 问答 → FAQ 实体
          - Markdown 表格 → 行/列实体 + 关系（见 _extract_markdown_tables）
        """
        entities: List[Entity] = []
        relations: List[Relation] = []

        # Markdown 标题作为 Document 实体
        for m in re.finditer(r"^#{1,6}\s+(.+)$", text, re.MULTILINE):
            title = m.group(1).strip()
            entities.append(Entity(
                entity_type="Document",
                name=title,
                attributes={
                    "title": title,
                    "source_file": source_file,
                    "doc_type": "spec",
                    "content": text,
                },
            ))

        # FAQ 模式: "Q: ... A: ..."
        for m in re.finditer(r"(?:^|\n)Q[:：]\s*(.+?)(?:\n|$)\s*A[:：]\s*(.+?)(?:\n|$)", text):
            q = m.group(1).strip()
            a = m.group(2).strip()
            entities.append(Entity(
                entity_type="FAQ",
                name=q,
                attributes={"question": q, "answer": a},
            ))

        # Markdown 链接: [text](url) → 提取为 Document 实体，附带 url 属性
        link_entities = self._extract_markdown_links(text, source_file)
        entities.extend(link_entities)

        # Markdown 表格
        table_entities, table_relations = self._extract_markdown_tables(text, source_file)
        entities.extend(table_entities)
        relations.extend(table_relations)

        return entities, relations

    # ---- Markdown 表格抽取 ----

    # 表头模板从本体读取（ontology.table_templates），无硬编码
    @property
    def _TABLE_HEADER_TEMPLATES(self):
        return self.ontology.table_templates

    # 人员单元格分隔符: 空格 / 逗号 / 中文逗号 / 斜杠
    _PERSON_CELL_SPLIT = re.compile(r"[\s,，/]+")

    # 工号正则: 6-8 位数字
    _EMPLOYEE_ID = re.compile(r"\d{6,8}")

    def _extract_markdown_tables(
        self, text: str, source_file: str
    ) -> Tuple[List[Entity], List[Relation]]:
        """
        解析 Markdown 表格，根据表头语义模板生成实体和关系。

        策略:
          1. 按行扫描，识别 |...| 形式的表格块
          2. 用表头匹配 _TABLE_HEADER_TEMPLATES 决定列的实体类型和关系
          3. 行首列 = 主实体，其余列 = 关联实体，建立 (主) --关系--> (关联) 三元组
          4. 人员列支持"姓名 工号 姓名 工号"多值拆分
        """
        entities: List[Entity] = []
        relations: List[Relation] = []
        seen_entity_keys: set = set()  # (entity_type, name) 去重

        def add_entity(etype: str, name: str, attrs: dict) -> Optional[Entity]:
            name = name.strip()
            if not name or len(name) < 1:
                return None
            key = (etype, name)
            if key in seen_entity_keys:
                # 已存在，返回 None 表示不重复添加（调用方不建关系）
                return None
            seen_entity_keys.add(key)
            e = Entity(entity_type=etype, name=name, attributes=attrs)
            entities.append(e)
            return e

        lines = text.splitlines()
        i = 0
        current_section = ""
        while i < len(lines):
            line = lines[i].strip()

            # 追踪当前所在的小节标题（## 或更深）
            heading_m = re.match(r"^#{1,6}\s+(.+)$", line)
            if heading_m:
                current_section = heading_m.group(1).strip()
                i += 1
                continue

            # 寻找表格起点: 含 | 的行
            if "|" not in line or line.startswith("```"):
                i += 1
                continue

            # 收集连续的表格行
            table_lines = []
            while i < len(lines) and "|" in lines[i].strip():
                table_lines.append(lines[i].strip())
                i += 1

            if len(table_lines) < 2:
                continue

            # 解析表格
            parsed = self._parse_md_table(table_lines)
            if not parsed:
                continue

            headers, rows = parsed
            header_key = tuple(h.strip() for h in headers)

            # 匹配表头模板
            template = self._match_table_template(header_key)
            if not template:
                # 未匹配模板，跳过（避免误抽取）
                continue

            header_keywords = template.headers
            col_types = template.column_types
            rel_type = template.relation_type

            # 找到主实体列在 header 中的位置（模板中第一个关键词对应主实体列）
            main_keyword = header_keywords[0]
            main_col_idx = self._find_header_index(headers, main_keyword)
            if main_col_idx is None:
                main_col_idx = 0

            # 构建列索引 → 列类型的映射
            col_type_map: Dict[int, str] = {}
            for col_idx, h in enumerate(headers):
                h_stripped = h.strip()
                # 尝试匹配到模板中的关键词位置
                kw_idx = self._find_keyword_index(header_keywords, h_stripped)
                if kw_idx is not None and kw_idx < len(col_types):
                    col_type_map[col_idx] = col_types[kw_idx]
                elif col_idx < len(col_types):
                    col_type_map[col_idx] = col_types[col_idx]

            for row in rows:
                if len(row) <= main_col_idx:
                    continue
                main_value = row[main_col_idx].strip()
                if not main_value:
                    continue

                main_type = col_type_map.get(main_col_idx, "Entity")

                # 收集属性列的值（先不创建实体）
                row_attributes: Dict[str, str] = {"source_file": source_file}
                for col_idx, cell_value in enumerate(row):
                    if col_idx == main_col_idx:
                        continue
                    col_type = col_type_map.get(col_idx, "")
                    if col_type == "attribute" and cell_value.strip():
                        # 属性列：用表头名作为属性 key
                        header_name = headers[col_idx].strip()
                        row_attributes[header_name] = cell_value.strip()

                # 创建主实体（带上属性列的值）
                main_entity = add_entity(main_type, main_value, row_attributes)
                if main_entity is None:
                    # 可能已存在，查找已添加的
                    for e in entities:
                        if e.entity_type == main_type and e.name == main_value:
                            main_entity = e
                            break

                # 处理非属性列（实体列）
                for col_idx, cell_value in enumerate(row):
                    if col_idx == main_col_idx:
                        continue
                    col_type = col_type_map.get(col_idx, "")
                    if col_type == "attribute":
                        continue  # 属性列已作为主实体属性处理
                    if not cell_value.strip():
                        continue

                    # 人员列: 多值拆分
                    if col_type == "Person":
                        persons = self._split_person_cell(cell_value)
                        for person_name, emp_id in persons:
                            attrs = {"source_file": source_file}
                            if emp_id:
                                attrs["employee_id"] = emp_id
                            person_entity = add_entity("Person", person_name, attrs)
                            if person_entity and main_entity:
                                relations.append(Relation(
                                    relation_type=rel_type,
                                    source_eid=main_entity.eid,
                                    target_eid=person_entity.eid,
                                    attributes={"section": current_section},
                                ))
                    else:
                        # 实体列: 整个单元格作为一个实体
                        # 多值用逗号/空格分隔时也拆分
                        values = re.split(r"[，,\s]+", cell_value.strip())
                        for v in values:
                            v = v.strip()
                            if not v:
                                continue
                            val_entity = add_entity(col_type, v, {
                                "source_file": source_file,
                            })
                            if val_entity and main_entity:
                                relations.append(Relation(
                                    relation_type=rel_type,
                                    source_eid=main_entity.eid,
                                    target_eid=val_entity.eid,
                                    attributes={"section": current_section},
                                ))

        return entities, relations

    def _parse_md_table(self, table_lines: List[str]) -> Optional[Tuple[List[str], List[List[str]]]]:
        """解析 Markdown 表格行，返回 (headers, rows)"""
        def split_row(row: str) -> List[str]:
            # 去掉首尾 |
            row = row.strip()
            if row.startswith("|"):
                row = row[1:]
            if row.endswith("|"):
                row = row[:-1]
            return [c.strip() for c in row.split("|")]

        if len(table_lines) < 2:
            return None

        headers = split_row(table_lines[0])

        # 第二行应该是分隔符 |---|---|
        sep_line = table_lines[1]
        if not re.match(r"^\s*\|?[\s\-:|]+\|?\s*$", sep_line):
            return None

        rows = []
        for line in table_lines[2:]:
            cells = split_row(line)
            if cells:
                rows.append(cells)

        return headers, rows

    def _match_table_template(
        self, header_key: Tuple[str, ...]
    ) -> Optional[Any]:
        """匹配表头到本体中的模板"""
        return self.ontology.find_table_template(header_key)

    def _find_header_index(self, headers: List[str], keyword: str) -> Optional[int]:
        """在表头中找到关键词所在列的索引"""
        for idx, h in enumerate(headers):
            if h.strip() == keyword:
                return idx
        return None

    @staticmethod
    def _find_keyword_index(keywords: Tuple[str, ...], header: str) -> Optional[int]:
        """在模板关键词中找到表头对应的索引位置"""
        for idx, kw in enumerate(keywords):
            if kw == header:
                return idx
        return None

    def _split_person_cell(self, cell: str) -> List[Tuple[str, str]]:
        """
        拆分人员单元格，返回 [(姓名, 工号), ...]。

        处理格式:
          "马亮 00828032 牛港 30077323 孙爱晶 30025793"
          → [("马亮", "00828032"), ("牛港", "30077323"), ("孙爱晶", "30025793")]

          "曹涵 30011832"
          → [("曹涵", "30011832")]
        """
        # 先用工号作为分隔点拆分
        # 策略: 找到所有工号位置，工号前面的文本段就是姓名
        results: List[Tuple[str, str]] = []

        tokens = self._PERSON_CELL_SPLIT.split(cell.strip())
        tokens = [t for t in tokens if t]

        i = 0
        while i < len(tokens):
            token = tokens[i]
            # 检查当前 token 是否是工号
            if self._EMPLOYEE_ID.fullmatch(token):
                # 工号前面的 token 是姓名
                if results and not results[-1][1]:
                    # 上一个姓名还没有工号，补上
                    name, _ = results[-1]
                    results[-1] = (name, token)
                else:
                    # 没有前置姓名，跳过孤立工号
                    pass
                i += 1
            else:
                # 当前 token 是姓名（可能带工号连在一起）
                # 检查是否 "姓名工号" 连在一起: "马亮00828032"
                m = re.match(r"^([\u4e00-\u9fffA-Za-z]{2,5})(\d{6,8})$", token)
                if m:
                    results.append((m.group(1), m.group(2)))
                else:
                    # 纯姓名，工号可能在下一个 token
                    results.append((token, ""))
                i += 1

        # 过滤掉空姓名
        return [(name, emp_id) for name, emp_id in results if name]

    def _extract_markdown_links(
        self, text: str, source_file: str
    ) -> List[Entity]:
        """
        从 Markdown 文本中提取链接，生成 Document 实体。

        处理格式:
          [常见问题定位案例](https://wiki.huawei.com/...)
          → Document 实体，name=链接文本，attributes.url=链接地址

        跳过:
          - 代码块内的链接
          - 图片链接 ![...]
          - 过短的链接文本
        """
        entities: List[Entity] = []
        seen_urls: set = set()

        # 按行处理，跳过代码块
        in_code_block = False
        for line in text.splitlines():
            stripped = line.strip()
            if stripped.startswith("```"):
                in_code_block = not in_code_block
                continue
            if in_code_block:
                continue
            # 跳过图片链接 ![...]
            cleaned = re.sub(r"!\[([^\]]*)\]\([^)]*\)", "", stripped)
            # 匹配 [text](url)
            for m in re.finditer(r"\[([^\]]+)\]\(([^)]+)\)", cleaned):
                link_text = m.group(1).strip()
                url = m.group(2).strip()
                if not link_text or not url:
                    continue
                # 跳过过短的链接文本
                if len(link_text) < 2:
                    continue
                # 跳过重复的 URL
                if url in seen_urls:
                    continue
                seen_urls.add(url)

                entities.append(Entity(
                    entity_type="Document",
                    name=link_text,
                    attributes={
                        "url": url,
                        "source_file": source_file,
                        "doc_type": "external_link",
                    },
                ))

        return entities

    def extract_from_structured(self, data: dict, source_file: str = "") -> ExtractionResult:
        """
        从结构化数据（JSON/dict）中抽取实体和关系。
        支持的格式：
          {"entities": [...], "relations": [...]}
          或直接 {"entity_type": "Product", "name": "...", ...}
        """
        result = ExtractionResult()

        if "entities" in data:
            for e_data in data["entities"]:
                etype = e_data.get("entity_type", "Document")
                ename = e_data.get("name", e_data.get("title", "unknown"))
                attrs = {k: v for k, v in e_data.items()
                         if k not in ("entity_type", "name", "eid")}
                entity = Entity(entity_type=etype, name=ename, attributes=attrs)
                result.add_entity(entity, source=source_file)
        elif "entity_type" in data:
            etype = data.get("entity_type", "Document")
            ename = data.get("name", data.get("title", "unknown"))
            attrs = {k: v for k, v in data.items()
                     if k not in ("entity_type", "name", "eid")}
            entity = Entity(entity_type=etype, name=ename, attributes=attrs)
            result.add_entity(entity, source=source_file)

        if "relations" in data:
            # 需要等实体都添加到图谱后才能解析 eid
            for r_data in data["relations"]:
                result.relations.append(Relation(
                    relation_type=r_data["relation_type"],
                    source_eid="",  # 稍后解析
                    target_eid="",
                    attributes=r_data.get("attributes", {}),
                ))
                result.provenance.append({
                    "relation": r_data["relation_type"],
                    "source_name": r_data.get("source", ""),
                    "target_name": r_data.get("target", ""),
                    "source": source_file,
                })

        return result
