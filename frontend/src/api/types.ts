export type Task = {
  task_id: string;
  product_name: string;
  competitors: string[];
  region: string;
  industry: string;
  collection_strategy_mode: "simple" | "balanced" | "expert";
  status: string;
  rework_count: number;
  created_at: string;
  updated_at: string;
};

export type TaskRun = {
  run_id: string;
  task_id: string;
  workflow_engine: string;
  collector_mode: string;
  analyst_mode: string;
  writer_mode: string;
  content_mode?: string | null;
  demo_mode: string;
  auto_rework: boolean;
  status: string;
  final_status?: string | null;
  started_at: string;
  finished_at?: string | null;
  elapsed_time_ms?: number | null;
  error_message?: string | null;
  created_at: string;
};

export type Evidence = {
  evidence_id: string;
  run_id?: string | null;
  competitor?: string;
  source_type: string;
  url?: string;
  local_ref?: string;
  snippet: string;
  confidence: number;
  source_domain?: string;
  source_quality?: "official" | "documentation" | "media" | "review" | "unknown" | "low_quality";
  relevance_score?: number;
  relevance_level?: "high" | "medium" | "low" | "unrelated";
  relevance_reason?: string;
  entity_match_signals?: Record<string, unknown>;
  content_mode?: "snippet" | "page";
  page_fetch_success?: boolean;
  page_title?: string | null;
  content_excerpt?: string | null;
  content_chars?: number | null;
  fetch_status_code?: number | null;
  page_fetch_error?: string | null;
  fetched_at?: string | null;
  collected_at: string;
};

export type Claim = {
  claim_id: string;
  competitor?: string;
  text: string;
  category: string;
  evidence_ids: string[];
  confidence: number;
};

export type DimensionResult = {
  dimension_result_id: string;
  dimension_id: string;
  competitor?: string | null;
  summary: string;
  findings: string[];
  evidence_ids: string[];
  confidence: number;
  insufficient_evidence: boolean;
  metadata?: Record<string, unknown>;
};

export type SwotItem = {
  summary: string;
  competitor?: string | null;
  evidence_ids: string[];
  confidence: number;
};

export type SwotAnalysis = {
  strengths: SwotItem[];
  weaknesses: SwotItem[];
  opportunities: SwotItem[];
  threats: SwotItem[];
};

export type Report = {
  report_id: string;
  task_id: string;
  run_id?: string | null;
  markdown: string;
  json_report: {
    knowledge?: Record<string, unknown>;
    swot?: SwotAnalysis;
    planner?: {
      intent_classification?: string | null;
      selected_dimensions?: string[];
      writer_guidance?: string[];
    };
    dimension_results?: DimensionResult[];
    claims?: Claim[];
    writer_diagnostics?: WriterDiagnostics;
    report_agent?: Record<string, unknown>;
  };
  dimension_results?: DimensionResult[];
  claims?: Claim[];
  qa_result?: QaResult;
};

export type WriterDiagnostics = {
  writer_mode_requested?: string;
  writer_mode_used?: string;
  llm_enabled?: boolean;
  llm_provider?: string;
  llm_model?: string;
  llm_base_url_configured?: boolean;
  has_api_key?: boolean;
  llm_call_attempted?: boolean;
  llm_call_success?: boolean;
  llm_elapsed_time_ms?: number;
  llm_error_type?: string;
  llm_error_message?: string;
  llm_response_preview?: string;
  fallback_used?: boolean;
  llm_fallback_reason?: string;
  selected_dimensions?: string[];
  writer_guidance_count?: number;
  intent_classification?: string | null;
};

export type LlmStatus = {
  llm_provider: string;
  llm_model: string;
  base_url_configured: boolean;
  api_key_configured: boolean;
  llm_enabled: boolean;
  last_check_status: "not_checked" | "success" | "failed";
  last_error?: string | null;
  suggested_action: string;
};

export type CollectorStatus = {
  search_provider: string;
  api_key_configured: boolean;
  base_url_configured: boolean;
  timeout: number;
  max_results: number;
  enabled: boolean;
};

export type SearchTestResult = {
  success: boolean;
  provider: string;
  query: string;
  result_count: number;
  results_preview: Array<{ title: string; url: string; snippet: string }>;
  error_type?: string | null;
  error_message?: string | null;
};

export type KnowledgeHit = {
  chunk_id: string;
  text_preview: string;
  source_url?: string | null;
  source_domain?: string | null;
  source_quality?: string | null;
  score: number;
  evidence_id?: string | null;
  updated_at?: string | null;
};

export type KnowledgeRetrievalStrategy = {
  retriever?: string;
  vector_store?: string;
  embedding_provider?: string;
  similarity?: string;
  top_k?: number;
  current_run_evidence_priority?: boolean;
};

