import { useEffect, useRef } from "react";

export interface LiveMessage {
  type: string;
  [key: string]: unknown;
}

// Subscribe to the tenant's live feed. The token is passed as a query param
// because browsers cannot set headers on a WebSocket handshake.
export function useLive(token: string, onMessage: (m: LiveMessage) => void) {
  const cb = useRef(onMessage);
  cb.current = onMessage;
  useEffect(() => {
    const proto = location.protocol === "https:" ? "wss" : "ws";
    const ws = new WebSocket(
      `${proto}://${location.host}/api/v1/live?token=${encodeURIComponent(token)}`
    );
    ws.onmessage = (e) => {
      try {
        cb.current(JSON.parse(e.data));
      } catch {
        /* ignore malformed frame */
      }
    };
    return () => ws.close();
  }, [token]);
}
