import React from 'react';
import { useNavigate } from 'react-router-dom';

const Landing = () => {
  const navigate = useNavigate();

  return (
    <div className="landing-container animate-fade-in">
      {/* Ambient Background Effects */}
      <div className="bg-glow top-glow"></div>
      <div className="bg-grid"></div>

      {/* Sleek Navigation Bar */}
      <nav className="navbar">
        <div className="nav-brand">
          <div className="brand-dot"></div>
          <span className="brand-text">Loan Wizard <span className="brand-accent">OS</span></span>
        </div>
        <div className="nav-links">
          <a href="#features">Architecture</a>
          <a href="#security">Security</a>
          <a href="#compliance">RBI V-CIP</a>
        </div>
        <div className="nav-actions">
          <button className="btn-ghost" onClick={() => navigate('/login')}>
            Staff Login
          </button>
        </div>
      </nav>

      {/* Hero Section */}
      <section className="hero">
        <div className="hero-content">
          <div className="badge">
            <span className="badge-pulse"></span>
            Production-Ready Fintech AI
          </div>
          <h1 className="text-gradient">
            The Future of <br />Instant Loan Approvals
          </h1>
          <p className="hero-subtitle">
            Experience the world's first fully autonomous AI loan officer. 
            Zero paperwork, zero waiting. Just intelligent, RBI-compliant finance.
          </p>
          <div className="hero-actions">
            {/* Primary CTA directs evaluators to the Staff Portal */}
            <button className="btn-primary" onClick={() => navigate('/login')}>
              Enter Command Center
              <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" style={{ marginLeft: '8px' }}>
                <line x1="5" y1="12" x2="19" y2="12"></line>
                <polyline points="12 5 19 12 12 19"></polyline>
              </svg>
            </button>
            <button className="btn-outline" onClick={() => document.getElementById('features').scrollIntoView({ behavior: 'smooth' })}>
              Explore Architecture
            </button>
          </div>
        </div>
        
        <div className="hero-visual">
          <div className="visual-glow"></div>
          <img 
            src="/fintech_ai_hero_1777804781560.png" 
            alt="AI Agent Visual" 
            className="floating-image glass-border"
            onError={(e) => {
              e.target.src = 'https://images.unsplash.com/photo-1639762681485-074b7f938ba0?auto=format&fit=crop&q=80&w=2832&ixlib=rb-4.0.3';
            }}
          />
        </div>
      </section>

      {/* Features Grid - Upgraded with SVGs and Glassmorphism */}
      <section id="features" className="features">
        <h2 className="section-title">Powered by Agentic AI</h2>
        <div className="features-grid">
          
          <div className="glass-card feature-card">
            <div className="feature-icon-wrapper">
              <svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="var(--accent-primary)" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                <rect x="2" y="2" width="20" height="8" rx="2" ry="2"></rect>
                <rect x="2" y="14" width="20" height="8" rx="2" ry="2"></rect>
                <line x1="6" y1="6" x2="6.01" y2="6"></line>
                <line x1="6" y1="18" x2="6.01" y2="18"></line>
              </svg>
            </div>
            <h3>Supervisor-Worker DAG</h3>
            <p>LangGraph orchestrates 6 distinct AI workers, ensuring deterministic, hallucination-free routing.</p>
          </div>

          <div className="glass-card feature-card">
            <div className="feature-icon-wrapper">
              <svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="var(--accent-secondary)" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                <polygon points="13 2 3 14 12 14 11 22 21 10 12 10 13 2"></polygon>
              </svg>
            </div>
            <h3>Sub-Second Inference</h3>
            <p>Local Llama 3.1 and Gemma 3 models execute on-demand, eliminating cloud API latency and drop-offs.</p>
          </div>

          <div className="glass-card feature-card">
            <div className="feature-icon-wrapper">
              <svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="var(--success)" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                <path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z"></path>
              </svg>
            </div>
            <h3>RBI V-CIP Compliant</h3>
            <p>End-to-end encryption, automated liveness checks, and seamless human-in-the-loop escalation nodes.</p>
          </div>

        </div>
      </section>

      <style jsx>{`
        .landing-container {
          position: relative;
          padding: 0 40px;
          max-width: 1400px;
          margin: 0 auto;
          min-height: 100vh;
        }

        /* Ambient Backgrounds */
        .bg-glow {
          position: absolute;
          width: 800px;
          height: 800px;
          background: radial-gradient(circle, rgba(59, 130, 246, 0.15) 0%, transparent 60%);
          filter: blur(80px);
          z-index: -2;
          pointer-events: none;
        }
        .top-glow { top: -400px; left: 50%; transform: translateX(-50%); }
        
        .bg-grid {
          position: absolute;
          inset: 0;
          background-image: 
            linear-gradient(to right, rgba(255,255,255,0.02) 1px, transparent 1px),
            linear-gradient(to bottom, rgba(255,255,255,0.02) 1px, transparent 1px);
          background-size: 60px 60px;
          z-index: -1;
          pointer-events: none;
          mask-image: linear-gradient(to bottom, black 40%, transparent 100%);
          -webkit-mask-image: linear-gradient(to bottom, black 40%, transparent 100%);
        }

        /* Navbar */
        .navbar {
          display: flex;
          justify-content: space-between;
          align-items: center;
          padding: 24px 0;
          border-bottom: 1px solid rgba(255,255,255,0.05);
          margin-bottom: 40px;
        }
        .nav-brand {
          display: flex;
          align-items: center;
          gap: 12px;
        }
        .brand-text {
          font-family: var(--font-heading);
          font-weight: 800;
          font-size: 20px;
          letter-spacing: -0.02em;
        }
        .brand-accent { color: var(--accent-primary); }
        
        .nav-links {
          display: flex;
          gap: 32px;
        }
        .nav-links a {
          color: var(--text-secondary);
          text-decoration: none;
          font-size: 14px;
          font-weight: 500;
          transition: color 0.2s;
        }
        .nav-links a:hover { color: var(--text-primary); }

        .btn-ghost {
          background: transparent;
          border: 1px solid var(--border);
          color: var(--text-primary);
          padding: 8px 20px;
          border-radius: 100px;
          font-size: 14px;
          font-weight: 600;
          cursor: pointer;
          transition: all 0.2s;
        }
        .btn-ghost:hover {
          background: rgba(255,255,255,0.05);
          border-color: var(--accent-primary);
        }

        /* Hero */
        .hero {
          display: flex;
          align-items: center;
          justify-content: space-between;
          min-height: 75vh;
          gap: 60px;
        }

        .hero-content {
          flex: 1.2;
          max-width: 640px;
          text-align: left;
        }

        .badge {
          background: rgba(59, 130, 246, 0.1);
          border: 1px solid rgba(59, 130, 246, 0.2);
          padding: 8px 16px;
          border-radius: 100px;
          font-size: 13px;
          font-weight: 600;
          color: var(--accent-primary);
          display: inline-flex;
          align-items: center;
          gap: 8px;
          margin-bottom: 24px;
        }
        
        .badge-pulse {
          width: 8px; height: 8px;
          background: var(--accent-primary);
          border-radius: 50%;
          box-shadow: 0 0 10px var(--accent-primary);
          animation: pulse 2s infinite;
        }

        h1 {
          font-size: clamp(48px, 5.5vw, 64px);
          line-height: 1.1;
          margin-bottom: 24px;
          font-family: var(--font-heading);
          letter-spacing: -0.03em;
        }

        .hero-subtitle {
          font-size: 18px;
          color: var(--text-secondary);
          margin-bottom: 40px;
          line-height: 1.6;
        }

        .hero-actions {
          display: flex;
          gap: 16px;
        }

        .btn-primary {
          display: flex;
          align-items: center;
          padding: 16px 32px;
          font-size: 16px;
        }

        /* Visuals */
        .hero-visual {
          flex: 1;
          position: relative;
          display: flex;
          justify-content: flex-end;
        }

        .visual-glow {
          position: absolute;
          width: 100%;
          height: 100%;
          background: radial-gradient(circle, var(--accent-glow) 0%, transparent 60%);
          filter: blur(40px);
          z-index: -1;
        }

        .floating-image {
          width: 100%;
          max-width: 560px;
          border-radius: 24px;
          box-shadow: 0 30px 60px rgba(0,0,0,0.6);
          animation: float 6s ease-in-out infinite;
        }
        
        .glass-border {
          border: 1px solid rgba(255,255,255,0.1);
        }

        /* Features */
        .features {
          padding: 80px 0 120px;
          text-align: left;
        }

        .section-title {
          font-size: 32px;
          margin-bottom: 48px;
          font-family: var(--font-heading);
          letter-spacing: -0.02em;
        }

        .features-grid {
          display: grid;
          grid-template-columns: repeat(auto-fit, minmax(320px, 1fr));
          gap: 24px;
        }

        .feature-card {
          padding: 32px;
          background: rgba(255,255,255,0.02);
          border: 1px solid rgba(255,255,255,0.05);
          transition: transform 0.3s ease, background 0.3s ease, border-color 0.3s ease;
        }
        
        .feature-card:hover {
          transform: translateY(-5px);
          background: rgba(255,255,255,0.04);
          border-color: rgba(59, 130, 246, 0.3);
        }

        .feature-icon-wrapper {
          width: 48px;
          height: 48px;
          border-radius: 12px;
          background: rgba(255,255,255,0.05);
          display: flex;
          align-items: center;
          justify-content: center;
          margin-bottom: 24px;
          border: 1px solid rgba(255,255,255,0.1);
        }

        .feature-card h3 {
          margin-bottom: 12px;
          font-size: 20px;
          color: var(--text-primary);
        }

        .feature-card p {
          color: var(--text-secondary);
          line-height: 1.6;
          font-size: 15px;
        }

        @keyframes float {
          0%, 100% { transform: translateY(0); }
          50% { transform: translateY(-20px); }
        }
        
        @keyframes pulse {
          0% { opacity: 1; transform: scale(1); }
          50% { opacity: 0.5; transform: scale(0.8); }
          100% { opacity: 1; transform: scale(1); }
        }

        @media (max-width: 1024px) {
          .hero {
            flex-direction: column;
            text-align: center;
            padding-top: 40px;
          }
          .hero-content {
            text-align: center;
            max-width: 100%;
          }
          .hero-subtitle { margin: 0 auto 40px; }
          .hero-actions { justifyContent: center; }
          .hero-visual { justifyContent: center; margin-top: 40px; }
          .nav-links { display: none; } /* Hide links on mobile to save space */
        }
      `}</style>
    </div>
  );
};

export default Landing;