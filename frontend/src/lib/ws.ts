import { useEffect, useRef } from "react";
import { reportActionableError } from "./api";

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
    let intentionalClose = false;
    const proto = location.protocol === "https:" ? "wss" : "ws";
    const ws = new WebSocket(
      `${proto}://${location.host}/api/v1/live?token=${encodeURIComponent(token)}`
    );
    ws.onmessage = (e) => {
      try {
        cb.current(JSON.parse(e.data));
      } catch {
        reportActionableError(
          "The live feed returned a malformed update. Next: refresh the dashboard; " +
          "if it repeats, ask the operator to inspect the backend logs."
        );
      }
    };
    ws.onerror = () => {
      reportActionableError(
        "The live connection failed. Next: confirm the stack is running, then refresh the dashboard."
      );
    };
    ws.onclose = (event) => {
      if (!intentionalClose) {
        reportActionableError(
          event.reason ||
          "The live connection closed unexpectedly. Next: sign in again or refresh the dashboard."
        );
      }
    };
    return () => {
      intentionalClose = true;
      ws.close();
    };
  }, [token]);
}
