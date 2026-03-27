import { useState } from "react";
import { PIPELINE_STEPS } from "./PipelineSidebar";
import {
  Plus, Trash2, Globe, ArrowRight, Check, Shield, Loader2,
  CheckCircle2, XCircle, AlertTriangle, Bot, Zap, Activity,
  Timer, Network, Terminal, ChevronDown, ChevronRight,
} from "lucide-react";
import { cn } from "@/lib/utils";
import type { StepData, ToolRow, TestResultDetail } from "@/hooks/usePipeline";

interface StepContentProps {
  stepIndex: number;
  pipeline: {
    isRunning: boolean;
    stepData: Record<number, StepData>;
    error: string | null;
    startPipeline: (urls: string[], token: string) => void;
    cancel: () => void;
  };
}

/* ── URL Input (step 0) ─────────────────────────────────────────────── */
const UrlInputStep = ({ onStart, isRunning }: { onStart: (urls: string[], token: string) => void; isRunning: boolean }) => {
  const [urls, setUrls] = useState<string[]>([
    "https://github.com/sAVItar02/Loom-WebDataLab",
  ]);
  const [authToken, setAuthToken] = useState("");

  const addUrl = () => setUrls([...urls, ""]);
  const removeUrl = (i: number) => setUrls(urls.filter((_, idx) => idx !== i));
  const updateUrl = (i: number, val: string) => {
    const updated = [...urls];
    updated[i] = val;
    setUrls(updated);
  };

  const handleStart = () => {
    const valid = urls.filter((u) => u.trim());
    if (valid.length) onStart(valid, authToken.trim());
  };

  return (
    <div>
      <h1 className="text-3xl font-bold mb-2">URL Input</h1>
      <p className="text-muted-foreground mb-8">
        Feed GitHub repository URLs to start the Test Pilot pipeline. Every repo is cloned into a shared sandbox, scanned for OpenAPI specs, and processed end-to-end. Repos without specs are cloned but skipped for MCP generation.
      </p>

      <div className="space-y-3 mb-6">
        {urls.map((url, i) => (
          <div key={i} className="flex items-center gap-3">
            <div className="flex-1 flex items-center gap-2 rounded-lg border border-border bg-muted/50 px-4 py-3">
              <Globe className="h-4 w-4 text-muted-foreground shrink-0" />
              <input
                type="url"
                value={url}
                onChange={(e) => updateUrl(i, e.target.value)}
                placeholder="https://github.com/org/repo"
                className="flex-1 bg-transparent text-sm text-foreground placeholder:text-muted-foreground outline-none font-mono"
                disabled={isRunning}
              />
            </div>
            {urls.length > 1 && !isRunning && (
              <button onClick={() => removeUrl(i)} className="p-2 rounded-lg text-muted-foreground hover:text-destructive hover:bg-destructive/10 transition-colors">
                <Trash2 className="h-4 w-4" />
              </button>
            )}
          </div>
        ))}

        <div className="flex items-center gap-2 rounded-lg border border-border bg-muted/50 px-4 py-3">
          <Shield className="h-4 w-4 text-muted-foreground shrink-0" />
          <input
            type="text"
            value={authToken}
            onChange={(e) => setAuthToken(e.target.value)}
            placeholder="[Optional] Auth0 Bearer Token (for Security Probes)"
            className="flex-1 bg-transparent text-sm text-foreground placeholder:text-muted-foreground outline-none font-mono"
            disabled={isRunning}
          />
        </div>

        <button
          onClick={addUrl}
          disabled={isRunning}
          className="flex w-full items-center justify-center gap-2 rounded-lg border border-dashed border-border py-2 text-sm text-muted-foreground hover:bg-muted/50 transition-colors"
        >
          <Plus className="h-4 w-4" /> Add Repository
        </button>
      </div>

      <div className="flex items-center gap-3">
        <button
          onClick={handleStart}
          disabled={urls.every((u) => !u.trim()) || isRunning}
          className="flex items-center gap-2 rounded-lg bg-primary px-6 py-2.5 text-sm font-semibold text-primary-foreground hover:bg-primary/90 transition-colors disabled:opacity-50 disabled:cursor-not-allowed ml-auto"
        >
          {isRunning ? (
            <>
              <Loader2 className="h-4 w-4 animate-spin" /> Running...
            </>
          ) : (
            <>
              Start Pipeline <ArrowRight className="h-4 w-4" />
            </>
          )}
        </button>
      </div>
    </div>
  );
};

