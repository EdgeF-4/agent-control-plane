import { useEffect, useState } from "react";
import { api, getToken, setToken, User } from "./lib/api";
import Dashboard from "./components/Dashboard";
import Login from "./components/Login";

export default function App() {
  const [user, setUser] = useState<User | null>(null);
  const [ready, setReady] = useState(false);

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
  if (!user) {
    return (
      <Login
        onAuthed={(u, token) => {
          setToken(token);
          setUser(u);
        }}
      />
    );
  }
  return (
    <Dashboard
      user={user}
      onLogout={() => {
        setToken(null);
        setUser(null);
      }}
    />
  );
}
