/**
 * VideoCallScreen.jsx
 * Main loan onboarding call screen with VideoSDK integration.
 * Renders: video grid, AI stage indicator, live captions, offer overlay.
 */

import { useState, useEffect, useRef, useCallback, useMemo } from "react";
import {
  MeetingProvider,
  useMeeting,
  useParticipant,
  MeetingConsumer,
} from "@videosdk.live/react-sdk";

// ── Design tokens ─────────────────────────────────────────────────────────────
const BRAND = {
  primary:    "#0047AB",   // Poonawalla blue
  accent:     "#00C9A7",   // Teal
  danger:     "#FF4757",
  surface:    "#0A0F1E",
  surfaceAlt: "#111827",
  border:     "rgba(255,255,255,0.08)",
  text:       "#F1F5F9",
  textMuted:  "#94A3B8",
};

const STAGE_META = {
  INIT:                 { label: "Connecting…",          icon: "⏳", pct: 0  },
  GREETING_CONSENT:     { label: "Consent",               icon: "🤝", pct: 8  },
  OVD_DOCUMENT_CAPTURE: { label: "Document Capture",      icon: "📄", pct: 18 },
  LIVENESS_CHALLENGE:   { label: "Liveness Check",        icon: "👁️", pct: 28 },
  AADHAAR_VERIFICATION: { label: "Aadhaar Verification",  icon: "🔐", pct: 38 },
  IDENTITY_KYC:         { label: "Identity Verification", icon: "🪪", pct: 48 },
  EMPLOYMENT_INCOME:    { label: "Income Details",        icon: "💼", pct: 60 },
  LOAN_PURPOSE:         { label: "Loan Purpose",          icon: "🎯", pct: 72 },
  RISK_ASSESSMENT:      { label: "Assessment",            icon: "📊", pct: 84 },
  OFFER_ACCEPTANCE:     { label: "Your Offer",            icon: "🎁", pct: 95 },
  COMPLETED:            { label: "Complete!",             icon: "✅", pct: 100 },
  ESCALATED:            { label: "Human Agent",           icon: "👤", pct: 84 },
};


// ══════════════════════════════════════════════════════════════════════════════
// Root: wraps MeetingProvider from VideoSDK
// ══════════════════════════════════════════════════════════════════════════════
export default function VideoCallScreen({ callId, roomId, videoSdkToken }) {
  return (
    <MeetingProvider
      config={{
        meetingId:     roomId,
        micEnabled:    true,
        webcamEnabled: true,
        name:          "Loan Applicant",
        multiStream:   false,           // single high-quality stream
        mode:          "CONFERENCE",
      }}
      token={videoSdkToken}
      joinWithoutUserInteraction={false}
    >
      <MeetingConsumer>
        {() => <CallUI callId={callId} />}
      </MeetingConsumer>
    </MeetingProvider>
  );
}


