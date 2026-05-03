import React, { useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { useAuth } from '../context/AuthContext';

const Dashboard = () => {
  const navigate = useNavigate();
  const { logout, user } = useAuth(); // Pulling from the AuthContext we created
  
  const [activeAgents] = useState([
    { name: 'Identity Verifier', status: 'Online', icon: '👤' },
    { name: 'Risk Evaluator', status: 'Online', icon: '📊' },
    { name: 'Offer Moderator', status: 'Online', icon: '🤝' },
    { name: 'RBI Compliance', status: 'Enforcing', icon: '🛡️' }
  ]);

  const handleLogout = () => {
    if (logout) logout();
    navigate('/');
  };

  return (
    <div className="dashboard-container animate-fade-in">
      <header className="dashboard-header">
        <div>
          <h1>Welcome back, {user?.name || 'Evaluator'}</h1>
          <p className="text-secondary">Loan Wizard OS / Command Center</p>
        </div>
        
        {/* Added Header Actions for Navigation & Logout */}
        <div className="header-actions">
          <button className="btn-outline" onClick={() => navigate('/admin')}>
            ⚙️ Session Manager
          </button>
          <button className="btn-outline danger" onClick={handleLogout}>
            Logout
          </button>
          <div className="user-profile">
            <div className="avatar">EV</div>
          </div>
        </div>
      </header>

      <div className="dashboard-grid">
        {/* Main Status Card -> Transformed into Session Generator Prompt */}
        <div className="glass-card status-card">
          <div className="card-header">
            <h3>New Customer Application</h3>
            <span className="status-badge active">System Ready</span>
          </div>
          <div className="progress-section">
            <p className="text-secondary" style={{ marginBottom: '24px', lineHeight: '1.6' }}>
              Initiate a secure Video KYC session to generate an instant loan offer. The magic link will be generated for the customer to join from their device.
            </p>
          </div>
          <button className="btn-primary full-width" onClick={() => navigate('/admin')}>
            + Generate New Session Link
          </button>
        </div>

        {/* AI Agent Status Card */}
        <div className="glass-card agent-card">
          <h3>Active AI Agents</h3>
          <div className="agent-list">
            {activeAgents.map((agent, i) => (
              <div key={i} className="agent-item">
                <div className="agent-info">
                  <span className="agent-icon">{agent.icon}</span>
                  <div>
                    <p className="agent-name">{agent.name}</p>
                    <p className="agent-status">{agent.status}</p>
                  </div>
                </div>
                {/* Changed idle dot to active success dot */}
                <div className={`status-dot ${agent.status === 'Idle' ? 'idle' : 'active'}`}></div>
              </div>
            ))}
          </div>
        </div>

        {/* Insights Card */}
        <div className="glass-card insight-card">
          <h3>System Metrics</h3>
          <div className="insight-content">
            <div className="stat-item">
              <span className="stat-label">Avg. Decision Time</span>
              <span className="stat-value">~14s</span>
            </div>
            <div className="stat-item">
              <span className="stat-label">Automation Rate</span>
              <span className="stat-value">98.2%</span>
            </div>
          </div>
          <div className="mini-chart">
            <div className="chart-bar" style={{ height: '40%' }}></div>
            <div className="chart-bar" style={{ height: '70%' }}></div>
            <div className="chart-bar" style={{ height: '50%' }}></div>
            <div className="chart-bar" style={{ height: '90%' }}></div>
            <div className="chart-bar" style={{ height: '60%' }}></div>
          </div>
        </div>
      </div>

      <style jsx>{`
        .dashboard-container {
          padding: 40px;
          max-width: 1200px;
          margin: 0 auto;
          width: 100%;
        }

        .dashboard-header {
          display: flex;
          justify-content: space-between;
          align-items: center;
          margin-bottom: 40px;
          text-align: left;
        }

        /* New Header Actions Layout */
        .header-actions {
          display: flex;
          align-items: center;
          gap: 16px;
        }

        .avatar {
          width: 48px;
          height: 48px;
          background: var(--accent-primary);
          border-radius: 50%;
          display: flex;
          align-items: center;
          justify-content: center;
          font-weight: 700;
          box-shadow: 0 0 15px var(--accent-glow);
        }

        .dashboard-grid {
          display: grid;
          grid-template-columns: 2fr 1fr;
          grid-template-rows: auto auto;
          gap: 24px;
        }

        .status-card {
          grid-column: 1 / 2;
          padding: 32px;
          text-align: left;
        }

        .card-header {
          display: flex;
          justify-content: space-between;
          align-items: center;
          margin-bottom: 32px;
        }

        .status-badge {
          padding: 4px 12px;
          border-radius: 100px;
          font-size: 12px;
          font-weight: 600;
          text-transform: uppercase;
        }

        .status-badge.draft { background: rgba(255, 255, 255, 0.1); color: var(--text-secondary); }
        
        /* New Active Badge */
        .status-badge.active { 
          background: rgba(16, 185, 129, 0.1); 
          color: var(--success); 
        }

        .progress-section {
          margin-bottom: 32px;
        }

        .agent-card {
          grid-column: 2 / 3;
          grid-row: 1 / 3;
          padding: 24px;
          text-align: left;
        }

        .agent-list {
          margin-top: 24px;
          display: flex;
          flex-direction: column;
          gap: 16px;
        }

        .agent-item {
          display: flex;
          justify-content: space-between;
          align-items: center;
          padding: 12px;
          background: rgba(255, 255, 255, 0.02);
          border-radius: 12px;
        }

        .agent-info {
          display: flex;
          gap: 12px;
          align-items: center;
        }

        .agent-icon { font-size: 20px; }
        .agent-name { font-weight: 600; font-size: 14px; }
        .agent-status { font-size: 12px; color: var(--text-tertiary); }

        .status-dot {
          width: 8px;
          height: 8px;
          border-radius: 50%;
          background: var(--text-tertiary);
        }
        .status-dot.idle { background: var(--text-tertiary); }
        .status-dot.active { background: var(--success); box-shadow: 0 0 8px var(--success); }

        .insight-card {
          grid-column: 1 / 2;
          padding: 24px;
          text-align: left;
          display: flex;
          flex-direction: column;
          gap: 16px;
        }

        .insight-content {
          display: flex;
          gap: 32px;
        }

        .stat-item {
          display: flex;
          flex-direction: column;
        }

        .stat-label { font-size: 12px; color: var(--text-tertiary); }
        .stat-value { font-size: 24px; font-weight: 700; color: var(--accent-secondary); }

        .mini-chart {
          height: 60px;
          display: flex;
          align-items: flex-end;
          gap: 8px;
          margin-top: auto;
        }

        .chart-bar {
          flex: 1;
          background: var(--border);
          border-radius: 4px 4px 0 0;
          transition: height 1s ease;
        }

        .full-width { width: 100%; margin-top: 12px; }

        /* Danger Button Styling */
        .btn-outline.danger {
          border-color: rgba(239, 68, 68, 0.3);
          color: var(--error);
          background: transparent;
        }
        .btn-outline.danger:hover {
          background: rgba(239, 68, 68, 0.1);
          border-color: rgba(239, 68, 68, 0.6);
        }

        @media (max-width: 900px) {
          .dashboard-grid { grid-template-columns: 1fr; }
          .agent-card { grid-column: 1 / 2; grid-row: auto; }
          .header-actions { flex-direction: column; align-items: flex-start; }
          .dashboard-header { flex-direction: column; gap: 20px; }
        }
      `}</style>
    </div>
  );
};

export default Dashboard;