/* ── Live Processing Step ────────────────────────────────────────────── */
const LiveProcessingStep = ({
  title,
  description,
  data,
}: {
  title: string;
  description: string;
  data?: StepData;
}) => {
  const items = data?.items ?? [];
  const status = data?.status ?? "pending";

  return (
    <div>
      <div className="flex items-center gap-3 mb-2">
        <h1 className="text-3xl font-bold">{title}</h1>
        {status === "running" && <Loader2 className="h-5 w-5 text-primary animate-spin" />}
        {status === "done" && <CheckCircle2 className="h-5 w-5 text-success" />}
        {status === "error" && <XCircle className="h-5 w-5 text-destructive" />}
      </div>
      <p className="text-muted-foreground mb-8">{description}</p>

      {items.length === 0 && status === "pending" && (
        <div className="flex items-center gap-2 text-sm text-muted-foreground">
          <Loader2 className="h-4 w-4 animate-spin" />
          Waiting for previous steps to complete...
        </div>
      )}

      <div className="space-y-3 mb-8">
        {items.map((item, i) => (
          <div
            key={i}
            className="flex items-center gap-3 rounded-lg border border-border bg-card p-4 transition-all duration-300 animate-in fade-in slide-in-from-bottom-1"
          >
            {status === "running" && i === items.length - 1 ? (
              <Loader2 className="h-4 w-4 text-primary animate-spin shrink-0" />
            ) : (
              <Check className="h-4 w-4 text-success shrink-0" />
            )}
            <span className="text-sm font-mono text-foreground">{item}</span>
          </div>
        ))}
      </div>

      {status === "running" && (
        <div className="flex items-center gap-2 text-sm text-muted-foreground">
          <Loader2 className="h-4 w-4 animate-spin" />
          Processing...
        </div>
      )}
    </div>
  );
};

