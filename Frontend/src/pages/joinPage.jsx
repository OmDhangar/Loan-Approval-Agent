/**
 * JoinPage.jsx
 * Entry page when customer clicks the SMS/WhatsApp campaign link.
 * URL: /join/:sessionToken
 *
 * Responsibilities:
 * 1. Parse sessionToken from URL
 * 2. Call useVideoSDKSession to bootstrap credentials
 * 3. Render VideoCallScreen once ready
 */

import { useParams } from "react-router-dom";
import { useVideoSDKSession } from "../hooks/videoSDKSession";
import VideoCallScreen from "../components/videoCallScreen";

const BRAND = {
  primary:   "#0047AB",
  accent:    "#00C9A7",
  surface:   "#0A0F1E",
  text:      "#F1F5F9",
  textMuted: "#94A3B8",
};

export default function JoinPage() {
  const { sessionToken } = useParams();
  const { loading, error, callId, roomId, videoSdkToken } = useVideoSDKSession(sessionToken);

  if (loading) {
    return (
      <div style={styles.center}>
        <div style={styles.spinner} />
        <p style={styles.loadingText}>Preparing your secure call…</p>
        <p style={styles.subText}>This takes just a moment</p>
      </div>
    );
  }

  if (error) {
    return (
      <div style={styles.center}>
        <div style={styles.errorIcon}>⚠️</div>
        <h2 style={styles.errorTitle}>Unable to join</h2>
        <p style={styles.errorText}>{error}</p>
        <p style={styles.errorHint}>
          Your link may have expired. Please request a new one from Poonawalla Fincorp.
        </p>
      </div>
    );
  }

  return (
    <VideoCallScreen
      callId={callId}
      roomId={roomId}
      videoSdkToken={videoSdkToken}
    />
  );
}

const styles = {
  center: {
    minHeight: "100vh",
    display: "flex", flexDirection: "column",
    alignItems: "center", justifyContent: "center",
    background: BRAND.surface, color: BRAND.text,
    fontFamily: "'DM Sans', sans-serif",
    textAlign: "center", padding: 24,
  },
  spinner: {
    width: 48, height: 48,
    border: `3px solid rgba(255,255,255,0.1)`,
    borderTop: `3px solid ${BRAND.accent}`,
    borderRadius: "50%",
    animation: "spin 0.8s linear infinite",
    marginBottom: 24,
  },
  loadingText: { fontSize: 18, fontWeight: 600, margin: "0 0 8px" },
  subText: { fontSize: 14, color: BRAND.textMuted, margin: 0 },
  errorIcon: { fontSize: 48, marginBottom: 16 },
  errorTitle: { fontSize: 22, fontWeight: 700, margin: "0 0 12px" },
  errorText: {
    fontSize: 15, color: BRAND.textMuted,
    background: "rgba(255,71,87,0.12)",
    border: "1px solid rgba(255,71,87,0.3)",
    borderRadius: 10, padding: "10px 20px",
    maxWidth: 360,
  },
  errorHint: { fontSize: 13, color: BRAND.textMuted, marginTop: 20, maxWidth: 320, lineHeight: 1.6 },
};
