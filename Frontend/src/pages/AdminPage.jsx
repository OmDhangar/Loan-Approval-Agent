/**
 * AdminPage.jsx
 * Internal dashboard for creating loan sessions and monitoring active calls.
 * Used by the campaign/operations team to generate session links for customers.
 */

import { useState, useEffect } from 'react'

const BRAND = {
  primary: '#0047AB',
  accent: '#00C9A7',
  surface: '#0A0F1E',
  surfaceAlt: '#111827',
  border: 'rgba(255,255,255,0.08)',
  text: '#F1F5F9',
  muted: '#94A3B8',
  danger: '#FF4757',
}

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
    <div style={s.page}>
      <header style={s.header}>
        <div style={s.brand}>
          <span style={s.dot} />
          <span style={s.brandName}>Loan Wizard</span>
          <span style={s.badge}>Admin</span>
        </div>
        <p style={s.subtitle}>Agentic AI Onboarding — Poonawalla Fincorp 2026</p>
      </header>

      <main style={s.main}>
        {/* Create session panel */}
        <section style={s.card}>
          <h2 style={s.cardTitle}>Create Loan Session</h2>
          <p style={s.cardSub}>Generate a secure VideoSDK room and send the link to a customer.</p>

          <div style={s.formGroup}>
            <label style={s.label}>Customer Phone</label>
            <input
              style={s.input}
              placeholder="+91 98765 43210"
              value={phone}
              onChange={e => setPhone(e.target.value)}
              onKeyDown={e => e.key === 'Enter' && handleCreate()}
            />
          </div>

          <div style={s.formGroup}>
            <label style={s.label}>Campaign ID</label>
            <input
              style={s.input}
              value={campaign}
              onChange={e => setCampaign(e.target.value)}
            />
          </div>

          {error && <p style={s.error}>{error}</p>}

          <button style={s.btn} onClick={handleCreate} disabled={creating}>
            {creating ? 'Creating…' : '+ Create Session & Get Link'}
          </button>
        </section>

        {/* Result panel */}
        {result && (
          <section style={{ ...s.card, borderColor: `${BRAND.accent}40` }}>
            <h2 style={{ ...s.cardTitle, color: BRAND.accent }}>✅ Session Created</h2>
            <div style={s.resultGrid}>
              <ResultRow label="Call ID" value={result.call_id} mono />
              <ResultRow label="Room ID" value={result.videosdk_room_id} mono />
              <ResultRow label="Expires" value={result.expires_at} />
              <ResultRow label="Join URL" value={result.join_url} link />
            </div>
            <button
              style={s.copyBtn}
              onClick={() => navigator.clipboard.writeText(result.join_url)}
            >
              📋 Copy Join Link
            </button>
          </section>
        )}

        {/* Active sessions */}
        <section style={s.card}>
          <h2 style={s.cardTitle}>
            Active Sessions
            <span style={s.count}>{sessions.length}</span>
          </h2>
          {sessions.length === 0 ? (
            <p style={s.empty}>No active sessions</p>
          ) : (
            <table style={s.table}>
              <thead>
                <tr>
                  {['Call ID', 'Room ID', 'Stage', 'Actions'].map(h => (
                    <th key={h} style={s.th}>{h}</th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {sessions.map(sess => (
                  <tr key={sess.call_id} style={s.tr}>
                    <td style={{ ...s.td, ...s.mono }}>{sess.call_id?.slice(0, 8)}…</td>
                    <td style={{ ...s.td, ...s.mono }}>{sess.room_id?.slice(-8)}</td>
                    <td style={s.td}>
                      <span style={{ ...s.stagePill, background: stageColor(sess.stage) }}>
                        {sess.stage || 'INIT'}
                      </span>
                    </td>
                    <td style={s.td}>
                      <button
                        style={s.smallBtn}
                        onClick={() => window.open(`/api/v1/session/${sess.call_id}`, '_blank')}
                      >
                        View State
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </section>

      </main>
    </div>
  )
}

function ResultRow({ label, value, mono, link }) {
  return (
    <div style={{
      display: 'flex', gap: 12, alignItems: 'flex-start', padding: '6px 0',
      borderBottom: `1px solid rgba(255,255,255,0.06)`
    }}>
      <span style={{ color: '#94A3B8', minWidth: 80, fontSize: 13 }}>{label}</span>
      {link
        ? <a href={value} target="_blank" rel="noreferrer"
          style={{ color: '#00C9A7', fontSize: 13, wordBreak: 'break-all' }}>{value}</a>
        : <span style={{
          fontSize: 13, fontFamily: mono ? 'monospace' : 'inherit',
          wordBreak: 'break-all'
        }}>{value}</span>
      }
    </div>
  )
}

function stageColor(stage) {
  const map = {
    INIT: '#374151', GREETING_CONSENT: '#1D4ED8', IDENTITY_KYC: '#6D28D9',
    EMPLOYMENT_INCOME: '#065F46', LOAN_PURPOSE: '#92400E',
    RISK_ASSESSMENT: '#7C3AED', OFFER_ACCEPTANCE: '#0F766E',
    COMPLETED: '#166534', ESCALATED: '#991B1B',
  }
  return map[stage] || '#374151'
}

const ARCH_LAYERS = [
  { icon: '🎥', name: 'VideoSDK', desc: 'Live video room, E2E recording, real-time transcription' },
  { icon: '🧠', name: 'LangGraph DAG', desc: 'Moderator state machine — 6 stages, conditional routing' },
  { icon: '🤖', name: '6 Agents', desc: 'Conversation · Verification · Vision · Risk · Offer · Compliance' },
  { icon: '⚡', name: 'Redis', desc: 'Shared State — <1ms reads, TTL cleanup, SSE pub/sub' },
  { icon: '📬', name: 'RabbitMQ', desc: 'On-demand agent activation queue — Supervisor-Worker pattern' },
  { icon: '🗄️', name: 'PostgreSQL', desc: 'Append-only audit log — RBI WORM compliance' },
  { icon: '🔊', name: 'Whisper STT', desc: 'local large-v3 — Hinglish support, <300ms latency' },
  { icon: '👁️', name: 'YOLOv8', desc: 'Face detection, liveness check, age estimation' },
]

const s = {
  page: {
    minHeight: '100vh', background: BRAND.surface, color: BRAND.text,
    fontFamily: "'DM Sans', sans-serif"
  },
  header: {
    padding: '24px 32px', borderBottom: `1px solid ${BRAND.border}`,
    background: 'rgba(10,15,30,0.95)'
  },
  brand: { display: 'flex', alignItems: 'center', gap: 10, marginBottom: 6 },
  dot: {
    width: 10, height: 10, borderRadius: '50%', background: BRAND.accent,
    boxShadow: `0 0 12px ${BRAND.accent}`
  },
  brandName: { fontWeight: 800, fontSize: 20, letterSpacing: '-0.03em' },
  badge: {
    fontSize: 11, fontWeight: 700, background: `${BRAND.primary}40`,
    color: '#93C5FD', padding: '3px 10px', borderRadius: 20,
    border: `1px solid ${BRAND.primary}60`
  },
  subtitle: { fontSize: 13, color: BRAND.muted },
  main: {
    padding: '28px 32px', display: 'flex', flexDirection: 'column', gap: 24,
    maxWidth: 900, margin: '0 auto'
  },
  card: {
    background: BRAND.surfaceAlt, borderRadius: 16, padding: 24,
    border: `1px solid ${BRAND.border}`
  },
  cardTitle: {
    fontSize: 17, fontWeight: 700, marginBottom: 6,
    display: 'flex', alignItems: 'center', gap: 10
  },
  cardSub: { fontSize: 13, color: BRAND.muted, marginBottom: 20 },
  count: {
    fontSize: 12, fontWeight: 700, background: BRAND.primary,
    padding: '2px 10px', borderRadius: 20
  },
  formGroup: { marginBottom: 16 },
  label: {
    display: 'block', fontSize: 12, fontWeight: 600,
    color: BRAND.muted, marginBottom: 6, textTransform: 'uppercase',
    letterSpacing: '0.06em'
  },
  input: {
    width: '100%', padding: '10px 14px', borderRadius: 10,
    background: 'rgba(255,255,255,0.05)', border: `1px solid ${BRAND.border}`,
    color: BRAND.text, fontSize: 14, outline: 'none',
    fontFamily: 'inherit'
  },
  error: {
    color: BRAND.danger, fontSize: 13, marginBottom: 12,
    background: 'rgba(255,71,87,0.1)', padding: '8px 12px', borderRadius: 8
  },
  btn: {
    padding: '12px 28px', borderRadius: 10,
    background: `linear-gradient(135deg, ${BRAND.primary}, ${BRAND.accent})`,
    border: 'none', color: '#fff', fontWeight: 700, fontSize: 14,
    cursor: 'pointer', width: '100%'
  },
  copyBtn: {
    padding: '10px 20px', borderRadius: 10, marginTop: 16,
    background: `${BRAND.accent}18`, border: `1px solid ${BRAND.accent}40`,
    color: BRAND.accent, fontWeight: 600, fontSize: 13, cursor: 'pointer'
  },
  resultGrid: { display: 'flex', flexDirection: 'column', gap: 2, marginBottom: 4 },
  table: { width: '100%', borderCollapse: 'collapse', fontSize: 13 },
  th: {
    textAlign: 'left', padding: '8px 12px', color: BRAND.muted,
    fontSize: 11, textTransform: 'uppercase', letterSpacing: '0.06em',
    borderBottom: `1px solid ${BRAND.border}`
  },
  tr: { borderBottom: `1px solid ${BRAND.border}` },
  td: { padding: '10px 12px' },
  mono: { fontFamily: 'monospace', fontSize: 12 },
  stagePill: {
    fontSize: 11, fontWeight: 700, padding: '3px 10px',
    borderRadius: 20, color: '#fff', letterSpacing: '0.04em'
  },
  smallBtn: {
    fontSize: 11, padding: '4px 12px', borderRadius: 6,
    background: 'rgba(255,255,255,0.07)', border: `1px solid ${BRAND.border}`,
    color: BRAND.muted, cursor: 'pointer'
  },
  empty: { color: BRAND.muted, fontSize: 13, textAlign: 'center', padding: '20px 0' },
  archGrid: { display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 12 },
  archItem: {
    display: 'flex', alignItems: 'flex-start', gap: 12, padding: '10px',
    background: 'rgba(255,255,255,0.03)', borderRadius: 10
  },
  archIcon: { fontSize: 22, flexShrink: 0 },
  archName: { fontWeight: 600, fontSize: 13, marginBottom: 3 },
  archDesc: { fontSize: 12, color: BRAND.muted, lineHeight: 1.5 },
}