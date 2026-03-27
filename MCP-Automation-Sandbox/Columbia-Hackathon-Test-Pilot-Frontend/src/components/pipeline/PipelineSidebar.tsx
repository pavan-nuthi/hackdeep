import { Link2, GitBranch, Rocket, FileSearch, Database, Search, Layers, Shield, Cpu, FlaskConical, CloudUpload, Check, LayoutDashboard, Bot, TestTube2 } from "lucide-react";
import { cn } from "@/lib/utils";

export interface PipelineStep {
  id: string;
  label: string;
  subtitle: string;
  icon: React.ComponentType<{ className?: string }>;
}

export const PIPELINE_STEPS: PipelineStep[] = [
  { id: "url-input", label: "URL Input", subtitle: "Feed URLs", icon: Link2 },
  { id: "clone", label: "Clone", subtitle: "Clone repos", icon: GitBranch },
  { id: "extract", label: "Extract", subtitle: "API docs", icon: FileSearch },
  { id: "ingest", label: "Ingest", subtitle: "Parse & index", icon: Database },
  { id: "discover", label: "Discover", subtitle: "Mine capabilities", icon: Search },
  { id: "schema", label: "Schema", subtitle: "Synthesize types", icon: Layers },
  { id: "policy", label: "Policy", subtitle: "Execution rules", icon: Shield },
  { id: "generate", label: "Generate", subtitle: "MCP server", icon: Cpu },
  { id: "mcp-test", label: "MCP Tests", subtitle: "Validate servers", icon: TestTube2 },
  { id: "user-test", label: "Deep Agent Test", subtitle: "AI QA agents", icon: Bot },
];

interface PipelineSidebarProps {
  currentStep: number;
  activeStep: number;
  completedSteps: Set<number>;
  onStepClick: (index: number) => void;
}

const PipelineSidebar = ({ currentStep, activeStep, completedSteps, onStepClick }: PipelineSidebarProps) => {
  return (
    <aside className="w-56 shrink-0 border-r border-border bg-sidebar h-[calc(100vh-4rem)] sticky top-16 overflow-y-auto">
      <div className="p-4">
        <Link to="/" className="flex items-center gap-2 text-sm text-muted-foreground hover:text-foreground transition-colors mb-6">
          <LayoutDashboard className="h-4 w-4" />
          Dashboard
        </Link>

        <div className="text-[11px] font-semibold uppercase tracking-wider text-muted-foreground mb-3">Pipeline</div>
        <div className="space-y-0.5">
          {PIPELINE_STEPS.map((step, i) => {
            const isCompleted = completedSteps.has(i);
            const isViewed = currentStep === i;
            const isActive = activeStep === i;
            return (
              <button
                key={step.id}
                onClick={() => onStepClick(i)}
                className={cn(
                  "w-full flex items-center gap-3 rounded-lg px-3 py-2.5 text-left transition-all text-sm",
                  isViewed && "bg-sidebar-accent text-sidebar-accent-foreground border-l-2 border-primary",
                  !isViewed && "text-sidebar-foreground hover:bg-sidebar-accent/50",
                )}
              >
                <div className={cn(
                  "flex h-6 w-6 shrink-0 items-center justify-center rounded-full text-xs font-mono",
                  isCompleted && "bg-success/20 text-success",
                  isActive && !isCompleted && "bg-primary/20 text-primary border border-primary/30 animate-pulse",
                  isViewed && !isActive && !isCompleted && "bg-primary/20 text-primary border border-primary/30",
                  !isCompleted && !isActive && !isViewed && "bg-muted text-muted-foreground",
                )}>
                  {isCompleted ? <Check className="h-3.5 w-3.5" /> : i + 1}
                </div>
                <div className="min-w-0">
                  <div className={cn("font-medium truncate", (isViewed || isActive) && "text-foreground")}>{step.label}</div>
                  <div className="text-[11px] text-muted-foreground truncate">{step.subtitle}</div>
                </div>
              </button>
            );
          })}
        </div>
      </div>
    </aside>
  );
};

// Need to add Link import
import { Link } from "react-router-dom";

export default PipelineSidebar;