// ══════════════════════════════════════════════════════════════════════════════
// Inner UI — has access to VideoSDK meeting hooks
// ══════════════════════════════════════════════════════════════════════════════
function CallUI({ callId }) {
  const {
    join, leave,
    toggleMic, toggleWebcam,
    localMicOn, localWebcamOn,
    participants,
    localParticipant,
    meetingId,
  } = useMeeting({
    onMeetingJoined:  () => console.log("Meeting joined"),
    onMeetingLeft:    () => console.log("Meeting left"),
    onError:          (err) => console.error("Meeting error:", err),
  });

  // ── Local state ───────────────────────────────────────────────────────────
  const [joined,        setJoined]       = useState(false);
  const [stage,         setStage]        = useState("INIT");
  const [caption,       setCaption]      = useState("");
  const [offer,         setOffer]        = useState(null);
  const [escalated,     setEscalated]    = useState(false);
  const [networkScore,  setNetworkScore] = useState(5);
  const [audioFirst,    setAudioFirst]   = useState(false);
  const [error,         setError]        = useState(null);
  const [agentSpeech,   setAgentSpeech]  = useState("");
  const [debugSpeech,   setDebugSpeech]  = useState("");
  // Local mic/cam state tracking (syncs with VideoSDK but prevents stale closures)
  const [micActive,     setMicActive]    = useState(true);
  const [camActive,     setCamActive]    = useState(true);
  const toggleCooldown                   = useRef(false);
  const evtSourceRef                     = useRef(null);
  const audioPlayerRef                   = useRef(null);
  const joinedRef                        = useRef(false);
  const endSentRef                       = useRef(false);
  // Client-side recording refs (replaces VideoSDK cloud recording)
  const mediaRecorderRef                 = useRef(null);
  const recordedChunksRef                = useRef([]);
  // Canvas snapshot refs (for vision agent)
  const snapshotIntervalRef              = useRef(null);
  const localVideoRef                    = useRef(null);
  // Speech recognition refs (replaces paid VideoSDK transcription)
  const recognitionRef                   = useRef(null);

  // ── SSE – real-time backend events ────────────────────────────────────────
  useEffect(() => {
    const src = new EventSource(`/api/v1/session/${callId}/events`);
    evtSourceRef.current = src;

    src.onmessage = (e) => {
      const evt = JSON.parse(e.data);
      switch (evt.event) {
        case "AI_AGENT_SPEECH":
          setAgentSpeech(evt.text);
          // Play audio if available (URL served from backend)
          if (evt.audio_url && audioPlayerRef.current) {
            audioPlayerRef.current.src = evt.audio_url;
            audioPlayerRef.current.play().catch(err => {
              console.warn("Failed to auto-play TTS audio:", err);
            });
          }
          // Clear agent speech after 10 seconds
          setTimeout(() => setAgentSpeech(""), 10000);
          break;
        case "STT_UTTERANCE":
          setCaption(evt.transcript);
          setTimeout(() => setCaption(""), 5000);
          break;
        case "OFFER_READY":
          setOffer(evt.offer);
          break;
        case "HUMAN_ESCALATION":
          setEscalated(true);
          setStage("ESCALATED");
          break;
        case "NETWORK_QUALITY_LOW":
          setNetworkScore(evt.score);
          setAudioFirst(true);
          break;
        case "SESSION_COMPLETED":
          setStage("COMPLETED");
          break;
        default:
          if (evt.stage) setStage(evt.stage);
      }
    };

    return () => src.close();
  }, [callId]);

  useEffect(() => {
    joinedRef.current = joined;
  }, [joined]);

  const notifySessionEnd = useCallback(async () => {
    if (endSentRef.current) return;
    endSentRef.current = true;

    const maxRetries = 3;
    const retryDelays = [1000, 2000, 4000]; // milliseconds

    for (let attempt = 0; attempt < maxRetries; attempt++) {
      try {
        const response = await fetch(`/api/v1/session/${callId}/end`, {
          method: "POST",
          keepalive: true,
        });

        const data = await response.json();
        console.log("Session end response:", data);

        if (!response.ok) {
          throw new Error(`HTTP ${response.status}: ${data.detail || "Failed to end session"}`);
        }

        if (!data.recording_stopped && data.recording_error) {
          setError(`Warning: Recording could not be stopped. ${data.recording_error}`);
          console.warn("Recording stop failed:", data.recording_error);
        }

        return; // Success
      } catch (err) {
        console.warn(`Session end attempt ${attempt + 1}/${maxRetries} failed:`, err);
        if (attempt < maxRetries - 1) {
          await new Promise(resolve => setTimeout(resolve, retryDelays[attempt]));
        } else {
          setError("Failed to properly end session. Your data may not be fully saved.");
          console.error("Session end failed after retries:", err);
        }
      }
    }
  }, [callId]);

  useEffect(() => {
    const notifyOnExit = () => {
      if (!joinedRef.current || endSentRef.current) return;
      endSentRef.current = true;
      if (!navigator.sendBeacon?.(`/api/v1/session/${callId}/end`)) {
        fetch(`/api/v1/session/${callId}/end`, { method: "POST", keepalive: true }).catch(() => {});
      }
    };

    window.addEventListener("beforeunload", notifyOnExit);
    return () => {
      window.removeEventListener("beforeunload", notifyOnExit);
      notifyOnExit();
    };
  }, [callId]);

  const handleJoin = useCallback(() => {
    join();
    setJoined(true);
    setStage("GREETING_CONSENT");
  }, [join]);

  // ── Browser Speech Recognition (replaces paid VideoSDK transcription) ─────
  // Uses the free Web Speech API to capture user speech and send to backend
  useEffect(() => {
    if (!joined) return;

    const SpeechRecognition = window.SpeechRecognition || window.webkitSpeechRecognition;
    if (!SpeechRecognition) {
      console.warn("SpeechRecognition API not supported in this browser");
      return;
    }

    const recognition = new SpeechRecognition();
    recognition.continuous = true;
    recognition.interimResults = true;    // <-- CHANGED to true for debugging
    recognition.lang = "en-IN";           // Indian English (also handles Hindi)
    recognition.maxAlternatives = 1;

    recognition.onresult = async (event) => {
      let interimTranscript = "";
      
      // Get the latest final result and build interim text
      for (let i = event.resultIndex; i < event.results.length; i++) {
        const transcript = event.results[i][0].transcript;
        if (event.results[i].isFinal) {
          const confidence = event.results[i][0].confidence;
          if (transcript.trim().length > 0) {
            console.log(`[FINAL] Speech recognized: "${transcript}" (conf: ${confidence.toFixed(2)})`);
            setCaption(transcript);
            setDebugSpeech(`[FINAL] ${transcript}`);

            // Send to backend for STT pipeline processing
            try {
              await fetch(`/api/v1/session/${callId}/transcript`, {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({
                  text: transcript.trim(),
                  confidence: confidence,
                  timestamp: Date.now() / 1000,
                }),
              });
            } catch (err) {
              console.warn("Failed to send transcript to backend:", err);
            }
          }
        } else {
          interimTranscript += transcript;
        }
      }
      
      // Update debug layer with real-time interim speech
      if (interimTranscript.trim().length > 0) {
        setDebugSpeech(`[LISTENING...] ${interimTranscript}`);
      }
    };

    recognition.onerror = (event) => {
      // "no-speech" and "aborted" are expected — just restart
      if (event.error === "no-speech" || event.error === "aborted") {
        return;
      }
      console.warn("Speech recognition error:", event.error);
    };

    // Auto-restart when recognition ends (browser stops after silence)
    recognition.onend = () => {
      if (joinedRef.current) {
        try {
          recognition.start();
        } catch (e) {
          // Already started, ignore
        }
      }
    };

    // Start recognition
    try {
      recognition.start();
      recognitionRef.current = recognition;
      console.log("Speech recognition started (Web Speech API)");
    } catch (e) {
      console.warn("Failed to start speech recognition:", e);
    }

    return () => {
      try {
        recognition.stop();
      } catch (e) { /* ignore */ }
      recognitionRef.current = null;
    };
  }, [joined, callId]);

  // ── Client-side recording (replaces VideoSDK cloud recording) ─────────────
  // Uses browser MediaRecorder API — free, no cloud dependency
  const startClientRecording = useCallback((stream) => {
    try {
      // Check browser support for codec
      const mimeType = MediaRecorder.isTypeSupported('video/webm;codecs=vp9')
        ? 'video/webm;codecs=vp9'
        : MediaRecorder.isTypeSupported('video/webm')
          ? 'video/webm'
          : 'video/mp4';

      recordedChunksRef.current = [];
      const recorder = new MediaRecorder(stream, { mimeType });

      recorder.ondataavailable = (event) => {
        if (event.data && event.data.size > 0) {
          recordedChunksRef.current.push(event.data);
        }
      };

      recorder.onerror = (err) => {
        console.error("MediaRecorder error:", err);
      };

      // Collect chunks every 5 seconds for reliability
      recorder.start(5000);
      mediaRecorderRef.current = recorder;
      console.log(`Client recording started (codec: ${mimeType})`);
    } catch (err) {
      console.warn("Failed to start client recording:", err);
    }
  }, []);

  const stopAndUploadRecording = useCallback(async () => {
    const recorder = mediaRecorderRef.current;
    if (!recorder || recorder.state === "inactive") return;

    return new Promise((resolve) => {
      recorder.onstop = async () => {
        try {
          if (recordedChunksRef.current.length === 0) {
            console.warn("No recording data to upload");
            resolve();
            return;
          }

          const blob = new Blob(recordedChunksRef.current, { type: 'video/webm' });
          const formData = new FormData();
          formData.append('file', blob, `recording_${callId}.webm`);

          console.log(`Uploading recording: ${(blob.size / 1024 / 1024).toFixed(1)} MB`);

          const response = await fetch(`/api/v1/session/${callId}/upload-recording`, {
            method: 'POST',
            body: formData,
          });

          if (response.ok) {
            const data = await response.json();
            console.log("Recording uploaded successfully:", data);
          } else {
            console.error("Recording upload failed:", response.status);
          }
        } catch (err) {
          console.error("Recording upload error:", err);
        }
        resolve();
      };

      recorder.stop();
    });
  }, [callId]);

  // Start recording after joining — capture local media stream
  useEffect(() => {
    if (!joined) return;

    // Get local stream for recording
    navigator.mediaDevices.getUserMedia({ video: true, audio: true })
      .then((stream) => {
        startClientRecording(stream);
      })
      .catch((err) => {
        console.warn("Could not access media for recording:", err);
      });

    return () => {
      // Cleanup on unmount
      if (mediaRecorderRef.current && mediaRecorderRef.current.state !== "inactive") {
        mediaRecorderRef.current.stop();
      }
    };
  }, [joined, startClientRecording]);

  // ── Canvas snapshot for vision agent ───────────────────────────────────────
  // Captures video frames and sends to backend for face/liveness analysis
  useEffect(() => {
    const SNAPSHOT_STAGES = ["LIVENESS_CHALLENGE", "IDENTITY_KYC"];
    if (!joined || !SNAPSHOT_STAGES.includes(stage)) {
      // Only capture snapshots during V-CIP stages that need vision
      if (snapshotIntervalRef.current) {
        clearInterval(snapshotIntervalRef.current);
        snapshotIntervalRef.current = null;
      }
      return;
    }

    const captureAndSend = async () => {
      const videoEl = localVideoRef.current;
      if (!videoEl || videoEl.videoWidth === 0) return;

      try {
        const canvas = document.createElement('canvas');
        canvas.width = videoEl.videoWidth;
        canvas.height = videoEl.videoHeight;
        const ctx = canvas.getContext('2d');
        ctx.drawImage(videoEl, 0, 0);

        // Convert to JPEG base64
        const imageData = canvas.toDataURL('image/jpeg', 0.8);

        // Send to backend
        await fetch(`/api/v1/session/${callId}/snapshot`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ image_data: imageData }),
        });
      } catch (err) {
        console.warn("Snapshot capture/send failed:", err);
      }
    };

    // Send snapshot every 10 seconds during KYC
    snapshotIntervalRef.current = setInterval(captureAndSend, 10000);
    // Also send one immediately
    captureAndSend();

    return () => {
      if (snapshotIntervalRef.current) {
        clearInterval(snapshotIntervalRef.current);
        snapshotIntervalRef.current = null;
      }
    };
  }, [joined, stage, callId]);

  const handleLeave = useCallback(async () => {
    // Upload recording before leaving
    await stopAndUploadRecording();
    await notifySessionEnd();
    leave();
  }, [leave, notifySessionEnd, stopAndUploadRecording]);

  const participantIds = [...participants.keys()].filter(
    (pid) => pid !== localParticipant?.id
  );
  const stageMeta = STAGE_META[stage] || STAGE_META.INIT;

  // ── Layout ────────────────────────────────────────────────────────────────
  return (
    <div style={styles.root}>
      {/* Background grain texture */}
      <div style={styles.grain} />

      {/* Error notification */}
      {error && (
        <div style={styles.errorNotification}>
          <span>⚠️ {error}</span>
          <button onClick={() => setError(null)} style={styles.errorClose}>✕</button>
        </div>
      )}

      {/* Header bar */}
      <header style={styles.header}>
        <div style={styles.brandMark}>
          <span style={styles.brandDot} />
          <span style={styles.brandName}>Loan Wizard</span>
        </div>
        <div style={styles.headerCenter}>
          <NetworkIndicator score={networkScore} audioFirst={audioFirst} />
          <RecordingTimer joined={joined} />
        </div>
        <div style={styles.headerRight}>
          <span style={styles.roomTag}>Room: {meetingId?.slice(-6).toUpperCase()}</span>
        </div>
      </header>

      {/* Progress bar */}
      <ProgressRail stageMeta={stageMeta} stage={stage} />

      {/* Main call area */}
      <main style={styles.main}>
        {!joined ? (
          <JoinPrompt onJoin={handleJoin} />
        ) : (
          <>
            {/* Video grid */}
            <div style={styles.videoGrid}>
              <LocalView
                participantId={localParticipant?.id}
                audioFirst={audioFirst}
                videoRef={localVideoRef}
              />
              {participantIds.map((pid) => (
                <RemoteView key={pid} participantId={pid} />
              ))}
            </div>

            {/* AI stage card */}
            <StageCard stage={stage} stageMeta={stageMeta} escalated={escalated} />

            {/* Live caption */}
            {caption && <CaptionBubble text={caption} />}

            {/* Agent speech subtitle */}
            {agentSpeech && (
              <div style={styles.agentSpeechBubble}>
                <div style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 6 }}>
                  <span style={{ fontSize: 18 }}>🤖</span>
                  <span style={{ fontSize: 13, fontWeight: 700, color: BRAND.accent }}>
                    Loan Wizard
                  </span>
                </div>
                <p style={{ margin: 0, fontSize: 16, lineHeight: 1.5 }}>
                  {agentSpeech}
                </p>
              </div>
            )}

            {/* Hidden audio player for TTS */}
            <audio ref={audioPlayerRef} style={{ display: "none" }} />

            {/* Document Upload Overlay */}
            {stage === "OVD_DOCUMENT_CAPTURE" && (
              <DocumentUploadOverlay callId={callId} />
            )}

            {/* Offer overlay */}
            {offer && stage === "OFFER_ACCEPTANCE" && (
              <OfferOverlay offer={offer} callId={callId} />
            )}

            {/* Debug Speech Layer */}
            {joined && (
              <div style={styles.debugOverlay}>
                <strong>🎤 Mic Debug:</strong> {debugSpeech || "Waiting for speech..."}
              </div>
            )}
          </>
        )}
      </main>

      {/* Control bar */}
      {joined && (
        <ControlBar
          micOn={micActive}
          camOn={camActive}
          onToggleMic={() => {
            if (toggleCooldown.current) return;
            toggleCooldown.current = true;
            toggleMic();
            setMicActive((prev) => !prev);
            setTimeout(() => { toggleCooldown.current = false; }, 500);
          }}
          onToggleCam={() => {
            if (toggleCooldown.current) return;
            toggleCooldown.current = true;
            toggleWebcam();
            setCamActive((prev) => !prev);
            setTimeout(() => { toggleCooldown.current = false; }, 500);
          }}
          onLeave={handleLeave}
          audioFirst={audioFirst}
          stage={stage}
        />
      )}
    </div>
  );
}