/* ── Policy Step (with real tool rows) ───────────────────────────────── */
const LivePolicyStep = ({ data }: { data?: StepData }) => {
  const items = data?.items ?? [];
  const toolRows: ToolRow[] = data?.toolRows ?? [];
  const status = data?.status ?? "pending";

  return (
    <div>
      <div className="flex items-center gap-3 mb-2">
        <h1 className="text-3xl font-bold">Policy Configuration</h1>
        {status === "running" && <Loader2 className="h-5 w-5 text-primary animate-spin" />}
        {status === "done" && <CheckCircle2 className="h-5 w-5 text-success" />}
      </div>
      <p className="text-muted-foreground mb-8">Configuring safety rules, rate limits, and execution policies for each tool</p>

      {toolRows.length > 0 ? (
        <div className="rounded-xl border border-border overflow-hidden mb-6">
          <div className="grid grid-cols-[1fr_120px_140px_100px_40px] gap-4 px-6 py-3 border-b border-border text-xs font-semibold uppercase tracking-wider text-muted-foreground">
            <span>Tool</span>
            <span>Safety Level</span>
            <span>Execution</span>
            <span>Rate Limit</span>
            <span></span>
          </div>
          {toolRows.map((tool, i) => (
            <div key={tool.name} className={cn(
              "grid grid-cols-[1fr_120px_140px_100px_40px] gap-4 px-6 py-4 items-center border-b border-border last:border-b-0",
              i % 2 === 0 ? "bg-card" : "bg-muted/30"
            )}>
              <div>
                <div className="font-semibold text-foreground">{tool.name}</div>
                <div className="text-xs text-muted-foreground font-mono">{tool.method} {tool.path}</div>
              </div>
              <span className={cn(
                "inline-flex items-center rounded-md px-2.5 py-1 text-xs font-medium w-fit",
                tool.safety === "Read" ? "bg-success/10 text-success" : "bg-warning/10 text-warning"
              )}>
                {tool.safety}
              </span>
              <span className={cn(
                "inline-flex items-center gap-1.5 rounded-md px-2.5 py-1 text-xs font-medium w-fit",
                tool.execution === "Auto Execute" ? "bg-success/10 text-success" : "bg-primary/10 text-primary"
              )}>
                <Shield className="h-3 w-3" />
                {tool.execution}
              </span>
              <span className="text-sm font-mono text-foreground">{tool.rateLimit} <span className="text-muted-foreground">/min</span></span>
              <div className={cn("h-3 w-3 rounded-full", status === "done" ? "bg-success" : "border-2 border-muted-foreground/30")} />
            </div>
          ))}
        </div>
      ) : status === "pending" ? (
        <div className="flex items-center gap-2 text-sm text-muted-foreground">
          <Loader2 className="h-4 w-4 animate-spin" />
          Waiting for previous steps...
        </div>
      ) : null}

      {items.length > 0 && (
        <div className="space-y-2">
          {items.map((item, i) => (
            <div key={i} className="flex items-center gap-2 text-sm font-mono text-foreground">
              <Check className="h-3 w-3 text-success shrink-0" />
              {item}
            </div>
          ))}
        </div>
      )}

      {status === "running" && (
        <div className="flex items-center gap-2 text-sm text-muted-foreground mt-4">
          <Loader2 className="h-4 w-4 animate-spin" />
          Auto-configuring policies...
        </div>
      )}
    </div>
  );
};