export type CollectionPlan = {
  collector_search_plan: Record<
    string,
    Record<
      string,
      {
        dimension_id?: string;
        label?: string;
        queries?: string[];
        research_goals?: string[];
        source?: string;
      }
    >
  >;
};

export type CollectorConfig = {
  default?: {
    max_results_per_query?: number | null;
    max_evidence_per_dimension?: number | null;
    min_valid_evidence_required?: number | null;
    include_domains?: string[];
    exclude_domains?: string[];
  };
  overrides?: Array<{
    competitor: string;
    dimension_id: string;
    max_results_per_query?: number | null;
    max_evidence_per_dimension?: number | null;
    min_valid_evidence_required?: number | null;
    include_domains?: string[];
    exclude_domains?: string[];
  }>;
};

export type WorkflowSummary = {
  run_id?: string;
  task_id?: string;
  workflow_engine_requested?: string;
  workflow_engine_used?: string;
  debug_stage?: "planner_only" | "main_flow" | "collector_only" | null;
  planner_summary?: {
    intent_classification?: string;
    product_name?: string;
    industry?: string;
    region?: string;
    competitors?: string[];
    product_type?: string;
    task_goal?: string;
  };
  intent_summary?: string | null;
  intent_classification?: string | null;
  ambiguity_level?: string | null;
  scope_type?: string | null;
  scope_size?: string | null;
  survey_needed?: boolean;
  survey_recommended?: boolean;
  node_sequence?: string[];
  conditional_routes_taken?: Array<{ from_node?: string; to_node?: string; reason?: string; details?: string; rework_count?: number; final_status?: string }>;
  rework_count?: number;
  final_status?: string;
  elapsed_time_ms?: number;
  error_message?: string | null;
  run_isolation_strategy?: string;
  run_cleanup_summary?: Record<string, unknown>;
  evidence_gate_output?: {
    evidence_gate_passed?: boolean;
    missing_relevant_evidence_competitors?: string[];
    relevant_evidence_count_by_competitor?: Record<string, number>;
    unrelated_evidence_count_by_competitor?: Record<string, number>;
    evidence_diagnostics_by_competitor?: Record<string, EvidenceGateCompetitorDiagnostic>;
    failure_explanation?: string;
    suggested_route?: string | null;
    suggested_action?: string;
  };
  selected_dimensions?: string[];
  analysis_dimension_plan?: {
    selected_dimensions?: string[];
    dimension_plans?: Array<{
      dimension_id?: string;
      label?: string;
      description?: string;
      keywords?: string[];
      required?: boolean;
      priority?: number;
      query_templates?: string[];
      research_goals?: string[];
      source?: string;
      metadata?: Record<string, unknown>;
    }>;
    research_goals?: string[];
    query_hints?: Record<string, string[]>;
    metadata?: {
      dimension_policy?: string;
      base_dimensions?: string[];
      dynamic_dimensions?: string[];
      dynamic_dimension_reasons?: string[];
      dimension_query_hints?: Record<string, Record<string, string>>;
      collector_search_plan?: Record<string, Record<string, unknown>>;
    };
  } | null;
  collection_plan?: CollectionPlan;
  manual_collection_plan_override_used?: boolean;
  collection_strategy_mode?: "simple" | "balanced" | "expert";
  collector_config_source?: string | null;
  manual_evidence_selection_used?: boolean;
  manual_selected_evidence_ids?: string[];
  manual_selected_evidence_count?: number;
  source_run_id?: string;
  planner_collection_plan_original?: CollectionPlan;
  collector_config?: CollectorConfig | null;
  collector_output?: {
    evidence?: Evidence[];
    diagnostics?: CollectorDiagnostics;
  };
  collector_diagnostics?: CollectorDiagnostics;
  qa_output?: {
    qa_result?: QaResult;
    diagnostics?: Record<string, unknown>;
  };
  recommended_next_constraints?: string[];
  clarification_targets?: string[];
  candidate_competitors?: Array<{
    name?: string;
    confidence?: number;
    rationale?: string;
    source?: string;
  }>;
  planning_stages?: Array<{
    stage_id?: string;
    label?: string;
    description?: string;
    status?: string;
  }>;
  downstream_guidance?: {
    collector?: string[];
    analyst?: string[];
    writer?: string[];
    qa?: string[];
    survey?: string[];
  } | null;
  diagnostics?: Record<string, unknown>;
  planner_notes?: string[];
  planner_output?: Record<string, unknown>;
  dag?: Dag;
  swot_analysis?: SwotAnalysis | null;
  page_fetch_output?: {
    page_fetch_provider?: string;
    page_fetch_attempted?: boolean;
    page_fetch_attempt_count?: number;
    page_fetch_success_count?: number;
    page_fetch_failed_count?: number;
    page_fetch_skipped_count?: number;
    page_fetch_fallback_count?: number;
    page_fetch_error_summary?: Record<string, number>;
    avg_content_chars?: number;
    max_content_chars?: number;
    fetched_evidence_ids?: string[];
    skipped_evidence_ids?: string[];
    run_id?: string | null;
  };
  evidence_content_fetch_output?: {
    content_fetch_provider?: string;
    content_fetch_available?: boolean;
    content_fetch_attempted?: boolean;
    content_fetch_attempt_count?: number;
    content_fetch_success_count?: number;
    content_fetch_failed_count?: number;
    content_fetch_skipped_count?: number;
    content_fetch_fallback_count?: number;
    content_fetch_error_summary?: Record<string, number>;
    content_fetch_elapsed_time_ms?: number;
    avg_content_chars?: number;
    max_content_chars?: number;
    fetched_evidence_ids?: string[];
    failed_evidence_ids?: string[];
    skipped_evidence_ids?: string[];
    run_id?: string | null;
  };
  evidence_analyst_output?: {
    question_results?: EvidenceDimensionAnswerResult[];
    diagnostics?: {
      evidence_analyst_mode?: string;
      evidence_analyst_enabled?: boolean;
      total_evidence?: number;
      target_group_count?: number;
      completed_group_count?: number;
      failed_group_count?: number;
      failed_evidence?: number;
      question_answer_count?: number;
      answered_question_count?: number;
      warning_count?: number;
      skip_reason?: string;
      llm_call_success_count?: number;
      llm_call_failed_count?: number;
      llm_elapsed_time_ms?: number;
      schema_validation_errors?: string[];
    } & Record<string, unknown>;
  };
  analyst_qa_output?: {
    qa_result?: QaResult;
    diagnostics?: Record<string, unknown>;
  };
  analyst_qa_result?: QaResult;
  analyst_incremental_attempts?: Array<Record<string, unknown>>;
  report_agent_output?: {
    markdown_report?: string;
    executive_summary?: string[];
    dimension_sections?: Array<Record<string, unknown>>;
    evidence_gaps?: Array<Record<string, unknown>>;
    diagnostics?: Record<string, unknown>;
  };
  markdown_report?: string | null;
  knowledge_hits?: KnowledgeHit[];
  retrieved_knowledge_chunk_count?: number;
  knowledge_retrieval_strategy?: KnowledgeRetrievalStrategy;
};