// ══════════════════════════════════════════════════════════════════════════════
// Sub-components
// ══════════════════════════════════════════════════════════════════════════════

function LocalView({ participantId, audioFirst, videoRef: externalVideoRef }) {
  const { webcamStream, micStream, webcamOn } = useParticipant(participantId);
  const vidRef = useRef(null);

  // Create MediaStream using useMemo (VideoSDK recommended pattern)
  const videoStream = useMemo(() => {
    if (webcamOn && webcamStream) {
      const stream = new MediaStream();
      stream.addTrack(webcamStream.track);
      return stream;
    }
    return null;
  }, [webcamStream, webcamOn]);

  useEffect(() => {
    if (vidRef.current && videoStream) {
      vidRef.current.srcObject = videoStream;
      vidRef.current.play().catch(e => console.warn("Video play error:", e));
    }
    // Also set external ref for snapshot capture
    if (externalVideoRef) {
      externalVideoRef.current = vidRef.current;
    }
  }, [videoStream, externalVideoRef]);

  const showVideo = webcamOn && !audioFirst;

  return (
    <div style={styles.videoCard}>
      <video 
        ref={vidRef} 
        autoPlay 
        muted 
        playsInline 
        style={{ ...styles.video, display: showVideo ? "block" : "none" }} 
      />
      {!showVideo && (
        <div style={styles.videoOff}>
          <span style={{ fontSize: 48 }}>🎙️</span>
          <p style={{ color: BRAND.textMuted, marginTop: 8, fontSize: 13 }}>
            {audioFirst ? "Audio-first mode" : "Camera off"}
          </p>
        </div>
      )}
      <div style={styles.videoLabel}>
        You
        <LocalAudioVisualizer micStream={micStream} />
      </div>
    </div>
  );
}

