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

export default function JoinPage() {
  const { sessionToken } = useParams();
  const { loading, error, callId, roomId, videoSdkToken } = useVideoSDKSession(sessionToken);

  if (loading) {
    return (
      <div className="center-screen animate-fade-in">
        <div className="loader-container">
          <div className="loader"></div>
          <div className="loader-glow"></div>
        </div>
        <h2 className="loading-title">Securing Connection</h2>
        <p className="loading-subtitle">Initializing our AI Agent for your session...</p>
        
        <style jsx="true">{`
          .center-screen {
            min-height: 100vh;
            display: flex;
            flex-direction: column;
            align-items: center;
            justify-content: center;
            background: var(--bg-primary);
            text-align: center;
            padding: 40px;
          }
          .loader-container {
            position: relative;
            margin-bottom: 32px;
          }
          .loader {
            width: 64px;
            height: 64px;
            border: 3px solid var(--surface);
            border-top: 3px solid var(--accent-primary);
            border-radius: 50%;
            animation: spin 1s linear infinite;
          }
          .loader-glow {
            position: absolute;
            top: 0; left: 0; right: 0; bottom: 0;
            background: var(--accent-glow);
            filter: blur(20px);
            opacity: 0.3;
            border-radius: 50%;
          }
          .loading-title {
            font-size: 24px;
            margin-bottom: 8px;
            background: linear-gradient(135deg, #fff 0%, #94a3b8 100%);
            -webkit-background-clip: text;
            -webkit-text-fill-color: transparent;
          }
          .loading-subtitle {
            color: var(--text-tertiary);
            font-size: 16px;
          }
          @keyframes spin {
            to { transform: rotate(360deg); }
          }
        `}</style>
      </div>
    );
  }

  if (error) {
    return (
      <div className="center-screen animate-fade-in">
        <div className="glass-card error-card">
          <div className="error-icon">⚠️</div>
          <h2>Session Unavailable</h2>
          <p className="error-msg">{error}</p>
          <div className="divider"></div>
          <p className="error-hint">
            The link may have expired or is invalid. Please contact support if you believe this is an error.
          </p>
          <button className="btn-outline full-width" onClick={() => window.location.href='/'}>
            Back to Home
          </button>
        </div>

        <style jsx="true">{`
          .center-screen {
            min-height: 100vh;
            display: flex;
            align-items: center;
            justify-content: center;
            background: var(--bg-primary);
            padding: 24px;
          }
          .error-card {
            max-width: 440px;
            padding: 40px;
            text-align: center;
          }
          .error-icon {
            font-size: 48px;
            margin-bottom: 24px;
          }
          h2 { margin-bottom: 16px; }
          .error-msg {
            color: var(--error);
            background: rgba(239, 68, 68, 0.1);
            padding: 12px;
            border-radius: 8px;
            font-size: 14px;
            margin-bottom: 24px;
          }
          .divider {
            height: 1px;
            background: var(--border);
            margin: 24px 0;
          }
          .error-hint {
            color: var(--text-tertiary);
            font-size: 14px;
            margin-bottom: 32px;
            line-height: 1.6;
          }
          .full-width { width: 100%; }
        `}</style>
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
