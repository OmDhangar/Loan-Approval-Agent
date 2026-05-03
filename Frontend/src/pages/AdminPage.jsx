import { useState, useEffect } from 'react';
import { useNavigate } from 'react-router-dom';


export default function AdminPage() {
  const [phone, setPhone] = useState('')
  const [campaign, setCampaign] = useState('HACKATHON-2026')
  const [creating, setCreating] = useState(false)
  const [result, setResult] = useState(null)
  const [sessions, setSessions] = useState([])
  const [error, setError] = useState('')

  // Poll active sessions every 5s
  useEffect(() => {
    const load = () =>
      fetch('/api/v1/session/active')
        .then(r => r.ok ? r.json() : [])
        .then(d => setSessions(Array.isArray(d) ? d : []))
        .catch(() => { })
    load()
    const t = setInterval(load, 5000)
    return () => clearInterval(t)
  }, [])

  const handleCreate = async () => {
    if (!phone.trim()) { setError('Phone number is required'); return }
    setError('')
    setCreating(true)
    setResult(null)
    try {
      const res = await fetch('/api/v1/session/create', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ customer_phone: phone.trim(), campaign_id: campaign }),
      })
      const data = await res.json()
      if (!res.ok) throw new Error(data.detail || 'Failed to create session')
      setResult(data)
      setPhone('')
    } catch (e) {
      setError(e.message)
    } finally {
      setCreating(false)
    }
  }

  return (
    <div className="admin-container animate-fade-in">
     <header className="admin-header" style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start' }}>
        <div>
          <div className="brand">
            <div className="brand-dot" />
            <h1>Loan Wizard</h1>
            <span className="badge">Session Manager</span>
          </div>
          <p className="subtitle">Generate & Monitor Customer Sessions</p>
        </div>
        <button className="btn-outline" onClick={() => navigate('/dashboard')}>
          ← Back to Dashboard
        </button>
      </header>

      <main className="admin-main">
        {/* Create session panel */}
        <section className="glass-card admin-card">
          <div className="card-header">
            <div>
              <h2>Create New Session</h2>
              <p className="text-secondary">Initiate a secure onboarding journey for a customer</p>
            </div>
            <div className="card-icon">⚡</div>
          </div>

          <div className="admin-form">
            <div className="form-group">
              <label>Customer Phone</label>
              <input
                placeholder="+91 98765 43210"
                value={phone}
                onChange={e => setPhone(e.target.value)}
                onKeyDown={e => e.key === 'Enter' && handleCreate()}
              />
            </div>

            <div className="form-group">
              <label>Campaign ID</label>
              <input
                value={campaign}
                onChange={e => setCampaign(e.target.value)}
              />
            </div>

            {error && <div className="error-box">{error}</div>}

            <button className="btn-primary" onClick={handleCreate} disabled={creating}>
              {creating ? 'Processing...' : 'Generate Secure Session'}
            </button>
          </div>
        </section>

        {/* Result panel */}
        {result && (
          <section className="glass-card result-card animate-fade-in">
            <h2 className="accent-gradient">✅ Session Ready</h2>
            <div className="result-details">
              <div className="result-row">
                <span>Join URL</span>
                <a href={result.join_url} target="_blank" rel="noreferrer">{result.join_url}</a>
              </div>
              <div className="result-row">
                <span>Room ID</span>
                <code>{result.videosdk_room_id}</code>
              </div>
            </div>
            <button
              className="btn-outline full-width"
              onClick={() => {
                navigator.clipboard.writeText(result.join_url);
                alert("Link copied to clipboard!");
              }}
            >
              📋 Copy Secure Link
            </button>
          </section>
        )}

        {/* Active sessions */}
        <section className="glass-card active-sessions-card">
          <div className="card-header">
            <h2>Live Sessions</h2>
            <div className="live-count">{sessions.length} Active</div>
          </div>
          
          <div className="table-wrapper">
            {sessions.length === 0 ? (
              <p className="empty-state">No sessions currently active</p>
            ) : (
              <table className="admin-table">
                <thead>
                  <tr>
                    <th>Call ID</th>
                    <th>Room ID</th>
                    <th>Stage</th>
                    <th>Monitor</th>
                  </tr>
                </thead>
                <tbody>
                  {sessions.map(sess => (
                    <tr key={sess.call_id}>
                      <td><code className="id-code">{sess.call_id?.slice(0, 8)}...</code></td>
                      <td><code className="id-code">{sess.room_id?.slice(-8)}</code></td>
                      <td>
                        <span className={`stage-pill ${sess.stage?.toLowerCase() || 'init'}`}>
                          {sess.stage || 'INIT'}
                        </span>
                      </td>
                      <td>
                        <button
                          className="btn-icon-only"
                          onClick={() => window.open(`/api/v1/session/${sess.call_id}`, '_blank')}
                          title="View Internal State"
                        >
                          👁️
                        </button>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
          </div>
        </section>
      </main>

      <style jsx>{`
        .admin-container {
          padding: 40px;
          max-width: 1000px;
          margin: 0 auto;
          width: 100%;
        }

        .admin-header {
          text-align: left;
          margin-bottom: 40px;
        }

        .brand {
          display: flex;
          align-items: center;
          gap: 12px;
          margin-bottom: 8px;
        }

        .brand h1 { margin: 0; font-size: 28px; }

        .brand-dot {
          width: 10px;
          height: 10px;
          background: var(--accent-primary);
          border-radius: 50%;
          box-shadow: 0 0 15px var(--accent-glow);
        }

        .badge {
          background: var(--bg-tertiary);
          color: var(--accent-secondary);
          padding: 4px 12px;
          border-radius: 100px;
          font-size: 12px;
          font-weight: 700;
          text-transform: uppercase;
          border: 1px solid var(--border);
        }

        .subtitle {
          color: var(--text-secondary);
          font-size: 14px;
        }

        .admin-main {
          display: flex;
          flex-direction: column;
          gap: 32px;
        }

        .admin-card {
          padding: 32px;
          text-align: left;
        }

        .card-header {
          display: flex;
          justify-content: space-between;
          align-items: flex-start;
          margin-bottom: 32px;
        }

        .card-icon {
          font-size: 32px;
          background: var(--surface-hover);
          width: 56px;
          height: 56px;
          display: flex;
          align-items: center;
          justify-content: center;
          border-radius: 16px;
          border: 1px solid var(--border);
        }

        .admin-form {
          display: grid;
          gap: 24px;
        }

        .form-group {
          display: flex;
          flex-direction: column;
          gap: 8px;
        }

        .form-group label {
          font-size: 12px;
          font-weight: 700;
          color: var(--text-tertiary);
          text-transform: uppercase;
          letter-spacing: 0.05em;
        }

        .form-group input {
          background: var(--bg-primary);
          border: 1px solid var(--border);
          border-radius: 12px;
          padding: 12px 16px;
          color: var(--text-primary);
          font-family: inherit;
          transition: border-color 0.2s;
        }

        .form-group input:focus {
          border-color: var(--accent-primary);
          outline: none;
        }

        .error-box {
          background: rgba(239, 68, 68, 0.1);
          color: var(--error);
          padding: 12px;
          border-radius: 8px;
          font-size: 14px;
        }

        .result-card {
          padding: 32px;
          border-color: var(--accent-primary);
          text-align: left;
        }

        .result-details {
          margin: 24px 0;
          display: flex;
          flex-direction: column;
          gap: 16px;
        }

        .result-row {
          display: flex;
          flex-direction: column;
          gap: 4px;
        }

        .result-row span {
          font-size: 12px;
          color: var(--text-tertiary);
        }

        .result-row a {
          color: var(--accent-secondary);
          text-decoration: none;
          word-break: break-all;
          font-size: 14px;
        }

        .active-sessions-card {
          padding: 0;
          overflow: hidden;
        }

        .active-sessions-card .card-header {
          padding: 32px;
          margin-bottom: 0;
          border-bottom: 1px solid var(--border);
        }

        .live-count {
          background: rgba(16, 185, 129, 0.1);
          color: var(--success);
          padding: 4px 12px;
          border-radius: 100px;
          font-size: 12px;
          font-weight: 700;
        }

        .table-wrapper {
          overflow-x: auto;
        }

        .admin-table {
          width: 100%;
          border-collapse: collapse;
          text-align: left;
        }

        .admin-table th {
          padding: 16px 32px;
          font-size: 11px;
          text-transform: uppercase;
          letter-spacing: 0.1em;
          color: var(--text-tertiary);
          border-bottom: 1px solid var(--border);
        }

        .admin-table td {
          padding: 16px 32px;
          border-bottom: 1px solid var(--border);
        }

        .id-code {
          font-size: 12px;
          color: var(--text-secondary);
        }

        .stage-pill {
          font-size: 10px;
          font-weight: 800;
          padding: 4px 10px;
          border-radius: 100px;
          text-transform: uppercase;
          background: var(--bg-tertiary);
        }

        .stage-pill.init { color: var(--text-tertiary); }
        .stage-pill.completed { background: rgba(16, 185, 129, 0.1); color: var(--success); }
        .stage-pill.escalated { background: rgba(239, 68, 68, 0.1); color: var(--error); }

        .btn-icon-only {
          background: var(--surface-hover);
          border: 1px solid var(--border);
          width: 32px;
          height: 32px;
          border-radius: 8px;
          cursor: pointer;
          transition: all 0.2s;
        }

        .btn-icon-only:hover {
          background: var(--accent-primary);
          color: white;
        }

        .empty-state {
          padding: 40px;
          color: var(--text-tertiary);
          text-align: center;
        }

        .full-width { width: 100%; }
      `}</style>
    </div>
  )
}
