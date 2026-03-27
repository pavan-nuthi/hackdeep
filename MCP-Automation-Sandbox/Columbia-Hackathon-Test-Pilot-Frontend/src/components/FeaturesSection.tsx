import { motion } from "framer-motion";
import { GitBranch, Rocket, FileSearch, Database, Search, Layers, Shield, Cpu, FlaskConical, CloudUpload, Link2 } from "lucide-react";

const steps = [
  { icon: Link2, title: "URL Input", desc: "Feed API URLs to kick off the pipeline" },
  { icon: GitBranch, title: "Clone", desc: "Clone the repository from the provided URL" },
  { icon: Rocket, title: "Deploy", desc: "Deploy sandbox environment via Blaxel" },
  { icon: FileSearch, title: "Extract", desc: "Extract API documentation automatically" },
  { icon: Database, title: "Ingest", desc: "Parse and index the API specification" },
  { icon: Search, title: "Discover", desc: "Mine capabilities, tools, and resources" },
  { icon: Layers, title: "Schema", desc: "Synthesize and validate type schemas" },
  { icon: Shield, title: "Policy", desc: "Configure safety rules and rate limits" },
  { icon: Cpu, title: "Generate", desc: "Generate the MCP server code" },
  { icon: FlaskConical, title: "Test", desc: "Run contract tests against the server" },
  { icon: CloudUpload, title: "Deploy", desc: "Ship to production on Blaxel" },
];

const FeaturesSection = () => {
  return (
    <section id="features" className="py-24 border-t border-border">
      <div className="container">
        <motion.div
          initial={{ opacity: 0, y: 20 }}
          whileInView={{ opacity: 1, y: 0 }}
          viewport={{ once: true }}
          className="text-center mb-16"
        >
          <h2 className="text-3xl sm:text-4xl font-bold mb-4">End-to-end pipeline</h2>
          <p className="text-muted-foreground text-lg max-w-xl mx-auto">
            From URL to deployed MCP server in minutes. Every step automated, governed, and observable.
          </p>
        </motion.div>

        <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4 gap-4">
          {steps.map((step, i) => (
            <motion.div
              key={step.title + i}
              initial={{ opacity: 0, y: 20 }}
              whileInView={{ opacity: 1, y: 0 }}
              viewport={{ once: true }}
              transition={{ delay: i * 0.05 }}
              className="group rounded-xl border border-border bg-card p-5 hover:bg-surface-hover hover:border-primary/20 transition-all"
            >
              <div className="flex items-center gap-3 mb-3">
                <div className="flex h-9 w-9 items-center justify-center rounded-lg bg-primary/10 text-primary">
                  <step.icon className="h-4.5 w-4.5" />
                </div>
                <div className="flex items-center gap-2">
                  <span className="text-xs font-mono text-muted-foreground">{String(i + 1).padStart(2, '0')}</span>
                  <h3 className="font-semibold text-foreground">{step.title}</h3>
                </div>
              </div>
              <p className="text-sm text-muted-foreground">{step.desc}</p>
            </motion.div>
          ))}
        </div>
      </div>
    </section>
  );
};

export default FeaturesSection;
