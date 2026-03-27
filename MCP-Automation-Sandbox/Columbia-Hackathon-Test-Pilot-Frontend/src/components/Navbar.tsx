import { Link } from "react-router-dom";
import { ArrowRight, ExternalLink } from "lucide-react";

const Navbar = () => {
  return (
    <nav className="fixed top-0 left-0 right-0 z-50 border-b border-border bg-background/80 backdrop-blur-xl">
      <div className="container flex h-16 items-center justify-between">
        <Link to="/" className="flex items-center gap-2.5">
          <div className="flex h-8 w-8 items-center justify-center rounded-lg bg-primary/10 border border-primary/20">
            <svg width="18" height="18" viewBox="0 0 24 24" fill="none" className="text-primary">
              <circle cx="12" cy="12" r="3" fill="currentColor" />
              <circle cx="12" cy="4" r="2" fill="currentColor" opacity="0.6" />
              <circle cx="4" cy="16" r="2" fill="currentColor" opacity="0.6" />
              <circle cx="20" cy="16" r="2" fill="currentColor" opacity="0.6" />
              <line x1="12" y1="7" x2="12" y2="9" stroke="currentColor" strokeWidth="1.5" opacity="0.4" />
              <line x1="6" y1="15" x2="9.5" y2="13.5" stroke="currentColor" strokeWidth="1.5" opacity="0.4" />
              <line x1="18" y1="15" x2="14.5" y2="13.5" stroke="currentColor" strokeWidth="1.5" opacity="0.4" />
            </svg>
          </div>
          <span className="text-lg font-bold tracking-tight text-foreground">Test Pilots</span>
        </Link>

        <div className="hidden md:flex items-center gap-8">
          <Link to="/" className="text-sm text-muted-foreground hover:text-foreground transition-colors">Services</Link>
          <Link to="/pipeline" className="text-sm text-muted-foreground hover:text-foreground transition-colors">Pipeline</Link>
          <a href="#features" className="text-sm text-muted-foreground hover:text-foreground transition-colors">Features</a>
        </div>

        <div className="flex items-center gap-3">
          <span className="hidden sm:flex items-center gap-1.5 text-sm text-muted-foreground px-3 py-1.5 rounded-full border border-border">
            <span className="text-primary">✦</span> Powered by Blaxel
          </span>
          <Link
            to="/pipeline"
            className="inline-flex items-center gap-2 rounded-lg bg-primary px-4 py-2 text-sm font-semibold text-primary-foreground hover:bg-primary/90 transition-colors"
          >
            Try Generator
            <ExternalLink className="h-3.5 w-3.5" />
          </Link>
        </div>
      </div>
    </nav>
  );
};

export default Navbar;