function RemoteView({ participantId }) {
  const { webcamStream, micStream, displayName, webcamOn } = useParticipant(participantId);
  const vidRef = useRef(null);
  const audRef = useRef(null);

  useEffect(() => {
    if (vidRef.current && webcamStream) {
      vidRef.current.srcObject = new MediaStream([webcamStream.track]);
      vidRef.current.play().catch(e => console.warn("Remote video play error:", e));
    }
  }, [webcamStream, webcamOn]);

  useEffect(() => {
    if (audRef.current && micStream) {
      audRef.current.srcObject = new MediaStream([micStream.track]);
      audRef.current.play().catch(e => console.warn("Remote audio play error:", e));
    }
  }, [micStream]);

  // Skip AI-agent silent participant (prefix: ai-agent-)
  if (participantId.startsWith("ai-agent-")) return null;

  const isOfficer = participantId.startsWith("official-");

  return (
    <div style={{ ...styles.videoCard, ...(isOfficer ? styles.officerCard : {}) }}>
      {webcamOn ? (
        <video ref={vidRef} autoPlay playsInline style={styles.video} />
      ) : (
        <div style={styles.videoOff}>
          <span style={{ fontSize: 44 }}>{isOfficer ? "👤" : "🤖"}</span>
          <p style={{ color: BRAND.textMuted, fontSize: 13, marginTop: 8 }}>
            {isOfficer ? "Loan Officer" : "AI Assistant"}
          </p>
        </div>
      )}
      <audio ref={audRef} autoPlay />
      <div style={styles.videoLabel}>
        {isOfficer ? "👤 Loan Officer" : displayName || "AI Agent"}
      </div>
    </div>
  );
}

function JoinPrompt({ onJoin }) {
  return (
    <div style={styles.joinPrompt}>
      <div style={styles.joinGlow} />
      <p style={styles.joinSubtitle}>Your loan journey begins with a 10-minute video call</p>
      <ul style={styles.joinChecklist}>
        {["No forms to fill", "Instant offer in-call", "RBI-compliant & secure"].map((t) => (
          <li key={t} style={styles.joinCheckItem}>
            <span style={styles.checkMark}>✓</span> {t}
          </li>
        ))}
      </ul>
      <button style={styles.joinBtn} onClick={onJoin}>
        Start Video Call
      </button>
      <p style={styles.joinDisclaimer}>
        By joining, you consent to this call being recorded for RBI compliance.
      </p>
    </div>
  );
}

