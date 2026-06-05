export type Task = {
  task_id: string;
  product_name: string;
  competitors: string[];
  region: string;
  industry: string;
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
    claims?: Claim[];
    writer_diagnostics?: WriterDiagnostics;
  };
  claims: Claim[];
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

export type WorkflowSummary = {
  run_id?: string;
  task_id?: string;
  workflow_engine_requested?: string;
  workflow_engine_used?: string;
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
  knowledge_hits?: KnowledgeHit[];
  retrieved_knowledge_chunk_count?: number;
  knowledge_retrieval_strategy?: KnowledgeRetrievalStrategy;
};

export type CollectorDiagnostics = {
  collector_mode_requested?: string;
  collector_mode_used?: string;
  web_search_attempted?: boolean;
  web_search_success?: boolean;
  query_count?: number;
  evidence_count?: number;
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
  status: "passed" | "failed" | "manual_review";
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
