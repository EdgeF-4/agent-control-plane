import { useEffect, useState } from "react";
import { ACTIONABLE_ERROR_EVENT, api, getToken, setToken, User } from "./lib/api";
import Dashboard from "./components/Dashboard";
import Login from "./components/Login";

export default function App() {
  const [user, setUser] = useState<User | null>(null);
  const [ready, setReady] = useState(false);
  const [actionableError, setActionableError] = useState("");

  useEffect(() => {
    const showError = (event: Event) => {
      setActionableError((event as CustomEvent<string>).detail);
    };
    window.addEventListener(ACTIONABLE_ERROR_EVENT, showError);
    return () => window.removeEventListener(ACTIONABLE_ERROR_EVENT, showError);
  }, []);

  useEffect(() => {
    if (!getToken()) {
      setReady(true);
      return;
    }
    api
      .me()
      .then(setUser)
      .catch(() => setToken(null))
      .finally(() => setReady(true));
  }, []);

  if (!ready) return null;
  const notice = actionableError ? (
    <div className="actionable-error" role="alert">
      <span>{actionableError}</span>
      <button onClick={() => setActionableError("")} aria-label="Dismiss error">Dismiss</button>
    </div>
  ) : null;
  if (!user) {
    return (
      <>
        {notice}
        <Login
          onAuthed={(u, token) => {
            setToken(token);
            setUser(u);
          }}
        />
      </>
    );
  }
  return (
    <>
      {notice}
      <Dashboard
        user={user}
        onLogout={() => {
          setToken(null);
          setUser(null);
        }}
      />
    </>
  );
}