function ProgressRail({ stageMeta }) {
  return (
    <div style={styles.progressRail}>
      <div style={{ ...styles.progressFill, width: `${stageMeta.pct}%` }} />
      <div style={styles.progressLabel}>
        <span style={{ marginRight: 6 }}>{stageMeta.icon}</span>
        {stageMeta.label}
        <span style={styles.progressPct}>{stageMeta.pct}%</span>
      </div>
    </div>
  );
}

function StageCard({ stage, stageMeta, escalated }) {
  if (stage === "INIT" || stage === "COMPLETED") return null;
  return (
    <div style={{ ...styles.stageCard, ...(escalated ? styles.stageCardEscalated : {}) }}>
      <span style={styles.stageIcon}>{stageMeta.icon}</span>
      <div>
        <p style={styles.stageLabel}>
          {escalated ? "Connecting to a Loan Officer…" : stageMeta.label}
        </p>
        <p style={styles.stageSub}>
          {escalated
            ? "A certified official will join shortly"
            : "Please answer the AI agent's questions clearly"}
        </p>
      </div>
    </div>
  );
}

function CaptionBubble({ text }) {
  return (
    <div style={styles.caption}>
      <span style={styles.captionDot} />
      {text}
    </div>
  );
}

function NetworkIndicator({ score, audioFirst }) {
  const bars  = [1, 2, 3, 4, 5];
  const color = score >= 4 ? BRAND.accent : score >= 2 ? "#FBBF24" : BRAND.danger;
  return (
    <div style={styles.netIndicator}>
      {bars.map((b) => (
        <div
          key={b}
          style={{
            ...styles.netBar,
            height: `${b * 4 + 4}px`,
            background: b <= score ? color : BRAND.border,
          }}
        />
      ))}
      {audioFirst && (
        <span style={{ ...styles.netLabel, color: "#FBBF24" }}>Audio mode</span>
      )}
    </div>
  );
}

function RecordingTimer({ joined }) {
  const [seconds, setSeconds] = useState(0);

  useEffect(() => {
    if (!joined) return;
    const interval = setInterval(() => setSeconds(s => s + 1), 1000);
    return () => clearInterval(interval);
  }, [joined]);

  if (!joined) return null;

  const mins = Math.floor(seconds / 60).toString().padStart(2, "0");
  const secs = (seconds % 60).toString().padStart(2, "0");

  return (
    <div style={styles.recordingTimer}>
      <span style={styles.recordDot} />
      REC {mins}:{secs}
    </div>
  );
}

function LocalAudioVisualizer({ micStream }) {
  const [volume, setVolume] = useState(0);

  useEffect(() => {
    if (!micStream) {
      setVolume(0);
      return;
    }
    const audioContext = new (window.AudioContext || window.webkitAudioContext)();
    const mediaStreamSource = audioContext.createMediaStreamSource(new MediaStream([micStream.track]));
    const analyser = audioContext.createAnalyser();
    analyser.fftSize = 256;
    mediaStreamSource.connect(analyser);

    const dataArray = new Uint8Array(analyser.frequencyBinCount);
    let animationFrame;

    const checkVolume = () => {
      analyser.getByteFrequencyData(dataArray);
      let sum = 0;
      for (let i = 0; i < dataArray.length; i++) {
        sum += dataArray[i];
      }
      const avg = sum / dataArray.length;
      setVolume(avg);
      animationFrame = requestAnimationFrame(checkVolume);
    };
    checkVolume();

    return () => {
      cancelAnimationFrame(animationFrame);
      audioContext.close();
    };
  }, [micStream]);

  return (
    <div style={{ display: "flex", gap: 3, marginLeft: 8, alignItems: "center", height: 12 }}>
      <div style={{ ...styles.volBar, height: 4 + (volume / 255) * 8 }} />
      <div style={{ ...styles.volBar, height: 4 + (volume / 255) * 12 }} />
      <div style={{ ...styles.volBar, height: 4 + (volume / 255) * 8 }} />
    </div>
  );
}



function OfferOverlay({ offer, callId }) {
  const [selected,  setSelected]  = useState(24);
  const [accepting, setAccepting] = useState(false);
  const [accepted,  setAccepted]  = useState(false);

  const emiKey = `emi_${selected}m`;

  const handleAccept = async () => {
    setAccepting(true);
    await fetch(`/api/v1/session/${callId}/offer/accept`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ tenure: selected }),
    });
    setAccepted(true);
  };

  if (accepted) {
    return (
      <div style={styles.offerOverlay}>
        <div style={styles.offerCard}>
          <div style={{ fontSize: 56, marginBottom: 12 }}>🎉</div>
          <h2 style={styles.offerTitle}>Loan Accepted!</h2>
          <p style={{ color: BRAND.textMuted }}>
            Our team will process your application. Check WhatsApp for next steps.
          </p>
        </div>
      </div>
    );
  }

  return (
    <div style={styles.offerOverlay}>
      <div style={styles.offerCard}>
        <div style={styles.offerBadge}>🎁 Your Personalised Offer</div>
        <div style={styles.offerAmount}>
          ₹{Number(offer.eligible_amount).toLocaleString("en-IN")}
        </div>
        <p style={styles.offerRate}>at {offer.interest_rate}% p.a.</p>

        {offer.explanation && (
          <p style={styles.offerExplanation}>{offer.explanation}</p>
        )}

        {/* Tenure selector */}
        <div style={styles.tenureRow}>
          {(offer.tenure_options || [12, 24, 36]).map((t) => (
            <button
              key={t}
              style={{ ...styles.tenureBtn, ...(selected === t ? styles.tenureBtnActive : {}) }}
              onClick={() => setSelected(t)}
            >
              {t}m
            </button>
          ))}
        </div>

        <div style={styles.emiDisplay}>
          EMI: <strong>₹{Number(offer[emiKey] || offer.emi_24m).toLocaleString("en-IN")}/mo</strong>
        </div>

        <div style={styles.offerActions}>
          <button style={styles.acceptBtn} onClick={handleAccept} disabled={accepting}>
            {accepting ? "Processing…" : "✅ Accept via UPI"}
          </button>
          <a href={offer.kfs_url} target="_blank" rel="noreferrer" style={styles.kfsLink}>
            View Key Facts Statement ↗
          </a>
        </div>
      </div>
    </div>
  );
}

