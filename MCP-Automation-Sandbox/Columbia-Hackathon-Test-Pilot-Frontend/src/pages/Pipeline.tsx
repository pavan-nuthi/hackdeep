import Navbar from "@/components/Navbar";
import PipelineSidebar from "@/components/pipeline/PipelineSidebar";
import PipelineStepper from "@/components/pipeline/PipelineStepper";
import StepContent from "@/components/pipeline/StepContent";
import { usePipeline } from "@/hooks/usePipeline";

const Pipeline = () => {
  const pipeline = usePipeline();

  return (
    <div className="min-h-screen bg-background">
      <Navbar />
      <div className="flex pt-16">
        <PipelineSidebar
          currentStep={pipeline.viewedStep}
          activeStep={pipeline.activeStep}
          completedSteps={pipeline.completedSteps}
          onStepClick={pipeline.navigateToStep}
        />
        <main className="flex-1 min-w-0">
          <div className="border-b border-border px-8 py-4">
            <PipelineStepper
              currentStep={pipeline.viewedStep}
              activeStep={pipeline.activeStep}
              completedSteps={pipeline.completedSteps}
              onStepClick={pipeline.navigateToStep}
            />
          </div>
          <div className="p-8 max-w-4xl">
            <StepContent
              stepIndex={pipeline.viewedStep}
              pipeline={pipeline}
            />
          </div>
        </main>
      </div>
    </div>
  );
};

export default Pipeline;
