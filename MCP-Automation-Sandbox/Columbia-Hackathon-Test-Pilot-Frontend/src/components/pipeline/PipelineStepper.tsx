import { Check, ArrowRight } from "lucide-react";
import { PIPELINE_STEPS } from "./PipelineSidebar";
import { cn } from "@/lib/utils";

interface PipelineStepperProps {
  currentStep: number;
  activeStep: number;
  completedSteps: Set<number>;
  onStepClick: (index: number) => void;
}

const PipelineStepper = ({ currentStep, activeStep, completedSteps, onStepClick }: PipelineStepperProps) => {
  return (
    <div className="flex items-center gap-1 overflow-x-auto pb-2 scrollbar-hide">
      {PIPELINE_STEPS.map((step, i) => {
        const isCompleted = completedSteps.has(i);
        const isViewed = currentStep === i;
        const isActive = activeStep === i;
        return (
          <div key={step.id} className="flex items-center">
            <button
              onClick={() => onStepClick(i)}
              className={cn(
                "flex items-center gap-1.5 rounded-full px-3 py-1.5 text-xs font-medium whitespace-nowrap transition-all",
                isCompleted && "bg-success/10 text-success border border-success/20",
                isViewed && !isCompleted && "bg-primary/10 text-primary border border-primary/30",
                isActive && !isViewed && !isCompleted && "bg-primary/5 text-primary border border-primary/20",
                !isCompleted && !isViewed && !isActive && "text-muted-foreground hover:text-foreground",
              )}
            >
              {isCompleted && <Check className="h-3 w-3" />}
              {!isCompleted && <span className="font-mono text-[10px]">{i + 1}</span>}
              {step.label}
            </button>
            {i < PIPELINE_STEPS.length - 1 && (
              <ArrowRight className="h-3 w-3 text-muted-foreground/40 mx-1 shrink-0" />
            )}
          </div>
        );
      })}
    </div>
  );
};

export default PipelineStepper;
