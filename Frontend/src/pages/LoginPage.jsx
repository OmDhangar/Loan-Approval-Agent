import React, { useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { useAuth } from '../context/AuthContext';

export default function LoginPage() {
  const [email, setEmail] = useState('evaluator@poonawalla.com');
  const [password, setPassword] = useState('hackathon2026');
  const [error, setError] = useState('');
  const [isLoggingIn, setIsLoggingIn] = useState(false);
  const { login } = useAuth();
  const navigate = useNavigate();

  const handleLogin = async (e) => {
    e.preventDefault();
    setIsLoggingIn(true);
    setError('');
    
    // Artificial delay to simulate network request and show loading state
    setTimeout(async () => {
      const result = await login(email, password);
      if (result.success) {
        navigate('/dashboard');
      } else {
        setError(result.error);
        setIsLoggingIn(false);
      }
    }, 800);
  };

  return (
    <div className="login-container animate-fade-in">
      <div className="login-glow"></div>
      <div className="glass-card login-card">
        <div className="brand-header">
          <div className="brand-dot"></div>
          <h2>Loan Wizard OS</h2>
        </div>
        <p className="subtitle">Agentic Platform Access</p>

        <form onSubmit={handleLogin} className="login-form">
          <div className="form-group">
            <label>Staff Email</label>
            <input 
              type="email" 
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              required
            />
          </div>
          
          <div className="form-group">
            <label>Access Key</label>
            <input 
              type="password" 
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              required
            />
          </div>

          {error && <div className="error-box">{error}</div>}

          <div className="evaluator-hint">
            💡 Evaluator credentials pre-filled for hackathon demo.
          </div>

          <button type="submit" className="btn-primary full-width" disabled={isLoggingIn}>
            {isLoggingIn ? 'Authenticating...' : 'Secure Login'}
          </button>
        </form>
      </div>

      <style jsx>{`
        .login-container {
          min-height: 100vh;
          display: flex;
          align-items: center;
          justify-content: center;
          background: var(--bg-primary);
          position: relative;
          overflow: hidden;
        }
        .login-glow {
          position: absolute;
          width: 600px;
          height: 600px;
          background: radial-gradient(circle, var(--accent-glow) 0%, transparent 60%);
          filter: blur(60px);
          opacity: 0.5;
          z-index: 0;
        }
        .login-card {
          width: 100%;
          max-width: 400px;
          padding: 40px;
          z-index: 1;
          border-top: 2px solid var(--accent-primary);
        }
        .brand-header {
          display: flex;
          align-items: center;
          justify-content: center;
          gap: 12px;
          margin-bottom: 8px;
        }
        .brand-dot {
          width: 12px;
          height: 12px;
          background: var(--accent-primary);
          border-radius: 50%;
          box-shadow: 0 0 15px var(--accent-glow);
        }
        h2 { font-size: 24px; margin: 0; }
        .subtitle {
          text-align: center;
          color: var(--text-secondary);
          margin-bottom: 32px;
          font-size: 14px;
        }
        .login-form {
          display: flex;
          flex-direction: column;
          gap: 20px;
        }
        .form-group {
          display: flex;
          flex-direction: column;
          gap: 8px;
          text-align: left;
        }
        .form-group label {
          font-size: 12px;
          font-weight: 700;
          color: var(--text-tertiary);
          text-transform: uppercase;
          letter-spacing: 0.05em;
        }
        .form-group input {
          background: rgba(0, 0, 0, 0.2);
          border: 1px solid var(--border);
          border-radius: 12px;
          padding: 14px 16px;
          color: var(--text-primary);
          font-family: inherit;
          transition: all 0.2s;
        }
        .form-group input:focus {
          border-color: var(--accent-primary);
          outline: none;
          box-shadow: 0 0 0 2px rgba(59, 130, 246, 0.2);
        }
        .evaluator-hint {
          font-size: 12px;
          color: var(--success);
          background: rgba(16, 185, 129, 0.1);
          padding: 12px;
          border-radius: 8px;
          border: 1px solid rgba(16, 185, 129, 0.2);
          text-align: left;
        }
        .error-box {
          background: rgba(239, 68, 68, 0.1);
          color: var(--error);
          padding: 12px;
          border-radius: 8px;
          font-size: 13px;
        }
        .full-width { width: 100%; margin-top: 8px; }
      `}</style>
    </div>
  );
}