function ControlBar({ micOn, camOn, onToggleMic, onToggleCam, onLeave, audioFirst, stage }) {
  return (
    <footer style={styles.controlBar}>
      <CtrlBtn
        active={micOn}
        icon={micOn ? "🎙️" : "🔇"}
        label={micOn ? "Mute" : "Unmute"}
        onClick={onToggleMic}
      />
      {!audioFirst && (
        <CtrlBtn
          active={camOn}
          icon={camOn ? "📷" : "📷"}
          label={camOn ? "Camera off" : "Camera on"}
          onClick={onToggleCam}
        />
      )}
      <CtrlBtn
        active={false}
        icon="📞"
        label="End Call"
        danger
        onClick={onLeave}
        disabled={stage === "OFFER_ACCEPTANCE"}
      />
    </footer>
  );
}

function CtrlBtn({ icon, label, onClick, danger, active, disabled }) {
  return (
    <button
      style={{
        ...styles.ctrlBtn,
        ...(danger    ? styles.ctrlBtnDanger   : {}),
        ...(active    ? {}                      : styles.ctrlBtnInactive),
        ...(disabled  ? styles.ctrlBtnDisabled : {}),
      }}
      onClick={onClick}
      disabled={disabled}
      title={label}
    >
      <span style={{ fontSize: 22 }}>{icon}</span>
      <span style={styles.ctrlLabel}>{label}</span>
    </button>
  );
}