/* ── Rich AI Agent Test Step ─────────────────────────────────────────── */
const LiveTestStep = ({ data }: { data?: StepData }) => {
  const [expandedTests, setExpandedTests] = useState<Set<string>>(new Set());
  const items = data?.items ?? [];
  const testResults: TestResultDetail[] = data?.testResults ?? [];
  const status = data?.status ?? "pending";
  const passed = data?.passed ?? 0;
  const total = data?.total ?? 0;

  const toggleTest = (name: string) => {
    setExpandedTests((prev) => {
      const next = new Set(prev);
      if (next.has(name)) next.delete(name);
      else next.add(name);
      return next;
    });
  };

  return (
    <div>
      {/* Header with AI Agent branding */}
      <div className="flex items-center gap-3 mb-2">
        <div className="flex h-10 w-10 items-center justify-center rounded-xl bg-primary/10 border border-primary/20">
          <Bot className="h-5 w-5 text-primary" />
        </div>
        <div>
          <h1 className="text-3xl font-bold">AI Agent Testing</h1>
          <p className="text-sm text-muted-foreground">Autonomous cross-service integration tests via MCP tools</p>
        </div>
        {status === "running" && <Loader2 className="h-5 w-5 text-primary animate-spin ml-auto" />}
        {status === "done" && <CheckCircle2 className="h-5 w-5 text-success ml-auto" />}
        {status === "error" && <XCircle className="h-5 w-5 text-destructive ml-auto" />}
      </div>

      {/* Agent activity card while running */}
      {status === "running" && (
        <div className="mt-6 rounded-xl border border-primary/20 bg-primary/5 overflow-hidden">
          <div className="flex items-center gap-3 px-5 py-3 border-b border-primary/10">
            <div className="relative">
              <Bot className="h-5 w-5 text-primary" />
              <span className="absolute -top-0.5 -right-0.5 h-2 w-2 rounded-full bg-primary animate-pulse" />
            </div>
            <span className="text-sm font-semibold text-primary">Agent Active</span>
            <span className="text-xs text-muted-foreground ml-auto">Real-time execution</span>
          </div>
          <div className="p-4 font-mono text-xs space-y-2 max-h-48 overflow-y-auto">
            {items.map((item, i) => (
              <div key={i} className="flex items-start gap-2">
                {i === items.length - 1 ? (
                  <Loader2 className="h-3 w-3 text-primary animate-spin shrink-0 mt-0.5" />
                ) : (
                  <Zap className="h-3 w-3 text-primary/50 shrink-0 mt-0.5" />
                )}
                <span className={cn(
                  "text-foreground/80",
                  i === items.length - 1 && "text-primary font-medium"
                )}>{item}</span>
              </div>
            ))}
          </div>
        </div>
      )}

      {/* Score dashboard */}
      {status === "done" && total > 0 && (
        <div className="mt-6 grid grid-cols-4 gap-4">
          <div className="rounded-xl border border-border bg-card p-5 text-center">
            <Activity className="h-5 w-5 text-muted-foreground mx-auto mb-2" />
            <div className="text-3xl font-bold text-foreground">{total}</div>
            <div className="text-xs text-muted-foreground mt-1">Test Cases</div>
          </div>
          <div className="rounded-xl border border-success/30 bg-success/5 p-5 text-center">
            <CheckCircle2 className="h-5 w-5 text-success mx-auto mb-2" />
            <div className="text-3xl font-bold text-success">{passed}</div>
            <div className="text-xs text-muted-foreground mt-1">Passed</div>
          </div>
          <div className={cn(
            "rounded-xl border p-5 text-center",
            total - passed > 0 ? "border-destructive/30 bg-destructive/5" : "border-border bg-card"
          )}>
            <XCircle className={cn("h-5 w-5 mx-auto mb-2", total - passed > 0 ? "text-destructive" : "text-muted-foreground")} />
            <div className={cn("text-3xl font-bold", total - passed > 0 ? "text-destructive" : "text-foreground")}>{total - passed}</div>
            <div className="text-xs text-muted-foreground mt-1">Failed</div>
          </div>
          <div className="rounded-xl border border-border bg-card p-5 text-center">
            <Timer className="h-5 w-5 text-muted-foreground mx-auto mb-2" />
            <div className="text-3xl font-bold text-foreground">
              {testResults.length > 0
                ? Math.round(testResults.reduce((a, t) => a + t.duration_ms, 0) / testResults.length)
                : 0}
            </div>
            <div className="text-xs text-muted-foreground mt-1">Avg ms</div>
          </div>
        </div>
      )}

      {/* Pass rate bar */}
      {status === "done" && total > 0 && (
        <div className="mt-6 rounded-xl border border-border bg-card p-5">
          <div className="flex items-center justify-between mb-3">
            <span className="text-sm font-semibold text-foreground">Pass Rate</span>
            <span className="text-sm font-bold text-foreground">{Math.round((passed / total) * 100)}%</span>
          </div>
          <div className="h-3 rounded-full bg-muted overflow-hidden">
            <div
              className={cn(
                "h-full rounded-full transition-all duration-1000",
                passed === total ? "bg-success" : passed > 0 ? "bg-warning" : "bg-destructive",
              )}
              style={{ width: `${(passed / total) * 100}%` }}
            />
          </div>
        </div>
      )}

      {/* Test result cards */}
      {testResults.length > 0 && (
        <div className="mt-6 space-y-3">
          <div className="flex items-center gap-2 mb-1">
            <Network className="h-4 w-4 text-muted-foreground" />
            <span className="text-sm font-semibold text-foreground">Test Execution Details</span>
          </div>
          {testResults.map((tr) => {
            const isExpanded = expandedTests.has(tr.test_name);
            return (
              <div key={tr.test_name} className="rounded-xl border border-border bg-card overflow-hidden">
                <button
                  onClick={() => toggleTest(tr.test_name)}
                  className="w-full flex items-center gap-3 px-5 py-4 text-left hover:bg-muted/30 transition-colors"
                >
                  {tr.passed ? (
                    <div className="flex h-8 w-8 items-center justify-center rounded-lg bg-success/10 border border-success/20">
                      <CheckCircle2 className="h-4 w-4 text-success" />
                    </div>
                  ) : (
                    <div className="flex h-8 w-8 items-center justify-center rounded-lg bg-destructive/10 border border-destructive/20">
                      <XCircle className="h-4 w-4 text-destructive" />
                    </div>
                  )}
                  <div className="flex-1 min-w-0">
                    <div className="font-semibold text-foreground text-sm">{tr.test_name}</div>
                    <div className="text-xs text-muted-foreground truncate">{tr.description}</div>
                  </div>
                  <div className="flex items-center gap-3 shrink-0">
                    <span className={cn(
                      "inline-flex items-center rounded-md px-2 py-1 text-xs font-medium",
                      tr.passed ? "bg-success/10 text-success" : "bg-destructive/10 text-destructive",
                    )}>
                      {tr.passed ? "PASS" : "FAIL"}
                    </span>
                    <span className="text-xs text-muted-foreground font-mono">{tr.duration_ms}ms</span>
                    {isExpanded ? (
                      <ChevronDown className="h-4 w-4 text-muted-foreground" />
                    ) : (
                      <ChevronRight className="h-4 w-4 text-muted-foreground" />
                    )}
                  </div>
                </button>

                {isExpanded && (
                  <div className="border-t border-border">
                    {/* Narrative: what the agent experienced */}
                    {tr.narrative && (
                      <div className="px-5 py-4 border-b border-border">
                        <div className="flex items-center gap-2 text-xs font-semibold text-muted-foreground uppercase tracking-wider mb-2">
                          <Bot className="h-3 w-3" />
                          Agent Narrative
                        </div>
                        <p className="text-sm text-foreground/90 leading-relaxed italic">&ldquo;{tr.narrative}&rdquo;</p>
                      </div>
                    )}

                    {/* MCP tool call trace */}
                    <div className="px-5 py-3 border-b border-border">
                      <div className="flex items-center gap-2 text-xs font-semibold text-muted-foreground uppercase tracking-wider mb-3">
                        <Terminal className="h-3 w-3" />
                        Agent Trace
                      </div>
                      <div className="space-y-2">
                        {tr.steps.map((s, si) => (
                          <div key={si} className="relative pl-6">
                            <div className="absolute left-2 top-0 bottom-0 w-px bg-border" />
                            <div className={cn(
                              "absolute left-0.5 top-1.5 h-3 w-3 rounded-full border-2",
                              s.success ? "bg-success/20 border-success" : "bg-destructive/20 border-destructive",
                            )} />
                            <div className="rounded-lg bg-muted/50 px-4 py-3">
                              <div className="flex items-center justify-between mb-1">
                                <div className="flex items-center gap-2">
                                  <Zap className={cn("h-3 w-3", s.success ? "text-success" : "text-destructive")} />
                                  <span className={cn(
                                    "text-sm font-medium text-foreground",
                                    s.success ? "" : "text-destructive/90",
                                  )}>{s.action}</span>
                                </div>
                                <div className="flex items-center gap-2 shrink-0">
                                  <span className={cn(
                                    "inline-flex items-center rounded px-1.5 py-0.5 text-[10px] font-bold uppercase",
                                    s.success ? "bg-success/10 text-success" : "bg-destructive/10 text-destructive",
                                  )}>{s.success ? "OK" : "FAIL"}</span>
                                  <span className="text-xs font-mono text-muted-foreground">{s.duration_ms}ms</span>
                                </div>
                              </div>
                              {s.error && (
                                <div className="mt-2 flex items-start gap-2 rounded-md bg-destructive/5 border border-destructive/20 px-3 py-2">
                                  <AlertTriangle className="h-3 w-3 text-destructive shrink-0 mt-0.5" />
                                  <span className="text-xs text-foreground/70">{s.error}</span>
                                </div>
                              )}
                            </div>
                          </div>
                        ))}
                      </div>
                    </div>

                    {/* Analytical assessment */}
                    {tr.analysis && (
                      <div className="px-5 py-4 border-b border-border bg-muted/10">
                        <div className="flex items-center gap-2 text-xs font-semibold text-muted-foreground uppercase tracking-wider mb-2">
                          <Activity className="h-3 w-3" />
                          Analysis
                        </div>
                        <p className="text-sm text-foreground/80 leading-relaxed">{tr.analysis}</p>
                      </div>
                    )}

                    {/* Summary */}
                    <div className="px-5 py-3 bg-muted/20">
                      <div className="flex items-center gap-2">
                        <Bot className="h-3.5 w-3.5 text-primary" />
                        <span className="text-xs font-medium text-muted-foreground">{tr.summary}</span>
                      </div>
                    </div>
                  </div>
                )}
              </div>
            );
          })}
        </div>
      )}

      {/* Progress items when no detailed results yet */}
      {items.length > 0 && !testResults.length && status !== "running" && (
        <div className="mt-6 space-y-3">
          {items.map((item, i) => (
            <div key={i} className="flex items-center gap-3 rounded-lg border border-border bg-card p-4">
              <Check className="h-4 w-4 text-success shrink-0" />
              <span className="text-sm font-mono text-foreground">{item}</span>
            </div>
          ))}
        </div>
      )}

      {status === "pending" && (
        <div className="mt-8 flex items-center gap-2 text-sm text-muted-foreground">
          <Loader2 className="h-4 w-4 animate-spin" />
          Waiting for MCP servers to be deployed...
        </div>
      )}
    </div>
  );
};

