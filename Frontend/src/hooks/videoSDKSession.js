/**
 * useVideoSDKSession.js
 * Handles: session token resolution, VideoSDK token fetch,
 * and graceful error/loading states.
 * Used by the Join page to bootstrap the VideoCallScreen.
 */

import { useState, useEffect } from "react";

const joinRequests = new Map();

function getJoinRequest(sessionToken) {
  if (!joinRequests.has(sessionToken)) {
    const request = fetch(`/api/v1/session/${sessionToken}/join`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
      }).then(async (res) => {
        const data = await res.json().catch(() => null);
        if (!res.ok) {
          throw new Error(data?.detail || "Session expired or invalid");
        }
        return data;
      }).catch((err) => {
        joinRequests.delete(sessionToken);
        throw err;
      });
    joinRequests.set(sessionToken, request);
  }

  return joinRequests.get(sessionToken);
}

export function useVideoSDKSession(sessionToken) {
  const [state, setState] = useState({
    loading:        true,
    error:          null,
    callId:         null,
    roomId:         null,
    videoSdkToken:  null,
    participantId:  null,
    stage:          "INIT",
  });

  useEffect(() => {
    if (!sessionToken) {
      setState((s) => ({ ...s, loading: false, error: "Missing session token" }));
      return;
    }

    let cancelled = false;

    async function bootstrap() {
      try {
        // 1. Join session → get VideoSDK credentials
        const data = await getJoinRequest(sessionToken);

        if (!cancelled) {
          setState({
            loading:       false,
            error:         null,
            callId:        data.call_id,
            roomId:        data.videosdk_room_id,
            videoSdkToken: data.videosdk_token,
            participantId: data.participant_id,
            stage:         data.stage || "GREETING_CONSENT",
          });
        }
      } catch (err) {
        if (!cancelled) {
          setState((s) => ({
            ...s,
            loading: false,
            error: err.message || "Unable to join session",
          }));
        }
      }
    }

    bootstrap();
    return () => { cancelled = true; };
  }, [sessionToken]);

  return state;
}
