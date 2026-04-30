export default function NotFound() {
  return (
    <div style={{ minHeight:'100vh', display:'flex', flexDirection:'column',
                  alignItems:'center', justifyContent:'center',
                  background:'#0A0F1E', color:'#F1F5F9',
                  fontFamily:"'DM Sans', sans-serif", textAlign:'center' }}>
      <div style={{ fontSize: 72, marginBottom: 16 }}>🏦</div>
      <h1 style={{ fontSize: 28, fontWeight: 800, marginBottom: 8 }}>Page Not Found</h1>
      <p style={{ color: '#94A3B8', fontSize: 15 }}>
        Your session link may have expired. Please request a new one.
      </p>
    </div>
  )
}