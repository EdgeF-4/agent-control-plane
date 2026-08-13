import { FormEvent, useState } from "react";
import { api, User } from "../lib/api";

export default function Login({ onAuthed }: { onAuthed: (u: User, token: string) => void }) {
  const [email, setEmail] = useState("admin@acme.test");
  const [password, setPassword] = useState("");
  const [err, setErr] = useState("");
  const [busy, setBusy] = useState(false);

  async function submit(e: FormEvent) {
    e.preventDefault();
    setBusy(true);
    setErr("");
    try {
      const r = await api.login(email, password);
      onAuthed(r.user, r.access_token);
    } catch (e) {
      setErr(e instanceof Error ? e.message : "Login failed. Check the credentials, then retry.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="login-wrap">
      <form className="login-card" onSubmit={submit}>
        <div className="brand">
          <span className="glyph">⬢</span> Agent Control Plane
        </div>
        <p className="tag">
          Self-hosted governance, cost control and a tamper-evident audit trail
          for the agents you run in production.
        </p>
        <div className="field">
          <label>Email</label>
          <input value={email} onChange={(e) => setEmail(e.target.value)} autoComplete="username" />
        </div>
        <div className="field">
          <label>Password</label>
          <input
            type="password"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            autoComplete="current-password"
          />
        </div>
        <button className="btn-primary" disabled={busy || !password}>
          {busy ? "Signing in…" : "Sign in"}
        </button>
        <div className="login-err">{err}</div>
        <div className="hint">Everything stays on your own infrastructure. No data leaves this server.</div>
      </form>
    </div>
  );
}
