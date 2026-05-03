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
  primary:    "#3b82f6",   // Electric Blue
  accent:     "#60a5fa",   // Cyan
  danger:     "#ef4444",
  surface:    "rgba(255, 255, 255, 0.03)",
  surfaceAlt: "rgba(255, 255, 255, 0.05)",
  border:     "rgba(255, 255, 255, 0.1)",
  text:       "#f8fafc",
  textMuted:  "#94a3b8",
};

const STAGE_META = {
  INIT:                 { label: "Initializing…",        icon: "⚡", pct: 0  },
  GREETING_CONSENT:     { label: "Consent & Greeting",    icon: "🤝", pct: 8  },
  OVD_DOCUMENT_CAPTURE: { label: "Document Verification", icon: "📄", pct: 18 },
  LIVENESS_CHALLENGE:   { label: "Biometric Liveness",    icon: "👁️", pct: 28 },
  AADHAAR_VERIFICATION: { label: "Digital Aadhaar",       icon: "🔐", pct: 38 },
  IDENTITY_KYC:         { label: "Identity Match",        icon: "🪪", pct: 48 },
  EMPLOYMENT_INCOME:    { label: "Financial Data",        icon: "💼", pct: 60 },
  LOAN_PURPOSE:         { label: "Loan Objective",        icon: "🎯", pct: 72 },
  RISK_ASSESSMENT:      { label: "AI Underwriting",       icon: "📊", pct: 84 },
  OFFER_ACCEPTANCE:     { label: "Loan Offer",            icon: "🎁", pct: 95 },
  COMPLETED:            { label: "Approved!",             icon: "✅", pct: 100 },
  ESCALATED:            { label: "Human Officer",         icon: "👤", pct: 84 },
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
    onMeetingLeft:    () => {
      console.log("Meeting left");
      setJoined(false);
    },
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
  const recordingStreamRef               = useRef(null);
  const localParticipantId = localParticipant?.id;
  const { webcamStream, micStream } = useParticipant(localParticipantId);

  // ── SSE – real-time backend events ────────────────────────────────────────
  useEffect(() => {
    const src = new EventSource(`/api/v1/session/${callId}/events`);
    evtSourceRef.current = src;

    src.onmessage = (e) => {
      const evt = JSON.parse(e.data);
      switch (evt.event) {
        case "AI_AGENT_SPEECH":
          setAgentSpeech(evt.text);
          // Clear agent speech after 10 seconds
          setTimeout(() => setAgentSpeech(""), 10000);
          break;
        case "TTS_AUDIO_READY":
          // Play audio when synthesis is complete
          if (evt.audio_url && audioPlayerRef.current) {
            console.log("Playing agent audio:", evt.audio_url);
            audioPlayerRef.current.src = evt.audio_url;
            audioPlayerRef.current.play().catch(err => {
              console.error("Agent audio playback failed:", err);
              setError("Audio playback blocked. Please click anywhere on the page.");
            });
          } else {
            console.warn("TTS_AUDIO_READY received but audioPlayerRef or audio_url is missing");
          }
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

      // Stop the media tracks to turn off the camera/mic
      if (recordingStreamRef.current) {
        recordingStreamRef.current.getTracks().forEach(track => track.stop());
        recordingStreamRef.current = null;
      }
    });
  }, [callId]);

  // Start recording after joining — reuse VideoSDK tracks to avoid 2nd camera stream
  useEffect(() => {
    if (!joined || !webcamStream || !micStream) return;

    try {
      const stream = new MediaStream([webcamStream.track, micStream.track]);
      recordingStreamRef.current = stream;
      startClientRecording(stream);
      console.log("Recording started using VideoSDK tracks");
    } catch (err) {
      console.warn("Failed to start recording from VideoSDK tracks:", err);
    }

    return () => {
      // Cleanup on unmount
      if (mediaRecorderRef.current && mediaRecorderRef.current.state !== "inactive") {
        mediaRecorderRef.current.stop();
      }
    };
  }, [joined, webcamStream, micStream, startClientRecording]);

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
    console.log("End call initiated...");
    
    // 1. Stop recording (this starts the background upload, do NOT await)
    stopAndUploadRecording();
    
    // 2. Notify backend that session is ending (non-blocking, uses keepalive)
    notifySessionEnd();
    
    // 3. Leave the VideoSDK meeting immediately
    try {
      leave();
    } catch (e) {
      console.warn("Error during VideoSDK leave:", e);
    }
    
    console.log("Call ended locally.");
  }, [leave, notifySessionEnd, stopAndUploadRecording]);

  const participantIds = [...participants.keys()].filter(
    (pid) => pid !== localParticipant?.id
  );
  const stageMeta = STAGE_META[stage] || STAGE_META.INIT;

  // ── Layout ────────────────────────────────────────────────────────────────
  return (
    <div style={styles.root}>
      <audio 
        ref={audioPlayerRef} 
        style={{ position: 'absolute', opacity: 0, pointerEvents: 'none' }} 
        crossOrigin="anonymous"
      />
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
    minHeight: "100vh", background: "var(--bg-primary)",
    color: "var(--text-primary)", fontFamily: "var(--font-sans)",
    position: "relative", overflow: "hidden",
  },
  grain: {
    position: "fixed", inset: 0, zIndex: 0, pointerEvents: "none",
    backgroundImage: `url("data:image/svg+xml,%3Csvg viewBox='0 0 256 256' xmlns='http://www.w3.org/2000/svg'%3E%3Cfilter id='n'%3E%3CfeTurbulence type='fractalNoise' baseFrequency='0.9' numOctaves='4'/%3E%3C/filter%3E%3Crect width='100%25' height='100%25' filter='url(%23n)' opacity='0.02'/%3E%3C/svg%3E")`,
    backgroundSize: "128px",
  },
  header: {
    display: "flex", alignItems: "center", justifyContent: "space-between",
    padding: "16px 32px", borderBottom: "1px solid var(--border)",
    background: "rgba(5,5,5,0.8)", backdropFilter: "blur(12px)",
    position: "relative", zIndex: 10,
  },
  brandMark: { display: "flex", alignItems: "center", gap: 12 },
  brandDot: {
    width: 8, height: 8, borderRadius: "50%",
    background: "var(--accent-primary)", boxShadow: "0 0 15px var(--accent-glow)",
  },
  brandName: { fontWeight: 700, fontSize: 18, fontFamily: "var(--font-heading)", letterSpacing: "-0.02em" },
  headerCenter: { display: "flex", alignItems: "center", gap: 24 },
  headerRight: {},
  roomTag: {
    fontSize: 10, color: "var(--text-tertiary)",
    fontFamily: "monospace", letterSpacing: "0.1em",
    textTransform: "uppercase",
  },
  progressRail: {
    height: 4, background: "var(--bg-tertiary)",
    position: "relative", overflow: "hidden",
  },
  progressFill: {
    height: "100%",
    background: "linear-gradient(90deg, var(--accent-primary), var(--accent-secondary))",
    transition: "width 1s cubic-bezier(0.4,0,0.2,1)",
    boxShadow: "0 0 20px var(--accent-glow)",
  },
  progressLabel: {
    display: "none", // Cleaner look
  },
  main: {
    flex: 1, display: "flex", flexDirection: "column", alignItems: "center",
    padding: "40px 24px", gap: 32, position: "relative", zIndex: 2,
  },
  videoGrid: {
    display: "flex", gap: 24, flexWrap: "wrap", justifyContent: "center",
    width: "100%", maxWidth: 1000,
  },
  videoCard: {
    position: "relative", borderRadius: 24,
    overflow: "hidden", background: "var(--surface)",
    border: "1px solid var(--border)",
    width: 440, height: 330,
    boxShadow: "0 20px 50px rgba(0,0,0,0.5)",
    transition: "all 0.3s ease",
  },
  officerCard: {
    border: "2px solid var(--accent-primary)",
    boxShadow: "0 0 30px var(--accent-glow)",
  },
  video: { width: "100%", height: "100%", objectFit: "cover" },
  videoOff: {
    width: "100%", height: "100%",
    display: "flex", flexDirection: "column",
    alignItems: "center", justifyContent: "center",
    background: "linear-gradient(135deg, #0a0a0a 0%, #111 100%)",
  },
  videoLabel: {
    position: "absolute", bottom: 16, left: 16,
    fontSize: 12, fontWeight: 600,
    background: "rgba(0,0,0,0.6)",
    padding: "8px 16px", borderRadius: 100,
    backdropFilter: "blur(8px)",
    border: "1px solid rgba(255,255,255,0.1)",
    display: "flex", alignItems: "center", gap: 8,
  },
  stageCard: {
    display: "flex", alignItems: "center", gap: 16,
    background: "rgba(255,255,255,0.03)", backdropFilter: "blur(12px)",
    border: "1px solid var(--border)",
    borderRadius: 20, padding: "20px 24px",
    maxWidth: 480, width: "100%",
    boxShadow: "0 10px 30px rgba(0,0,0,0.4)",
    animation: "fadeIn 0.6s ease-out",
  },
  stageCardEscalated: {
    border: "1px solid var(--accent-primary)",
    background: "rgba(59, 130, 246, 0.05)",
  },
  stageIcon: { fontSize: 32 },
  stageLabel: { fontWeight: 700, fontSize: 18, margin: 0, fontFamily: "var(--font-heading)" },
  stageSub: { fontSize: 13, color: "var(--text-secondary)", margin: "4px 0 0" },
  caption: {
    position: "fixed", bottom: 120, left: "50%", transform: "translateX(-50%)",
    background: "rgba(5,5,5,0.9)", backdropFilter: "blur(12px)",
    borderRadius: 16, padding: "12px 24px",
    fontSize: 15, maxWidth: 600, textAlign: "center",
    border: "1px solid var(--border)",
    display: "flex", alignItems: "center", gap: 12,
    boxShadow: "0 10px 40px rgba(0,0,0,0.5)",
  },
  captionDot: {
    width: 8, height: 8, borderRadius: "50%",
    background: "var(--accent-primary)", flexShrink: 0,
    animation: "pulse 1.5s infinite",
  },
  netIndicator: {
    display: "flex", alignItems: "flex-end", gap: 3, height: 20,
  },
  netBar: { width: 3, borderRadius: 2, transition: "background 0.3s" },
  netLabel: { fontSize: 10, marginLeft: 8, fontWeight: 600, color: "var(--text-tertiary)" },
  controlBar: {
    display: "flex", justifyContent: "center", gap: 16,
    padding: "20px 32px", borderTop: "1px solid var(--border)",
    background: "rgba(5,5,5,0.95)", backdropFilter: "blur(12px)",
    position: "relative", zIndex: 10,
  },
  ctrlBtn: {
    display: "flex", flexDirection: "column", alignItems: "center",
    gap: 6, padding: "12px 24px", borderRadius: 16,
    border: "1px solid var(--border)",
    background: "var(--surface)", color: "var(--text-primary)",
    cursor: "pointer", transition: "all 0.2s cubic-bezier(0.4, 0, 0.2, 1)",
    minWidth: 80,
  },
  ctrlBtnDanger: { background: "rgba(239, 68, 68, 0.1)", border: "1px solid rgba(239, 68, 68, 0.3)", color: "var(--error)" },
  ctrlBtnInactive: { opacity: 0.6, background: "transparent" },
  ctrlBtnDisabled: { opacity: 0.3, cursor: "not-allowed" },
  ctrlLabel: { fontSize: 10, fontWeight: 700, letterSpacing: "0.05em", textTransform: "uppercase" },
  joinPrompt: {
    display: "flex", flexDirection: "column", alignItems: "center",
    textAlign: "center", maxWidth: 480, padding: "60px 40px",
    background: "var(--surface)", borderRadius: 32, border: "1px solid var(--border)",
    position: "relative", boxShadow: "0 30px 60px rgba(0,0,0,0.6)",
  },
  joinGlow: {
    position: "absolute", top: -100, width: 400, height: 400,
    borderRadius: "50%",
    background: "radial-gradient(circle, var(--accent-glow) 0%, transparent 70%)",
    filter: "blur(40px)", pointerEvents: "none", opacity: 0.4,
  },
  joinSubtitle: { fontSize: 18, color: "var(--text-secondary)", marginBottom: 32, lineHeight: 1.6 },
  joinChecklist: { listStyle: "none", padding: 0, margin: "0 0 40px", textAlign: "left", width: "100%" },
  joinCheckItem: {
    padding: "12px 0", fontSize: 15,
    display: "flex", alignItems: "center", gap: 14,
    color: "var(--text-primary)",
  },
  checkMark: {
    color: "var(--accent-primary)", fontWeight: 800, fontSize: 16,
    background: "var(--surface-hover)", borderRadius: "50%",
    width: 28, height: 28, display: "flex", alignItems: "center", justifyContent: "center",
    flexShrink: 0, border: "1px solid var(--border)",
  },
  joinBtn: {
    padding: "16px 48px", borderRadius: 16,
    background: "var(--accent-primary)",
    border: "none", color: "#fff",
    fontSize: 17, fontWeight: 700, cursor: "pointer",
    boxShadow: "0 10px 30px var(--accent-glow)",
    transition: "all 0.3s ease",
    width: "100%",
  },
  joinDisclaimer: { fontSize: 12, color: "var(--text-tertiary)", marginTop: 24, lineHeight: 1.6 },
  offerOverlay: {
    position: "fixed", inset: 0, zIndex: 100,
    background: "rgba(0,0,0,0.9)", backdropFilter: "blur(12px)",
    display: "flex", alignItems: "center", justifyContent: "center",
    padding: 24,
  },
  offerCard: {
    background: "var(--bg-secondary)", borderRadius: 32,
    border: "1px solid var(--accent-primary)",
    padding: 48, maxWidth: 500, width: "100%",
    boxShadow: "0 0 100px var(--accent-glow)",
    textAlign: "center",
    animation: "fadeIn 0.8s ease-out",
  },
  documentUploadCard: {
    background: "var(--bg-secondary)", backdropFilter: "blur(12px)",
    borderRadius: 32, border: "1px solid var(--border)",
    padding: 40, maxWidth: 440, width: "100%",
    boxShadow: "0 20px 60px rgba(0,0,0,0.6)",
    textAlign: "center", zIndex: 100,
    animation: "fadeIn 0.4s ease-out",
  },
  offerBadge: {
    display: "inline-block",
    background: "rgba(59, 130, 246, 0.1)", color: "var(--accent-primary)",
    fontSize: 12, fontWeight: 800, borderRadius: 100,
    padding: "8px 20px", marginBottom: 24, letterSpacing: "0.05em",
    textTransform: "uppercase",
  },
  offerAmount: {
    fontSize: 56, fontWeight: 800, letterSpacing: "-0.04em",
    background: "linear-gradient(135deg, #fff 0%, #3b82f6 100%)",
    WebkitBackgroundClip: "text", WebkitTextFillColor: "transparent",
    marginBottom: 8,
  },
  offerRate: { color: "var(--text-secondary)", fontSize: 18, marginBottom: 24, fontWeight: 500 },
  offerExplanation: {
    fontSize: 15, color: "var(--text-secondary)", lineHeight: 1.6,
    margin: "0 0 32px", textAlign: "left",
    background: "rgba(255,255,255,0.02)", padding: 20, borderRadius: 16,
  },
  offerTitle: { fontSize: 32, fontWeight: 800, margin: "0 0 16px", fontFamily: "var(--font-heading)" },
  tenureRow: { display: "flex", gap: 12, justifyContent: "center", marginBottom: 24 },
  tenureBtn: {
    padding: "10px 24px", borderRadius: 12,
    border: "1px solid var(--border)",
    background: "var(--surface)", color: "var(--text-secondary)",
    cursor: "pointer", fontSize: 15, fontWeight: 600,
    transition: "all 0.2s",
  },
  tenureBtnActive: {
    background: "var(--accent-primary)", border: "1px solid var(--accent-primary)",
    color: "#fff", boxShadow: "0 0 15px var(--accent-glow)",
  },
  emiDisplay: {
    fontSize: 16, color: "var(--text-primary)", marginBottom: 32,
    padding: "16px", background: "rgba(255,255,255,0.03)", borderRadius: 12,
  },
  offerActions: {
    display: "flex", flexDirection: "column", gap: 16, alignItems: "center",
  },
  acceptBtn: {
    width: "100%", padding: "18px", borderRadius: 16,
    background: "var(--accent-primary)",
    border: "none", color: "#fff",
    fontSize: 18, fontWeight: 700, cursor: "pointer",
    boxShadow: "0 10px 30px var(--accent-glow)",
  },
  kfsLink: {
    fontSize: 13, color: "var(--text-tertiary)", textDecoration: "none",
    marginTop: 8, transition: "color 0.2s",
  },
  errorNotification: {
    position: "fixed", top: 100, left: "50%", transform: "translateX(-50%)",
    zIndex: 50, background: "rgba(239, 68, 68, 0.95)", backdropFilter: "blur(12px)",
    color: "#fff", padding: "16px 24px", borderRadius: 16,
    display: "flex", alignItems: "center", justifyContent: "space-between",
    gap: 20, maxWidth: 500, width: "90%",
    boxShadow: "0 10px 40px rgba(239, 68, 68, 0.3)",
    animation: "fadeIn 0.3s ease",
    fontSize: 15, fontWeight: 600,
  },
  errorClose: {
    background: "rgba(255,255,255,0.2)", border: "none", color: "#fff",
    width: 28, height: 28, borderRadius: "50%", cursor: "pointer",
    display: "flex", alignItems: "center", justifyContent: "center",
  },
  agentSpeechBubble: {
    position: "fixed", bottom: 120, left: "50%", transform: "translateX(-50%)",
    background: "rgba(10,10,10,0.85)", backdropFilter: "blur(16px)",
    borderRadius: 24, padding: "20px 32px",
    maxWidth: 700, width: "90%",
    border: "1px solid var(--accent-primary)",
    boxShadow: "0 20px 50px rgba(0,0,0,0.6)",
    animation: "fadeIn 0.5s ease-out",
    textAlign: "left",
  },
  recordingTimer: {
    display: "flex", alignItems: "center", gap: 8,
    background: "rgba(239, 68, 68, 0.1)",
    border: "1px solid rgba(239, 68, 68, 0.2)",
    padding: "6px 14px", borderRadius: 100,
    color: "var(--error)", fontSize: 12, fontWeight: 800,
    letterSpacing: "0.05em",
  },
  recordDot: {
    width: 8, height: 8, borderRadius: "50%",
    background: "var(--error)",
    animation: "pulse 1.5s infinite",
  },
  volBar: {
    width: 3, borderRadius: 2, background: "var(--accent-primary)",
    transition: "height 0.1s ease",
  },
  debugOverlay: {
    position: "absolute", top: 120, right: 32,
    background: "rgba(0,0,0,0.8)", color: "#fff",
    padding: "10px 20px", borderRadius: 12, fontSize: 12,
    maxWidth: 300, zIndex: 50, fontFamily: "monospace",
    border: "1px solid rgba(255,255,255,0.1)",
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
        <div style={{ fontSize: 48, marginBottom: 16 }}>✅</div>
        <h3 style={{ margin: 0, fontSize: 24, fontFamily: "var(--font-heading)" }}>Verification Started</h3>
        <p style={{ fontSize: 15, color: "var(--text-secondary)", margin: "12px 0 0", lineHeight: 1.6 }}>
          Your document is being processed by our AI vision agents. This usually takes 10-15 seconds.
        </p>
      </div>
    );
  }

  return (
    <div style={styles.documentUploadCard}>
      <div style={{ fontSize: 32, marginBottom: 20 }}>📄</div>
      <h3 style={{ margin: "0 0 12px 0", fontSize: 24, fontFamily: "var(--font-heading)" }}>Identity Document</h3>
      <p style={{ fontSize: 15, color: "var(--text-secondary)", margin: "0 0 24px 0", lineHeight: 1.6 }}>
        Please upload a high-quality photo of your original Aadhaar or PAN card.
      </p>
      
      <div className="file-input-wrapper">
        <input 
          type="file" 
          accept="image/*" 
          id="doc-upload"
          onChange={(e) => setFile(e.target.files[0])}
          style={{ display: "none" }}
        />
        <label htmlFor="doc-upload" className="btn-outline" style={{ display: "block", marginBottom: 24, cursor: "pointer", textAlign: "center" }}>
          {file ? file.name : "Choose File"}
        </label>
      </div>

      <button 
        className="btn-primary"
        style={{
          width: "100%",
          opacity: (!file || uploading) ? 0.6 : 1,
          cursor: (!file || uploading) ? "not-allowed" : "pointer"
        }}
        onClick={handleUpload}
        disabled={!file || uploading}
      >
        {uploading ? "Analyzing..." : "Confirm & Upload"}
      </button>
      
      <style jsx>{`
        .file-input-wrapper label {
          padding: 12px;
          border-radius: 12px;
          font-size: 14px;
        }
      `}</style>
    </div>
  );
}

const keyframes = `
  @keyframes pulse {
    0% { opacity: 1; transform: scale(1); }
    50% { opacity: 0.5; transform: scale(0.9); }
    100% { opacity: 1; transform: scale(1); }
  }
  @keyframes fadeIn {
    from { opacity: 0; transform: translateY(20px); }
    to { opacity: 1; transform: translateY(0); }
  }
`;
if (typeof document !== "undefined") {
  const style = document.createElement("style");
  style.innerHTML = keyframes;
  document.head.appendChild(style);
}

