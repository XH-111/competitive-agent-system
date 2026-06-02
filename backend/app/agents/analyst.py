from collections import defaultdict

from app.agents.base import run_with_trace
from app.domain import domain_pack_reference, resolve_domain_pack
from app.schemas import (
    AnalystInput,
    AnalystOutput,
    CapabilityBucket,
    CapabilityMap,
    CapabilitySignal,
    Evidence,
    FeatureTree,
    PricingModel,
    ProductProfile,
    SwotAnalysis,
    SwotItem,
    UserPersona,
)
from app.services.evidence_relevance_service import is_relevant_evidence
from app.services.trace_service import TraceService


FEATURE_KEYWORDS = {
    "AI": ["ai", "artificial intelligence", "智能", "人工智能"],
    "automation": ["automation", "automated", "自动化"],
    "collaboration": ["collaboration", "collaborative", "team", "协作", "团队", "办公"],
    "pricing": ["pricing", "price", "plan", "定价", "价格", "套餐"],
    "integration": ["integration", "integrations", "集成"],
    "analytics": ["analytics", "analysis", "dashboard", "分析", "看板"],
    "security": ["security", "compliance", "安全", "合规"],
    "mobile": ["mobile", "app", "移动端"],
    "API": ["api", "developer", "接口", "开发者"],
    "workflow": ["workflow", "process", "流程"],
}

PRICING_KEYWORDS = [
    "free",
    "trial",
    "pricing",
    "subscription",
    "enterprise",
    "plan",
    "quote",
    "免费",
    "试用",
    "订阅",
    "企业版",
    "套餐",
    "定价",
    "价格",
]
PERSONA_KEYWORDS = {
    "企业团队": ["enterprise", "procurement", "企业"],
    "团队用户": ["team", "operations", "团队"],
    "开发者": ["developer", "engineering", "开发者"],
    "市场团队": ["marketer", "marketing", "市场"],
    "产品团队": ["product team", "product manager", "产品团队", "产品经理"],
    "学生": ["student", "education", "学生"],
}