export type EvidenceQuestionAnswer = {
  question_id: string;
  question: string;
  answer: string;
  evidence_ids: string[];
  answer_status: "answered" | "partial" | "not_found";
  suggestions?: string[];
};

export type EvidenceDimensionAnswerResult = {
  competitor?: string | null;
  dimension_id?: string | null;
  dimension_goal: string;
  research_questions: string[];
  question_answers: EvidenceQuestionAnswer[];
  dimension_summary: string;
  warnings: string[];
};

export type PlannerRunResult = {
  run?: TaskRun;
  run_id?: string;
  plan?: Record<string, unknown>;
  planner_output?: Record<string, unknown>;
  planner_summary?: WorkflowSummary["planner_summary"];
  intent_summary?: string | null;
  intent_classification?: string;
  selected_dimensions?: string[];
  analysis_dimension_plan?: WorkflowSummary["analysis_dimension_plan"];
  collection_plan?: WorkflowSummary["collection_plan"];
  collector_output?: {
    evidence: Evidence[];
    diagnostics: CollectorDiagnostics;
  };
  qa_output?: {
    qa_result: QaResult;
    diagnostics: Record<string, unknown>;
  };
  evidence?: Evidence[];
  downstream_guidance?: WorkflowSummary["downstream_guidance"];
  diagnostics?: Record<string, unknown>;
  planner_notes?: string[];
  dag?: Dag;
  report?: Report | null;
  qa_result?: QaResult | null;
  workflow_summary?: WorkflowSummary;
};

export type RunTaskOverrides = {
  collection_plan_override?: CollectionPlan;
  collector_config?: CollectorConfig;
  skip_initial_planner?: boolean;
  manual_evidence_selection_enabled?: boolean;
  selected_evidence_ids?: string[];
  source_run_id?: string;
};

export type PlannerAttempt = {
  run_id: string;
  attempt_no: number;
  status: "generated" | "fallback" | "failed";
  planner_output: Record<string, unknown>;
  diagnostics: Record<string, unknown>;
  rework_context?: Record<string, unknown> | null;
  raw_llm_response?: string | null;
  created_at: string;
};

export type WorkflowProgress = {
  run_id: string;
  current_agent?: string;
  current_stage?: string;
  message?: string;
  current?: number;
  total?: number;
  unit?: string;
  detail?: string | null;
  status?: string;
  metadata?: Record<string, unknown>;
  node_statuses?: Record<string, string>;
  updated_at?: string;
};