// ══════════════════════════════════════════════════════════════════════════════
// Styles
// ══════════════════════════════════════════════════════════════════════════════
const styles = {
  root: {
    display: "flex", flexDirection: "column",
    minHeight: "100vh", background: BRAND.surface,
    color: BRAND.text, fontFamily: "'DM Sans', 'Segoe UI', sans-serif",
    position: "relative", overflow: "hidden",
  },
  grain: {
    position: "fixed", inset: 0, zIndex: 0, pointerEvents: "none",
    backgroundImage: `url("data:image/svg+xml,%3Csvg viewBox='0 0 256 256' xmlns='http://www.w3.org/2000/svg'%3E%3Cfilter id='n'%3E%3CfeTurbulence type='fractalNoise' baseFrequency='0.9' numOctaves='4'/%3E%3C/filter%3E%3Crect width='100%25' height='100%25' filter='url(%23n)' opacity='0.035'/%3E%3C/svg%3E")`,
    backgroundSize: "128px",
  },
  header: {
    display: "flex", alignItems: "center", justifyContent: "space-between",
    padding: "12px 24px", borderBottom: `1px solid ${BRAND.border}`,
    background: "rgba(10,15,30,0.9)", backdropFilter: "blur(12px)",
    position: "relative", zIndex: 10,
  },
  brandMark: { display: "flex", alignItems: "center", gap: 10 },
  brandDot: {
    width: 10, height: 10, borderRadius: "50%",
    background: BRAND.accent, boxShadow: `0 0 12px ${BRAND.accent}`,
  },
  brandName: { fontWeight: 700, fontSize: 16, letterSpacing: "-0.02em" },
  headerCenter: { display: "flex", alignItems: "center", gap: 12 },
  headerRight: {},
  roomTag: {
    fontSize: 11, color: BRAND.textMuted,
    fontFamily: "monospace", letterSpacing: "0.08em",
  },
  progressRail: {
    height: 6, background: BRAND.surfaceAlt,
    position: "relative", overflow: "hidden",
  },
  progressFill: {
    height: "100%",
    background: `linear-gradient(90deg, ${BRAND.primary}, ${BRAND.accent})`,
    transition: "width 0.8s cubic-bezier(0.4,0,0.2,1)",
    boxShadow: `0 0 20px ${BRAND.accent}60`,
  },
  progressLabel: {
    position: "absolute", right: 12, top: "50%",
    transform: "translateY(-50%)",
    fontSize: 11, color: BRAND.textMuted,
    display: "flex", alignItems: "center", gap: 4,
  },
  progressPct: { marginLeft: 8, color: BRAND.accent, fontWeight: 600 },
  main: {
    flex: 1, display: "flex", flexDirection: "column", alignItems: "center",
    padding: "24px", gap: 20, position: "relative", zIndex: 2,
  },
  videoGrid: {
    display: "flex", gap: 16, flexWrap: "wrap", justifyContent: "center",
    width: "100%", maxWidth: 900,
  },
  videoCard: {
    position: "relative", borderRadius: 16,
    overflow: "hidden", background: BRAND.surfaceAlt,
    border: `1px solid ${BRAND.border}`,
    width: 360, height: 270,
    boxShadow: "0 8px 40px rgba(0,0,0,0.5)",
  },
  officerCard: {
    border: `1px solid ${BRAND.accent}60`,
    boxShadow: `0 0 20px ${BRAND.accent}20`,
  },
  video: { width: "100%", height: "100%", objectFit: "cover" },
  videoOff: {
    width: "100%", height: "100%",
    display: "flex", flexDirection: "column",
    alignItems: "center", justifyContent: "center",
  },
  videoLabel: {
    position: "absolute", bottom: 10, left: 12,
    fontSize: 12, fontWeight: 600,
    background: "rgba(0,0,0,0.65)",
    padding: "6px 12px", borderRadius: 20,
    backdropFilter: "blur(6px)",
    display: "flex", alignItems: "center", gap: 6,
  },
  stageCard: {
    display: "flex", alignItems: "center", gap: 14,
    background: "rgba(17,24,39,0.85)", backdropFilter: "blur(12px)",
    border: `1px solid ${BRAND.border}`,
    borderRadius: 14, padding: "14px 20px",
    maxWidth: 420, width: "100%",
    boxShadow: "0 4px 24px rgba(0,0,0,0.3)",
  },
  stageCardEscalated: {
    border: `1px solid ${BRAND.accent}60`,
    background: `rgba(0,201,167,0.08)`,
  },
  stageIcon: { fontSize: 28 },
  stageLabel: { fontWeight: 700, fontSize: 15, margin: 0 },
  stageSub: { fontSize: 12, color: BRAND.textMuted, margin: "3px 0 0" },
  caption: {
    position: "fixed", bottom: 100, left: "50%", transform: "translateX(-50%)",
    background: "rgba(0,0,0,0.82)", backdropFilter: "blur(8px)",
    borderRadius: 40, padding: "10px 22px",
    fontSize: 14, maxWidth: 540, textAlign: "center",
    border: `1px solid ${BRAND.border}`,
    display: "flex", alignItems: "center", gap: 8,
    animation: "fadeUp 0.3s ease",
  },
  captionDot: {
    width: 7, height: 7, borderRadius: "50%",
    background: BRAND.accent, flexShrink: 0,
    animation: "pulse 1.5s infinite",
  },
  netIndicator: {
    display: "flex", alignItems: "flex-end", gap: 3, height: 24,
  },
  netBar: { width: 4, borderRadius: 2, transition: "background 0.3s" },
  netLabel: { fontSize: 11, marginLeft: 6, fontWeight: 600 },
  controlBar: {
    display: "flex", justifyContent: "center", gap: 12,
    padding: "16px 24px", borderTop: `1px solid ${BRAND.border}`,
    background: "rgba(10,15,30,0.95)", backdropFilter: "blur(12px)",
    position: "relative", zIndex: 10,
  },
  ctrlBtn: {
    display: "flex", flexDirection: "column", alignItems: "center",
    gap: 4, padding: "10px 18px", borderRadius: 12,
    border: `1px solid ${BRAND.border}`,
    background: BRAND.surfaceAlt, color: BRAND.text,
    cursor: "pointer", transition: "all 0.2s",
    minWidth: 70,
  },
  ctrlBtnDanger: { background: "#7F1D1D", border: `1px solid ${BRAND.danger}40` },
  ctrlBtnInactive: { opacity: 0.55 },
  ctrlBtnDisabled: { opacity: 0.3, cursor: "not-allowed" },
  ctrlLabel: { fontSize: 10, fontWeight: 600, letterSpacing: "0.03em" },
  joinPrompt: {
    display: "flex", flexDirection: "column", alignItems: "center",
    textAlign: "center", maxWidth: 420, padding: "40px 24px",
    position: "relative",
  },
  joinGlow: {
    position: "absolute", top: -60, width: 280, height: 280,
    borderRadius: "50%",
    background: `radial-gradient(circle, ${BRAND.primary}30, transparent 70%)`,
    pointerEvents: "none",
  },
  joinSubtitle: { fontSize: 16, color: BRAND.textMuted, marginBottom: 24, lineHeight: 1.6 },
  joinChecklist: { listStyle: "none", padding: 0, margin: "0 0 32px", textAlign: "left" },
  joinCheckItem: {
    padding: "8px 0", fontSize: 14,
    display: "flex", alignItems: "center", gap: 10,
    color: BRAND.text,
  },
  checkMark: {
    color: BRAND.accent, fontWeight: 800, fontSize: 16,
    background: `${BRAND.accent}18`, borderRadius: "50%",
    width: 24, height: 24, display: "flex", alignItems: "center", justifyContent: "center",
    flexShrink: 0,
  },
  joinBtn: {
    padding: "14px 48px", borderRadius: 40,
    background: `linear-gradient(135deg, ${BRAND.primary}, ${BRAND.accent})`,
    border: "none", color: "#fff",
    fontSize: 16, fontWeight: 700, cursor: "pointer",
    boxShadow: `0 0 32px ${BRAND.accent}40`,
    transition: "transform 0.2s, box-shadow 0.2s",
  },
  joinDisclaimer: { fontSize: 11, color: BRAND.textMuted, marginTop: 16, lineHeight: 1.5 },
  offerOverlay: {
    position: "fixed", inset: 0, zIndex: 100,
    background: "rgba(10,15,30,0.85)", backdropFilter: "blur(8px)",
    display: "flex", alignItems: "center", justifyContent: "center",
    padding: 24,
    animation: "fadeIn 0.4s ease",
  },
  offerCard: {
    background: BRAND.surfaceAlt, borderRadius: 20,
    border: `1px solid ${BRAND.accent}40`,
    padding: 32, maxWidth: 420, width: "100%",
    boxShadow: `0 0 60px ${BRAND.accent}20`,
    textAlign: "center",
  },
  documentUploadCard: {
    position: "absolute",
    top: "50%", left: "50%", transform: "translate(-50%, -50%)",
    background: "rgba(17,24,39,0.95)", backdropFilter: "blur(12px)",
    borderRadius: 20, border: `1px solid ${BRAND.accent}40`,
    padding: 32, maxWidth: 400, width: "90%",
    boxShadow: `0 0 40px ${BRAND.accent}20`,
    textAlign: "center", zIndex: 100,
    animation: "fadeIn 0.3s ease",
  },
  offerBadge: {
    display: "inline-block",
    background: `${BRAND.accent}18`, color: BRAND.accent,
    fontSize: 13, fontWeight: 700, borderRadius: 40,
    padding: "6px 16px", marginBottom: 16,
  },
  offerAmount: {
    fontSize: 42, fontWeight: 800, letterSpacing: "-0.03em",
    background: `linear-gradient(135deg, #fff, ${BRAND.accent})`,
    WebkitBackgroundClip: "text", WebkitTextFillColor: "transparent",
  },
  offerRate: { color: BRAND.textMuted, fontSize: 14, margin: "4px 0 16px" },
  offerExplanation: {
    fontSize: 14, color: BRAND.textMuted, lineHeight: 1.6,
    margin: "0 0 20px", textAlign: "left",
  },
  offerTitle: { fontSize: 26, fontWeight: 800, margin: "0 0 12px" },
  tenureRow: { display: "flex", gap: 8, justifyContent: "center", marginBottom: 16 },
  tenureBtn: {
    padding: "8px 18px", borderRadius: 40,
    border: `1px solid ${BRAND.border}`,
    background: "transparent", color: BRAND.textMuted,
    cursor: "pointer", fontSize: 14, fontWeight: 600,
    transition: "all 0.2s",
  },
  tenureBtnActive: {
    background: BRAND.accent, border: `1px solid ${BRAND.accent}`,
    color: "#000",
  },
  emiDisplay: {
    fontSize: 14, color: BRAND.textMuted, marginBottom: 24,
  },
  offerActions: {
    display: "flex", flexDirection: "column", gap: 12, alignItems: "center",
  },
  acceptBtn: {
    width: "100%", padding: "14px", borderRadius: 12,
    background: `linear-gradient(135deg, ${BRAND.primary}, ${BRAND.accent})`,
    border: "none", color: "#fff",
    fontSize: 16, fontWeight: 700, cursor: "pointer",
  },
  kfsLink: {
    fontSize: 12, color: BRAND.accent, textDecoration: "none",
  },
  errorNotification: {
    position: "fixed", top: 80, left: "50%", transform: "translateX(-50%)",
    zIndex: 50, background: "#7F1D1D", border: `1px solid ${BRAND.danger}60`,
    color: BRAND.text, padding: "12px 20px", borderRadius: 12,
    display: "flex", alignItems: "center", justifyContent: "space-between",
    gap: 16, maxWidth: 500, width: "90%",
    boxShadow: "0 8px 32px rgba(0,0,0,0.5)",
    animation: "slideDown 0.3s ease",
    fontSize: 14,
  },
  errorClose: {
    background: "none", border: "none", color: BRAND.text,
    fontSize: 18, cursor: "pointer", padding: 0,
    display: "flex", alignItems: "center", justifyContent: "center",
  },
  agentSpeechBubble: {
    position: "fixed", bottom: 110, left: "50%", transform: "translateX(-50%)",
    background: "rgba(10,15,30,0.9)", backdropFilter: "blur(12px)",
    borderRadius: 12, padding: "14px 24px",
    maxWidth: 600, width: "90%",
    border: `1px solid ${BRAND.primary}50`,
    boxShadow: "0 8px 32px rgba(0,0,0,0.4)",
    animation: "slideUp 0.4s ease",
    textAlign: "left",
  },
  recordingTimer: {
    display: "flex", alignItems: "center", gap: 6,
    background: "rgba(255, 71, 87, 0.15)",
    border: `1px solid ${BRAND.danger}40`,
    padding: "4px 10px", borderRadius: 20,
    color: BRAND.danger, fontSize: 12, fontWeight: 700,
    letterSpacing: "0.05em",
  },
  recordDot: {
    width: 8, height: 8, borderRadius: "50%",
    background: BRAND.danger,
    animation: "pulse 1.5s infinite",
  },
  volBar: {
    width: 3, borderRadius: 2, background: BRAND.accent,
    transition: "height 0.1s ease",
  },
  debugOverlay: {
    position: "absolute", top: 100, right: 32,
    background: "rgba(255, 100, 0, 0.8)", color: "#fff",
    padding: "8px 16px", borderRadius: 8, fontSize: 13,
    maxWidth: 300, zIndex: 50, fontFamily: "monospace",
  },
};