class AnalystAgent:
    name = "AnalystAgent"

    def __init__(self, trace_service: TraceService):
        self.trace_service = trace_service

    def run(self, input_data: AnalystInput) -> AnalystOutput:
        task = input_data.task

        def produce() -> AnalystOutput:
            if input_data.analyst_mode == "mock":
                return self._mock_output(input_data, fallback_reason=None)
            if input_data.analyst_mode == "llm":
                return self._evidence_output(
                    input_data,
                    fallback_reason="analyst_mode=llm is not implemented in this phase; fallback to evidence mode.",
                )
            return self._evidence_output(input_data, fallback_reason=None)

        return run_with_trace(
            trace_service=self.trace_service,
            task_id=task.task_id,
            agent_name=self.name,
            to_agent="ReportWriterAgent",
            message_type="analysis",
            schema_name="AnalystOutput",
            input_summary=f"analyst_mode_requested={input_data.analyst_mode}; analyze {len(input_data.evidence)} evidence records",
            retry_count=input_data.retry_count,
            fn=produce,
        )

    def _mock_output(self, input_data: AnalystInput, fallback_reason: str | None) -> AnalystOutput:
        task = input_data.task
        ids = self._ids(input_data.evidence)
        selected_dimensions = self._selected_dimensions(input_data)
        capability_map = self._build_capability_map(task, input_data.evidence, competitor_order=task.competitors)
        diagnostics = self._diagnostics(
            input_data,
            "mock",
            ids,
            fallback_reason=fallback_reason,
            evidence_by_competitor=self._group_by_competitor(input_data.evidence, task.competitors),
            extracted_fields_by_competitor={
                competitor: {"profile": 1, "feature": 1, "pricing": 1, "persona": 1} for competitor in task.competitors
            },
        )
        competitor_analysis = {
            competitor: {
                "positioning": f"{competitor} 当前由 mock 竞品知识表示。",
                "features": ["collaboration", "workflow"],
                "pricing": ["付费或企业版信号"],
                "persona": ["企业团队"],
                "evidence_ids": [item.evidence_id for item in input_data.evidence if item.competitor == competitor] or ids[:1],
                "insufficient_evidence": False,
            }
            for competitor in task.competitors
        }
        profile = ProductProfile(
            product_name=task.product_name,
            positioning=""
            if input_data.force_invalid_extraction
            else f"{task.product_name} 是一个结构化竞品分析工作台。",
            target_segments=[]
            if input_data.force_invalid_extraction
            else ["产品市场团队", "战略团队", "销售赋能团队"],
            strengths=["证据可追溯", "结构化 Schema", "QA 反馈闭环"],
            weaknesses=["当前 Demo 仍使用简化抽取规则"],
            evidence_ids=ids[:2],
            extensions={
                "domain": {
                    "region": task.region,
                    "industry": task.industry,
                    "domain_pack": domain_pack_reference(
                        resolve_domain_pack(task),
                        industry_label=task.industry,
                        metadata={"owner": "AnalystAgent", "mode": "mock"},
                    ).model_dump(mode="json"),
                },
                "workflow": {
                    "analyst_mode": "mock",
                    "selected_dimensions": selected_dimensions,
                    "competitor_analysis": competitor_analysis,
                    "capability_map": capability_map.model_dump(mode="json"),
                },
            },
            custom_dimensions={
                "region": task.region,
                "industry": task.industry,
                "analyst_mode": "mock",
                "selected_dimensions": selected_dimensions,
                "competitor_analysis": competitor_analysis,
            },
        )
        feature_tree = FeatureTree(
            core_features=self._legacy_feature_tree_projection(capability_map) or {competitor: ["协作", "工作流", "定价"] for competitor in task.competitors},
            differentiators=self._legacy_differentiators(capability_map) or ["Claim 到 Evidence 的可追溯性", "人工复核兜底"],
            evidence_ids=ids,
        )
        pricing = PricingModel(
            model="分层 SaaS 对标模型",
            tiers=[f"{competitor}: starter/team/enterprise 信号" for competitor in task.competitors],
            pricing_notes="竞品通常会把协作和集成能力打包到更高阶套餐中。",
            evidence_ids=ids[1:3] or ids[:1],
        )
        persona = UserPersona(
            persona_name="竞品情报负责人",
            goals=["减少人工调研时间", "保持结论有来源支撑", "标准化报告格式"],
            pain_points=["来源分散", "证据质量不透明", "QA 耗时"],
            buying_triggers=["进入新市场", "季度规划", "销售 battlecard 更新"],
            evidence_ids=ids[2:4] or ids[:1],
        )
        swot = self._build_mock_swot(task.competitors, ids, selected_dimensions)
        diagnostics.update(
            {
                "selected_dimensions": selected_dimensions,
                "selected_dimension_count": len(selected_dimensions),
                "swot_item_count": self._count_swot_items(swot),
                "rework_context_applied": bool(input_data.rework_context),
            }
        )
        return AnalystOutput(
            product_profile=profile,
            feature_tree=feature_tree,
            capability_map=capability_map,
            pricing_model=pricing,
            user_persona=persona,
            swot=swot,
            diagnostics=diagnostics,
        )

    def _evidence_output(self, input_data: AnalystInput, fallback_reason: str | None) -> AnalystOutput:
        task = input_data.task
        selected_dimensions = self._selected_dimensions(input_data)
        usable_evidence = [item for item in input_data.evidence if item.relevance_level in {"high", "medium"}]
        weak_evidence = [item for item in input_data.evidence if item.relevance_level == "low"]
        evidence_by_competitor = self._group_by_competitor(usable_evidence, task.competitors)
        all_evidence_by_competitor = self._group_by_competitor(input_data.evidence, task.competitors)
        ids = self._ids(usable_evidence)
        capability_map = self._build_capability_map(task, usable_evidence, competitor_order=task.competitors)
        competitor_analysis: dict[str, dict] = {}
        aggregate_feature_hits: dict[str, list[Evidence]] = defaultdict(list)
        pricing_tiers: list[str] = []
        persona_goals: list[str] = []
        persona_pain_points: list[str] = []
        persona_triggers: list[str] = []
        aggregate_persona_labels: list[str] = []
        extracted_fields_by_competitor: dict[str, dict[str, int]] = {}
        evidence_used_by_competitor: dict[str, list[str]] = {}

        for competitor in task.competitors:
            competitor_evidence = sorted(evidence_by_competitor.get(competitor, []), key=lambda item: item.confidence, reverse=True)
            competitor_ids = self._ids(competitor_evidence)
            feature_hits = self._feature_hits(task, competitor_evidence)
            pricing_evidence = self._keyword_evidence(competitor_evidence, PRICING_KEYWORDS)
            persona_hits = self._persona_hits(competitor_evidence)
            insufficient = len(competitor_evidence) < 1 or (len(feature_hits) + len(pricing_evidence) + len(persona_hits)) < 1

            feature_names = list(feature_hits.keys()) or ["insufficient evidence"]
            pricing_labels = self._pricing_tiers(pricing_evidence)
            persona_labels = list(persona_hits.keys()) or ["证据不足，暂不判断用户画像"]
            aggregate_persona_labels.extend([item for item in persona_labels if item != "证据不足，暂不判断用户画像"])
            positioning = (
                "当前公开证据不足，暂不做强结论。Evidence is insufficient."
                if insufficient
                else f"{competitor} 的定位仅根据其自身公开证据保守推断：{self._compact(self._evidence_text(competitor_evidence[0]))}"
            )

            competitor_analysis[competitor] = {
                "positioning": positioning,
                "features": feature_names,
                "pricing": pricing_labels,
                "persona": persona_labels,
                "evidence_ids": competitor_ids,
                "insufficient_evidence": insufficient,
            }
            evidence_used_by_competitor[competitor] = competitor_ids
            extracted_fields_by_competitor[competitor] = {
                "profile": 0 if insufficient else 1,
                "feature": len(feature_hits),
                "pricing": len(pricing_evidence),
                "persona": len(persona_hits),
            }
            for feature, values in feature_hits.items():
                aggregate_feature_hits[feature].extend(values)
            pricing_tiers.append(f"{competitor}: {', '.join(pricing_labels)}")
            persona_goals.append(f"{competitor}: evaluate fit for {', '.join(persona_labels[:2])}")
            persona_pain_points.append(f"{competitor}: 需要更多官网、定价和用户反馈证据交叉验证")
            persona_triggers.append(f"{competitor}: 产品选型和竞品替换场景")
        missing_competitors = [competitor for competitor, records in evidence_by_competitor.items() if not records]
        insufficient = bool(missing_competitors) or any(item["insufficient_evidence"] for item in competitor_analysis.values())
        diagnostics = self._diagnostics(
            input_data,
            "evidence",
            ids,
            fallback_reason=fallback_reason or ("一个或多个竞品的公开证据不足。" if insufficient else None),
            insufficient=insufficient,
            feature_count=sum(item["feature"] for item in extracted_fields_by_competitor.values()),
            pricing_count=sum(item["pricing"] for item in extracted_fields_by_competitor.values()),
            persona_count=sum(item["persona"] for item in extracted_fields_by_competitor.values()),
            evidence_by_competitor=evidence_by_competitor,
            extracted_fields_by_competitor=extracted_fields_by_competitor,
            evidence_used_by_competitor=evidence_used_by_competitor,
        )
        diagnostics.update(
            {
                "insufficient_relevant_evidence": insufficient,
                "relevant_evidence_count": len(usable_evidence),
                "low_relevance_evidence_count": len(weak_evidence),
                "unrelated_evidence_count": sum(1 for item in input_data.evidence if item.relevance_level == "unrelated"),
                "all_evidence_count_by_competitor": {
                    competitor: len(records) for competitor, records in all_evidence_by_competitor.items()
                },
                "content_source_used": self._content_source_summary(usable_evidence),
                "capability_area_count": len(capability_map.aggregate_capabilities),
                "unmapped_capability_signal_count": len(capability_map.unmapped_signals),
                "selected_dimensions": selected_dimensions,
                "selected_dimension_count": len(selected_dimensions),
            }
        )
        profile = ProductProfile(
            product_name=task.product_name,
            positioning=""
            if input_data.force_invalid_extraction
            else (
                "当前公开证据不足，暂不做强结论。Evidence is insufficient."
                if insufficient
                else f"{task.product_name} 基于各竞品专属公开证据对比 {', '.join(task.competitors)}。"
            ),
            target_segments=[]
            if input_data.force_invalid_extraction
            else (["当前公开证据不足，暂不做强结论。Evidence is insufficient."] if insufficient else ["企业团队", "产品团队"]),
            strengths=self._strengths_from_features(dict(aggregate_feature_hits)) or ["当前公开证据不足，暂不做强结论。Evidence is insufficient."],
            weaknesses=["当前结论仍受各竞品公开证据覆盖度限制。"],
            evidence_ids=ids[: min(5, len(ids))],
            extensions={
                "domain": {
                    "region": task.region,
                    "industry": task.industry,
                    "domain_pack": domain_pack_reference(
                        resolve_domain_pack(task),
                        industry_label=task.industry,
                        metadata={"owner": "AnalystAgent", "mode": "evidence"},
                    ).model_dump(mode="json"),
                },
                "workflow": {
                    "analyst_mode": "evidence",
                    "selected_dimensions": selected_dimensions,
                    "insufficient_evidence": insufficient,
                    "supporting_evidence_ids": ids,
                    "competitor_analysis": competitor_analysis,
                    "capability_map": capability_map.model_dump(mode="json"),
                },
            },
            custom_dimensions={
                "region": task.region,
                "industry": task.industry,
                "analyst_mode": "evidence",
                "selected_dimensions": selected_dimensions,
                "insufficient_evidence": insufficient,
                "supporting_evidence_ids": ids,
                "competitor_analysis": competitor_analysis,
            },
        )
        feature_tree = FeatureTree(
            core_features=self._feature_tree_from_hits(dict(aggregate_feature_hits)) or self._legacy_feature_tree_projection(capability_map) or {"insufficient evidence": ["当前公开证据不足，暂不做强结论。Evidence is insufficient."]},
            differentiators=self._legacy_differentiators(capability_map) or self._strengths_from_features(dict(aggregate_feature_hits)) or ["当前公开证据不足，暂不做强结论。Evidence is insufficient."],
            evidence_ids=ids,
        )
        pricing = PricingModel(
            model="基于证据的竞品定价摘要" if not insufficient else "证据不足",
            tiers=pricing_tiers or ["当前公开证据不足"],
            pricing_notes=self._pricing_notes([item for records in evidence_by_competitor.values() for item in records]),
            evidence_ids=[
                item.evidence_id
                for records in evidence_by_competitor.values()
                for item in self._keyword_evidence(records, PRICING_KEYWORDS)
            ]
            or ids[:1],
        )
        persona = UserPersona(
            persona_name=aggregate_persona_labels[0] if aggregate_persona_labels else "竞品评估团队",
            goals=persona_goals or ["当前公开证据不足，暂不做强结论。Evidence is insufficient."],
            pain_points=persona_pain_points or ["需要更多竞品专属证据。"],
            buying_triggers=persona_triggers or ["当前公开证据不足，暂不做强结论。Evidence is insufficient."],
            evidence_ids=ids[: min(5, len(ids))],
        )
        swot = self._build_evidence_swot(
            task.competitors,
            selected_dimensions,
            evidence_by_competitor=evidence_by_competitor,
            competitor_analysis=competitor_analysis,
            aggregate_feature_hits=dict(aggregate_feature_hits),
        )
        swot, swot_refinement_summary = self._refine_swot_for_rework(
            swot,
            input_data=input_data,
            evidence_by_competitor=evidence_by_competitor,
        )
        diagnostics["swot_item_count"] = self._count_swot_items(swot)
        diagnostics["rework_context_applied"] = bool(input_data.rework_context)
        diagnostics["swot_refinement_summary"] = swot_refinement_summary
        return AnalystOutput(
            product_profile=profile,
            feature_tree=feature_tree,
            capability_map=capability_map,
            pricing_model=pricing,
            user_persona=persona,
            swot=swot,
            diagnostics=diagnostics,
        )

    def _diagnostics(
        self,
        input_data: AnalystInput,
        used_mode: str,
        ids: list[str],
        *,
        fallback_reason: str | None,
        insufficient: bool = False,
        feature_count: int = 0,
        pricing_count: int = 0,
        persona_count: int = 0,
        evidence_by_competitor: dict[str, list[Evidence]] | None = None,
        extracted_fields_by_competitor: dict[str, dict[str, int]] | None = None,
        evidence_used_by_competitor: dict[str, list[str]] | None = None,
    ) -> dict:
        grouped = evidence_by_competitor or self._group_by_competitor(input_data.evidence, input_data.task.competitors)
        evidence_count_by_competitor = {competitor: len(records) for competitor, records in grouped.items()}
        competitors_covered = [competitor for competitor, count in evidence_count_by_competitor.items() if count > 0]
        missing_competitors = [
            competitor for competitor in input_data.task.competitors if evidence_count_by_competitor.get(competitor, 0) == 0
        ]
        return {
            "analyst_mode_requested": input_data.analyst_mode,
            "analyst_mode_used": used_mode,
            "evidence_count": len(input_data.evidence),
            "evidence_used_count": len(ids),
            "extracted_profile_count": 0 if insufficient else len(competitors_covered),
            "extracted_feature_count": feature_count,
            "extracted_pricing_count": pricing_count,
            "extracted_persona_count": persona_count,
            "insufficient_evidence": insufficient,
            "fallback_used": bool(fallback_reason),
            "fallback_reason": fallback_reason,
            "analyst_fallback_reason": fallback_reason,
            "competitors_requested": input_data.task.competitors,
            "competitors_covered": competitors_covered,
            "missing_competitors": missing_competitors,
            "evidence_count_by_competitor": evidence_count_by_competitor,
            "evidence_used_by_competitor": evidence_used_by_competitor
            or {competitor: [item.evidence_id for item in records] for competitor, records in grouped.items()},
            "extracted_fields_by_competitor": extracted_fields_by_competitor or {},
        }

    @staticmethod
    def _group_by_competitor(evidence: list[Evidence], competitors: list[str]) -> dict[str, list[Evidence]]:
        grouped: dict[str, list[Evidence]] = {competitor: [] for competitor in competitors}
        if evidence and not any(item.competitor for item in evidence) and all(is_relevant_evidence(item) for item in evidence):
            for competitor in competitors:
                grouped[competitor] = list(evidence)
            return grouped
        for item in evidence:
            if item.competitor in grouped:
                grouped[item.competitor].append(item)
            elif item.competitor is None and len(competitors) == 1:
                grouped[competitors[0]].append(item)
        return grouped

    @staticmethod
    def _ids(evidence: list[Evidence]) -> list[str]:
        return [item.evidence_id for item in evidence] or ["insufficient_evidence"]

    @staticmethod
    def _dedupe(items) -> list[str]:
        seen: set[str] = set()
        output: list[str] = []
        for item in items:
            value = str(item).strip()
            if not value or value in seen:
                continue
            seen.add(value)
            output.append(value)
        return output

    @staticmethod
    def _compact(text: str) -> str:
        return text[:120].replace("\n", " ")

    @staticmethod
    def _evidence_text(item: Evidence) -> str:
        return item.content_excerpt or item.snippet

    @staticmethod
    def _content_source_summary(evidence: list[Evidence]) -> dict[str, int]:
        return {
            "page_excerpt": sum(1 for item in evidence if item.content_excerpt),
            "snippet": sum(1 for item in evidence if not item.content_excerpt),
        }

    def _build_capability_map(self, task, evidence: list[Evidence], *, competitor_order: list[str]) -> CapabilityMap:
        resolved_domain_pack = resolve_domain_pack(task)
        domain_pack = domain_pack_reference(
            resolved_domain_pack,
            industry_label=task.industry,
            metadata={"owner": "AnalystAgent", "source": "capability_extraction"},
        )
        feature_taxonomy = {
            **{feature: tuple(keywords) for feature, keywords in FEATURE_KEYWORDS.items()},
            **resolved_domain_pack.feature_taxonomy,
        }
        competitor_capabilities: dict[str, list[CapabilityBucket]] = {}
        aggregate_buckets: list[CapabilityBucket] = []
        unmapped_signals: list[CapabilitySignal] = []

        for competitor in competitor_order:
            competitor_evidence = [
                item for item in evidence
                if item.competitor == competitor or (item.competitor is None and len(competitor_order) == 1)
            ]
            buckets, competitor_unmapped = self._capability_buckets_for_competitor(
                competitor,
                competitor_evidence,
                feature_taxonomy=feature_taxonomy,
            )
            competitor_capabilities[competitor] = buckets
            unmapped_signals.extend(competitor_unmapped)

        aggregate_by_area: dict[str, list[CapabilityBucket]] = defaultdict(list)
        for buckets in competitor_capabilities.values():
            for bucket in buckets:
                aggregate_by_area[bucket.capability_area].append(bucket)

        for area, buckets in aggregate_by_area.items():
            aggregate_buckets.append(
                CapabilityBucket(
                    competitor=None,
                    capability_area=area,
                    normalized_features=self._dedupe(
                        feature
                        for bucket in buckets
                        for feature in bucket.normalized_features
                    ),
                    evidence_ids=self._dedupe(
                        evidence_id
                        for bucket in buckets
                        for evidence_id in bucket.evidence_ids
                    ),
                    confidence=round(sum(bucket.confidence for bucket in buckets) / max(len(buckets), 1), 2),
                    insufficient_evidence=all(bucket.insufficient_evidence for bucket in buckets),
                    summary=f"该能力信号出现在 {len(buckets)} 个竞品能力桶中。",
                    signals=[
                        signal
                        for bucket in buckets
                        for signal in bucket.signals
                    ][:8],
                )
            )

        return CapabilityMap(
            domain_pack=domain_pack,
            competitor_capabilities=competitor_capabilities,
            aggregate_capabilities=sorted(aggregate_buckets, key=lambda item: item.capability_area),
            unmapped_signals=unmapped_signals[:12],
            evidence_ids=self._ids(evidence),
        )

    def _capability_buckets_for_competitor(
        self,
        competitor: str,
        evidence: list[Evidence],
        *,
        feature_taxonomy: dict[str, tuple[str, ...]],
    ) -> tuple[list[CapabilityBucket], list[CapabilitySignal]]:
        signals_by_area: dict[str, list[CapabilitySignal]] = defaultdict(list)
        unmapped: list[CapabilitySignal] = []

        for item in evidence:
            text = self._evidence_text(item).lower()
            matched_area = False
            for area, keywords in feature_taxonomy.items():
                matched_keywords = [keyword for keyword in keywords if keyword.lower() in text]
                if not matched_keywords:
                    continue
                matched_area = True
                signals_by_area[area].append(
                    CapabilitySignal(
                        competitor=competitor,
                        capability_area=area,
                        normalized_feature=area,
                        evidence_ids=[item.evidence_id],
                        confidence=round(min(0.95, max(0.45, item.confidence)), 2),
                        insufficient_evidence=False,
                        matched_keywords=matched_keywords[:4],
                        support_summary=self._compact(self._evidence_text(item)),
                    )
                )
            if not matched_area:
                fallback_area = self._generic_capability_area(text)
                if fallback_area is None:
                    unmapped.append(
                        CapabilitySignal(
                            competitor=competitor,
                            capability_area="other",
                            normalized_feature="unmapped_signal",
                            evidence_ids=[item.evidence_id],
                            confidence=round(min(0.6, max(0.3, item.confidence)), 2),
                            insufficient_evidence=False,
                            matched_keywords=[],
                            support_summary=self._compact(self._evidence_text(item)),
                        )
                    )
                    continue
                signals_by_area[fallback_area].append(
                    CapabilitySignal(
                        competitor=competitor,
                        capability_area=fallback_area,
                        normalized_feature=fallback_area,
                        evidence_ids=[item.evidence_id],
                        confidence=round(min(0.7, max(0.35, item.confidence)), 2),
                        insufficient_evidence=False,
                        matched_keywords=[],
                        support_summary=self._compact(self._evidence_text(item)),
                    )
                )

        if not evidence and not signals_by_area:
            return (
                [
                    CapabilityBucket(
                        competitor=competitor,
                        capability_area="evidence_gap",
                        normalized_features=[],
                        evidence_ids=["insufficient_evidence"],
                        confidence=0.25,
                        insufficient_evidence=True,
                        summary="当前相关公开证据不足，暂不抽取能力结论。",
                        signals=[],
                    )
                ],
                [],
            )

        if unmapped:
            signals_by_area["other"].extend(unmapped)

        buckets: list[CapabilityBucket] = []
        for area, signals in signals_by_area.items():
            evidence_ids = self._dedupe(evidence_id for signal in signals for evidence_id in signal.evidence_ids)
            buckets.append(
                CapabilityBucket(
                    competitor=competitor,
                    capability_area=area,
                    normalized_features=self._dedupe(signal.normalized_feature for signal in signals),
                    evidence_ids=evidence_ids,
                    confidence=round(sum(signal.confidence for signal in signals) / max(len(signals), 1), 2),
                    insufficient_evidence=False,
                    summary=f"{competitor} 在 {area} 方向存在由证据支撑的信号。",
                    signals=signals[:8],
                )
            )
        return sorted(buckets, key=lambda item: item.capability_area), unmapped

    def _legacy_feature_tree_projection(self, capability_map: CapabilityMap) -> dict[str, list[str]]:
        projected: dict[str, list[str]] = {}
        for area_bucket in capability_map.aggregate_capabilities:
            labels = []
            for bucket in capability_map.competitor_capabilities.values():
                for competitor_bucket in bucket:
                    if competitor_bucket.capability_area != area_bucket.capability_area:
                        continue
                    labels.append(
                        f"{competitor_bucket.competitor}: {', '.join(competitor_bucket.normalized_features[:3]) or competitor_bucket.capability_area}"
                    )
            if labels:
                projected[area_bucket.capability_area] = labels[:4]
        return projected

    @staticmethod
    def _feature_tree_from_hits(feature_hits: dict[str, list[Evidence]]) -> dict[str, list[str]]:
        return {
            feature: [f"{feature} 相关证据：{', '.join(item.evidence_id for item in records[:3])}"]
            for feature, records in feature_hits.items()
            if records
        }

    def _legacy_differentiators(self, capability_map: CapabilityMap) -> list[str]:
        differentiators = []
        for bucket in capability_map.aggregate_capabilities[:4]:
            if bucket.insufficient_evidence:
                continue
            differentiators.append(
                f"由证据支撑的能力方向：{bucket.capability_area}"
            )
        return differentiators

    def _generic_capability_area(self, text: str) -> str | None:
        for area, keywords in FEATURE_KEYWORDS.items():
            if any(keyword.lower() in text for keyword in keywords):
                return area.lower()
        return None

    def _feature_hits(self, task, evidence: list[Evidence]) -> dict[str, list[Evidence]]:
        hits: dict[str, list[Evidence]] = defaultdict(list)
        resolved_domain_pack = resolve_domain_pack(task)
        feature_keywords = {
            **FEATURE_KEYWORDS,
            **{feature: list(keywords) for feature, keywords in resolved_domain_pack.feature_taxonomy.items()},
        }
        for item in evidence:
            text = self._evidence_text(item).lower()
            for feature, keywords in feature_keywords.items():
                if any(keyword.lower() in text for keyword in keywords):
                    hits[feature].append(item)
        return dict(hits)

    def _keyword_evidence(self, evidence: list[Evidence], keywords: list[str]) -> list[Evidence]:
        matched = []
        for item in evidence:
            text = f"{self._evidence_text(item)} {item.url or ''}".lower()
            if any(keyword.lower() in text for keyword in keywords):
                matched.append(item)
        return matched

    def _persona_hits(self, evidence: list[Evidence]) -> dict[str, list[Evidence]]:
        hits: dict[str, list[Evidence]] = defaultdict(list)
        for item in evidence:
            text = self._evidence_text(item).lower()
            for persona, keywords in PERSONA_KEYWORDS.items():
                if any(keyword.lower() in text for keyword in keywords):
                    hits[persona].append(item)
        return dict(hits)

    @staticmethod
    def _feature_labels(feature: str, evidence: list[Evidence]) -> list[str]:
        ids = ", ".join(item.evidence_id for item in evidence[:3])
        return [f"{feature} related evidence: {ids}"]

    @staticmethod
    def _strengths_from_features(feature_hits: dict[str, list[Evidence]]) -> list[str]:
        return [f"公开来源提到 {feature} 能力" for feature in list(feature_hits.keys())[:4]]

    @staticmethod
    def _pricing_notes(evidence: list[Evidence]) -> str:
        if not evidence:
            return "当前公开证据不足，暂不做强定价结论。Evidence is insufficient for pricing."
        return "公开来源包含定价或套餐信号 pricing signals；正式结论仍应以官网页面做最终校验。"

    def _pricing_tiers(self, evidence: list[Evidence]) -> list[str]:
        if not evidence:
            return ["当前公开证据不足"]
        return [
            "免费或试用信号"
            if any(word in self._evidence_text(item).lower() for word in ["free", "trial", "免费", "试用"])
            else "付费或企业版信号"
            for item in evidence[:3]
        ]

    @staticmethod
    def _selected_dimensions(input_data: AnalystInput) -> list[str]:
        return [str(item).strip().lower() for item in input_data.selected_dimensions if str(item).strip()]

    @staticmethod
    def _count_swot_items(swot: SwotAnalysis) -> int:
        return len(swot.strengths) + len(swot.weaknesses) + len(swot.opportunities) + len(swot.threats)

    def _build_mock_swot(self, competitors: list[str], ids: list[str], selected_dimensions: list[str]) -> SwotAnalysis:
        dimensions = ", ".join(selected_dimensions[:3]) if selected_dimensions else "feature, pricing, persona"
        competitor_label = competitors[0] if competitors else None
        return SwotAnalysis(
            strengths=[
                SwotItem(
                    summary=f"Mock 分析突出 {dimensions} 维度下的证据可追溯性。",
                    competitor=competitor_label,
                    evidence_ids=ids[:1],
                    confidence=0.55,
                )
            ],
            weaknesses=[
                SwotItem(
                    summary="Mock 抽取仍依赖简化规则，应使用更丰富证据进一步验证。",
                    competitor=competitor_label,
                    evidence_ids=ids[:1],
                    confidence=0.45,
                )
            ],
            opportunities=[
                SwotItem(
                    summary=f"Planner 选择的维度提示可围绕 {dimensions} 做更深入对比。",
                    competitor=competitor_label,
                    evidence_ids=ids[:1],
                    confidence=0.5,
                )
            ],
            threats=[
                SwotItem(
                    summary="公开证据缺口仍会限制竞品差异化判断的置信度。",
                    competitor=competitor_label,
                    evidence_ids=ids[:1],
                    confidence=0.45,
                )
            ],
        )

    def _build_evidence_swot(
        self,
        competitors: list[str],
        selected_dimensions: list[str],
        *,
        evidence_by_competitor: dict[str, list[Evidence]],
        competitor_analysis: dict[str, dict],
        aggregate_feature_hits: dict[str, list[Evidence]],
    ) -> SwotAnalysis:
        strengths: list[SwotItem] = []
        weaknesses: list[SwotItem] = []
        opportunities: list[SwotItem] = []
        threats: list[SwotItem] = []
        dimension_focus = set(selected_dimensions)

        for feature, feature_evidence in sorted(aggregate_feature_hits.items(), key=lambda item: len(item[1]), reverse=True)[:3]:
            feature_competitors = {item.competitor for item in feature_evidence if item.competitor}
            competitor = next(iter(feature_competitors)) if len(feature_competitors) == 1 else None
            strengths.append(
                SwotItem(
                    summary=f"公开来源多次提到 {feature} 能力，可作为可见的竞争优势信号。",
                    competitor=competitor,
                    evidence_ids=[item.evidence_id for item in feature_evidence[:3]] or ["insufficient_evidence"],
                    confidence=min(0.9, 0.55 + 0.08 * len(feature_evidence)),
                )
            )

        for competitor in competitors:
            records = evidence_by_competitor.get(competitor, [])
            analysis = competitor_analysis.get(competitor, {})
            record_ids = [item.evidence_id for item in records[:3]] or ["insufficient_evidence"]
            if not records or analysis.get("insufficient_evidence"):
                weaknesses.append(
                    SwotItem(
                        summary="相关公开证据仍然不足，暂不对该竞品做强结论。",
                        competitor=competitor,
                        evidence_ids=record_ids,
                        confidence=0.35,
                    )
                )
                threats.append(
                    SwotItem(
                        summary="证据覆盖较薄会增加过度依赖少量公开信号的风险。",
                        competitor=competitor,
                        evidence_ids=record_ids,
                        confidence=0.35,
                    )
                )
                continue

            pricing_records = self._keyword_evidence(records, PRICING_KEYWORDS)
            feature_labels = analysis.get("features") or []
            if pricing_records and {"pricing", "positioning"} & dimension_focus:
                opportunities.append(
                    SwotItem(
                        summary="定价与套餐信号已有一定可见度，可在下一步支持更细的定位对比。",
                        competitor=competitor,
                        evidence_ids=[item.evidence_id for item in pricing_records[:3]],
                        confidence=0.65,
                    )
                )
            if feature_labels and feature_labels[0] != "insufficient evidence":
                opportunities.append(
                    SwotItem(
                        summary=f"围绕 {', '.join(feature_labels[:2])} 的公开信号可支持更有针对性的功能差异化分析。",
                        competitor=competitor,
                        evidence_ids=record_ids,
                        confidence=0.6,
                    )
                )
            weaknesses.append(
                SwotItem(
                    summary=(
                        "在采集到更明确的痛点证据前，UX 和用户反馈相关结论应保持保守。"
                        if {"ux", "feedback", "prioritization"} & dimension_focus
                        else "当前公开证据仍留下部分工作流和买方适配不确定性。"
                    ),
                    competitor=competitor,
                    evidence_ids=record_ids,
                    confidence=0.5,
                )
            )
            threats.append(
                SwotItem(
                    summary="由于现有证据可能无法覆盖完整产品能力，跨竞品结论应保持谨慎。",
                    competitor=competitor,
                    evidence_ids=record_ids,
                    confidence=0.5,
                )
            )

        fallback_competitor = competitors[0] if competitors else None
        if not strengths:
            strengths.append(
                SwotItem(
                    summary="已有部分相关证据，但仍不足以识别稳定优势。",
                    competitor=fallback_competitor,
                    evidence_ids=["insufficient_evidence"],
                    confidence=0.35,
                )
            )
        if not opportunities:
            opportunities.append(
                SwotItem(
                    summary="补充官方文档和定价页会提升机会点判断质量。",
                    competitor=fallback_competitor,
                    evidence_ids=["insufficient_evidence"],
                    confidence=0.35,
                )
            )

        return SwotAnalysis(
            strengths=strengths[:4],
            weaknesses=weaknesses[:4],
            opportunities=opportunities[:4],
            threats=threats[:4],
        )

    def _refine_swot_for_rework(
        self,
        swot: SwotAnalysis,
        *,
        input_data: AnalystInput,
        evidence_by_competitor: dict[str, list[Evidence]],
    ) -> tuple[SwotAnalysis, dict]:
        if input_data.rework_context is None:
            return swot, {"applied": False, "issues_seen": 0, "adjustments": []}

        metadata = input_data.rework_context.metadata or {}
        issues = metadata.get("swot_issues", []) if isinstance(metadata.get("swot_issues", []), list) else []
        if not issues:
            return swot, {"applied": False, "issues_seen": 0, "adjustments": []}

        refined = swot.model_copy(deep=True)
        evidence_by_id = {item.evidence_id: item for item in input_data.evidence}
        adjustments: list[str] = []

        for issue in issues:
            quadrant = issue.get("quadrant")
            competitor = issue.get("competitor")
            error_type = issue.get("error_type")
            if quadrant not in {"strengths", "weaknesses", "opportunities", "threats"}:
                continue
            items = getattr(refined, quadrant)
            for index, item in enumerate(items):
                if competitor and item.competitor not in {competitor, None}:
                    continue
                supporting_records = [
                    evidence_by_id[evidence_id]
                    for evidence_id in item.evidence_ids
                    if evidence_id in evidence_by_id and evidence_by_id[evidence_id].relevance_level in {"high", "medium"}
                ]
                competitor_records = evidence_by_competitor.get(item.competitor or competitor or "", [])
                if error_type == "swot_missing_support":
                    fallback_ids = [record.evidence_id for record in competitor_records[:2]] or ["insufficient_evidence"]
                    items[index] = item.model_copy(
                        update={
                            "summary": "复核后证据仍然不足，暂不保留强 SWOT 结论。",
                            "evidence_ids": fallback_ids,
                            "confidence": 0.35,
                        }
                    )
                    adjustments.append(f"{quadrant}:{item.competitor or 'overall'} softened due to missing support")
                elif error_type == "swot_over_inference":
                    items[index] = item.model_copy(
                        update={
                            "summary": f"保守改写：{item.summary}",
                            "confidence": min(item.confidence, 0.45),
                        }
                    )
                    adjustments.append(f"{quadrant}:{item.competitor or 'overall'} confidence lowered")
                elif error_type == "swot_competitor_mismatch":
                    same_competitor_ids = [record.evidence_id for record in competitor_records[:3]] or ["insufficient_evidence"]
                    items[index] = item.model_copy(
                        update={
                            "evidence_ids": same_competitor_ids,
                            "confidence": 0.4 if same_competitor_ids == ["insufficient_evidence"] else min(item.confidence, 0.5),
                        }
                    )
                    adjustments.append(f"{quadrant}:{item.competitor or 'overall'} evidence rebound to same competitor")
                elif error_type == "swot_dimension_gap" and supporting_records:
                    items[index] = item.model_copy(update={"confidence": min(item.confidence, 0.55)})
                    adjustments.append(f"{quadrant}:{item.competitor or 'overall'} kept conservative for planner dimension gap")

        return refined, {
            "applied": bool(adjustments),
            "issues_seen": len(issues),
            "adjustments": adjustments,
            "rework_error_type": input_data.rework_context.error_type,
        }