/* ── Step descriptions ───────────────────────────────────────────────── */
const STEP_META: Record<string, { title: string; description: string }> = {
  "clone": { title: "Clone Repositories", description: "Cloning all repositories locally. Repos without OpenAPI specs will be auto-inferred using AI." },
  "extract": { title: "Extract API Docs", description: "Scanning all cloned repositories for OpenAPI/Swagger specifications and extracting them for processing." },
  "ingest": { title: "Ingest Specifications", description: "Parsing and indexing API specifications — extracting endpoints, schemas, and metadata." },
  "discover": { title: "Discover Capabilities", description: "Mining MCP tool capabilities from each API endpoint — identifying callable tools and their parameters." },
  "schema": { title: "Synthesize Schemas", description: "Generating and validating JSON type schemas for every tool parameter and response." },
  "generate": { title: "Generate MCP Server", description: "Using Gemini LLM to generate complete MCP server code including server.py, tests, and deployment config." },
  "mcp-test": { title: "MCP Server Validation", description: "Validating generated MCP server code — checking file integrity, syntax, dependencies, and tool declarations." },
};

/* ── Main Switch ─────────────────────────────────────────────────────── */
const StepContent = ({ stepIndex, pipeline }: StepContentProps) => {
  const step = PIPELINE_STEPS[stepIndex];
  if (!step) {
    return (
      <div className="flex items-center gap-2 text-muted-foreground">
        <CheckCircle2 className="h-5 w-5 text-success" />
        <span>Pipeline complete.</span>
      </div>
    );
  }
  const data = pipeline.stepData[stepIndex];

  switch (step.id) {
    case "url-input":
      return <UrlInputStep onStart={pipeline.startPipeline} isRunning={pipeline.isRunning} />;
    case "policy":
      return <LivePolicyStep data={data} />;
    case "user-test":
      return <LiveTestStep data={data} />;
    default: {
      const meta = STEP_META[step.id] || { title: step.label, description: step.subtitle };
      return <LiveProcessingStep title={meta.title} description={meta.description} data={data} />;
    }
  }
};

export default StepContent;