function DocumentUploadOverlay({ callId }) {
  const [file, setFile] = useState(null);
  const [uploading, setUploading] = useState(false);
  const [success, setSuccess] = useState(false);

  const handleUpload = async () => {
    if (!file) return;
    setUploading(true);
    
    const formData = new FormData();
    formData.append("file", file);
    
    try {
      const response = await fetch(`/api/v1/session/${callId}/upload-document`, {
        method: "POST",
        body: formData,
      });
      if (response.ok) {
        setSuccess(true);
      } else {
        console.error("Document upload failed");
        setUploading(false);
      }
    } catch (err) {
      console.error("Document upload error", err);
      setUploading(false);
    }
  };

  if (success) {
    return (
      <div style={styles.documentUploadCard}>
        <div style={{ fontSize: 32, marginBottom: 8 }}>✅</div>
        <h3 style={{ margin: 0, fontSize: 18 }}>Document Uploaded!</h3>
        <p style={{ fontSize: 13, color: BRAND.textMuted, margin: "8px 0 0" }}>
          Please wait while we verify your identity...
        </p>
      </div>
    );
  }

  return (
    <div style={styles.documentUploadCard}>
      <h3 style={{ margin: "0 0 12px 0", fontSize: 18 }}>Upload Identity Document</h3>
      <p style={{ fontSize: 13, color: BRAND.textMuted, margin: "0 0 16px 0", lineHeight: 1.4 }}>
        Please upload a clear photo of your original Aadhaar or PAN card to proceed.
      </p>
      <input 
        type="file" 
        accept="image/*" 
        onChange={(e) => setFile(e.target.files[0])}
        style={{ marginBottom: 16, width: "100%", fontSize: 13 }}
      />
      <button 
        style={{
          ...styles.joinBtn, 
          padding: "10px 24px", 
          fontSize: 14, 
          width: "100%",
          opacity: (!file || uploading) ? 0.6 : 1,
          cursor: (!file || uploading) ? "not-allowed" : "pointer"
        }}
        onClick={handleUpload}
        disabled={!file || uploading}
      >
        {uploading ? "Uploading..." : "Upload Document"}
      </button>
    </div>
  );
}

const keyframes = `
  @keyframes pulse {
    0% { opacity: 1; transform: scale(1); }
    50% { opacity: 0.5; transform: scale(0.8); }
    100% { opacity: 1; transform: scale(1); }
  }
`;
if (typeof document !== "undefined") {
  const style = document.createElement("style");
  style.innerHTML = keyframes;
  document.head.appendChild(style);
}