export type CollectorDiagnostics = {
  collector_mode_requested?: string;
  collector_mode_used?: string;
  web_search_attempted?: boolean;
  web_search_success?: boolean;
  query_count?: number;
  query_count_by_competitor?: Record<string, number>;
  query_count_by_dimension?: Record<string, number>;
  evidence_count?: number;
  evidence_count_by_dimension?: Record<string, number>;
  failed_queries?: string[];
  collection_plan_used?: boolean;
  collector_search_plan_source?: string;
  collector_search_plan_missing?: boolean;
  fallback_used?: boolean;
  fallback_reason?: string;
  elapsed_time_ms?: number;
  planner_query_hints_used?: boolean;
  collector_search_plan_used?: boolean;
  targeted_recollection_used?: boolean;
  planner_hint_query_count_by_competitor?: Record<string, number>;
  planned_query_count_by_competitor?: Record<string, number>;
  targeted_query_count_by_competitor?: Record<string, number>;
  effective_query_count_by_competitor?: Record<string, number>;
  effective_queries_preview_by_competitor?: Record<string, string[]>;
  targeted_queries_preview_by_competitor?: Record<string, string[]>;
  skipped_queries_by_competitor?: Record<string, Array<{ query?: string; dimension_id?: string | null; reason?: string | null }>>;
  query_policy?: string[];
  entity_aliases_used?: boolean;
  competitor_aliases_by_competitor?: Record<string, string[]>;
  alias_query_count_by_competitor?: Record<string, number>;
  alias_queries_preview_by_competitor?: Record<string, string[]>;
  query_dimensions_by_competitor?: Record<string, Array<{ query?: string; dimension_id?: string | null; intent?: string | null; source?: string | null }>>;
  evidence_count_by_competitor?: Record<string, number>;
  relevant_evidence_count_by_competitor?: Record<string, number>;
  unrelated_evidence_count_by_competitor?: Record<string, number>;
  missing_relevant_evidence_competitors?: string[];
};

export type QaResult = {
  task_id: string;
  run_id?: string | null;
  qa_stage?: "full" | "evidence" | "analyst";
  status: "passed" | "warning" | "failed" | "manual_review";
  hard_errors: string[];
  soft_suggestions: string[];
  rework_instructions: Array<{
    target_agent: string;
    error_type: string;
    reason: string;
    suggested_action: string;
    claim_id?: string;
    failed_claim?: string;
    failed_schema?: string;
    metadata?: Record<string, unknown>;
  }>;
  rework_history: Array<{
    round: number;
    from_status: string;
    error_type: string;
    route_to?: string;
    action: string;
    result_status?: string;
    reason?: string | null;
    failed_schema?: string | null;
    claim_id?: string | null;
    failed_claim?: string | null;
    metadata?: Record<string, unknown>;
  }>;
  route_to?: string;
  failed_queries?: string[];
  failed_dimensions?: string[];
  failed_competitors?: string[];
  suggested_action?: string;
  rework_count: number;
  metadata?: {
    evidence_gate_details?: {
      missing_competitors?: string[];
      failure_explanation?: string;
      suggested_action?: string;
      query_focus?: string[];
      by_competitor?: Record<string, EvidenceGateCompetitorDiagnostic>;
    };
    swot_validation?: {
      status?: string;
      issue_count?: number;
      issues?: Array<{
        error_type?: string;
        target_agent?: string;
        competitor?: string | null;
        quadrant?: string | null;
        fix_type?: string;
        reason?: string;
        suggested_action?: string;
        query_focus?: string[];
        focus_dimensions?: string[];
      }>;
    };
  };
};

export type EvidenceGateCompetitorDiagnostic = {
  competitor?: string;
  status?: string;
  reason_code?: string;
  explanation?: string;
  total_evidence_count?: number;
  relevant_evidence_count?: number;
  high_count?: number;
  medium_count?: number;
  low_count?: number;
  unrelated_count?: number;
  alias_miss_count?: number;
  collector_dimensions_seen?: string[];
  top_evidence?: Array<{
    evidence_id?: string;
    source_domain?: string | null;
    source_quality?: string | null;
    relevance_level?: string;
    relevance_score?: number;
    confidence?: number;
    relevance_reason?: string;
    collector_dimension?: string;
    url?: string | null;
  }>;
};

export type TraceRecord = {
  trace_id: string;
  task_id: string;
  run_id?: string | null;
  agent_name: string;
  input_summary: string;
  output_summary: string;
  schema_validation_result: string;
  elapsed_time_ms: number;
  retry_count: number;
  error_message?: string;
  model_name?: string;
  token_usage?: number;
};

export type Dag = {
  nodes: Array<{ id: string; label: string; status: string }>;
  edges: Array<{ source: string; target: string; label: string }>;
};
