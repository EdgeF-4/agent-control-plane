import { Fragment, useCallback, useEffect, useState } from "react";
import { api, ApiKey, ApiKeyCreated, Project, Tenant, User } from "../lib/api";
import { ago, microToUsd } from "../lib/format";

type Tab = "projects" | "users" | "tenants";
const GATE_SUITE = "assistant-smoke";

// Administration for the whole self-hosted deployment: projects and their budget,
// eval gate and ingest keys; users within the tenant; and (for a superadmin) the
// tenants themselves.
export default function AdminPage({ user }: { user: User }) {
  const [tab, setTab] = useState<Tab>("projects");
  return (
    <div className="wrap">
      <div className="admin-tabs">
        <button className={tab === "projects" ? "on" : ""} onClick={() => setTab("projects")}>Projects</button>
        <button className={tab === "users" ? "on" : ""} onClick={() => setTab("users")}>Users</button>
        {user.superadmin && (
          <button className={tab === "tenants" ? "on" : ""} onClick={() => setTab("tenants")}>Tenants</button>
        )}
      </div>
      {tab === "projects" && <ProjectsTab />}
      {tab === "users" && <UsersTab me={user} />}
      {tab === "tenants" && user.superadmin && <TenantsTab />}
    </div>
  );
}

// ------------------------------- projects ---------------------------------- //
function ProjectsTab() {
  const [projects, setProjects] = useState<Project[]>([]);
  const [slug, setSlug] = useState("");
  const [name, setName] = useState("");
  const [budget, setBudget] = useState(50);
  const [err, setErr] = useState("");
  const [expanded, setExpanded] = useState<string | null>(null);

  const load = useCallback(async () => setProjects(await api.projects()), []);
  useEffect(() => { load(); }, [load]);

  async function create() {
    setErr("");
    try {
      await api.createProject({ slug, name: name || slug, budget_usd: budget });
      setSlug(""); setName("");
      await load();
    } catch (e) {
      setErr(e instanceof Error ? e.message : "Create failed. Correct the project fields, then retry.");
    }
  }
  async function toggleGate(p: Project) {
    await api.setEvalGate(p.slug, p.eval_gate_suite ? null : GATE_SUITE);
    await load();
  }

  return (
    <div className="panel">
      <header><h2>Projects</h2><span className="sub">budget · eval gate · ingest keys</span></header>
      <div className="body flush">
        <table>
          <thead>
            <tr><th>Project</th><th style={{ textAlign: "right" }}>Budget</th><th>Eval gate</th><th></th></tr>
          </thead>
          <tbody>
            {projects.length === 0 && <tr><td colSpan={4}><div className="empty">No projects yet.</div></td></tr>}
            {projects.map((p) => (
              <Fragment key={p.slug}>
                <tr>
                  <td>
                    <div style={{ fontWeight: 600 }}>{p.name}</div>
                    <div className="mono">{p.slug}</div>
                  </td>
                  <td className="num">
                    <BudgetEditor project={p} onSaved={load} />
                  </td>
                  <td>
                    <button className={`pill-int ${p.eval_gate_suite ? "ok" : ""}`} onClick={() => toggleGate(p)}>
                      {p.eval_gate_suite ? `gated: ${p.eval_gate_suite}` : "no gate"}
                    </button>
                  </td>
                  <td style={{ textAlign: "right", whiteSpace: "nowrap" }}>
                    <button className="btn ghost" onClick={() => setExpanded(expanded === p.slug ? null : p.slug)}>Keys</button>
                    <button className="btn ghost" onClick={async () => { await api.deleteProject(p.slug); await load(); }}>Delete</button>
                  </td>
                </tr>
                {expanded === p.slug && (
                  <tr><td colSpan={4}><KeyManager slug={p.slug} /></td></tr>
                )}
              </Fragment>
            ))}
          </tbody>
        </table>
        <div className="admin-create">
          <input placeholder="slug" value={slug} onChange={(e) => setSlug(e.target.value)} />
          <input placeholder="name" value={name} onChange={(e) => setName(e.target.value)} />
          <input type="number" min={0} value={budget} onChange={(e) => setBudget(Number(e.target.value))} />
          <span className="sub">USD cap</span>
          <button className="btn" onClick={create} disabled={!slug}>Create project</button>
          {err && <span className="inline-err">{err}</span>}
        </div>
      </div>
    </div>
  );
}

function BudgetEditor({ project, onSaved }: { project: Project; onSaved: () => void }) {
  const [editing, setEditing] = useState(false);
  const [usd, setUsd] = useState(project.budget_limit_micro / 1_000_000);
  if (!editing) {
    return (
      <span className="budget-cell" onClick={() => setEditing(true)} title="click to edit">
        {microToUsd(project.budget_limit_micro)}
      </span>
    );
  }
  return (
    <span className="budget-edit">
      <input type="number" min={0} value={usd} onChange={(e) => setUsd(Number(e.target.value))} />
      <button className="btn" onClick={async () => { await api.updateProject(project.slug, { budget_usd: usd }); setEditing(false); onSaved(); }}>✓</button>
    </span>
  );
}

function KeyManager({ slug }: { slug: string }) {
  const [keys, setKeys] = useState<ApiKey[]>([]);
  const [name, setName] = useState("");
  const [minted, setMinted] = useState<ApiKeyCreated | null>(null);

  const load = useCallback(async () => setKeys(await api.projectKeys(slug)), [slug]);
  useEffect(() => { load(); }, [load]);

  return (
    <div className="keymgr">
      {minted && (
        <div className="minted">
          <span className="sub">Copy this key now — it is not shown again:</span>
          <code>{minted.key}</code>
        </div>
      )}
      {keys.length === 0 && <div className="empty small">No ingest keys for this project.</div>}
      {keys.map((k) => (
        <div className="key-row" key={k.id}>
          <span className={`dot-state ${k.revoked_at ? "off" : "up"}`} />
          <span className="mono">{k.key_prefix}…</span>
          <span className="sub">{k.name || "unnamed"} · {k.revoked_at ? "revoked" : k.last_used_at ? `used ${ago(k.last_used_at)}` : "never used"}</span>
          {!k.revoked_at && (
            <button className="btn ghost" onClick={async () => { await api.revokeKey(slug, k.id); await load(); }}>Revoke</button>
          )}
        </div>
      ))}
      <div className="key-create">
        <input placeholder="key name (e.g. ci-runner)" value={name} onChange={(e) => setName(e.target.value)} />
        <button className="btn" onClick={async () => { const m = await api.createKey(slug, name); setMinted(m); setName(""); await load(); }}>Mint key</button>
      </div>
    </div>
  );
}

// -------------------------------- users ------------------------------------ //
function UsersTab({ me }: { me: User }) {
  const [users, setUsers] = useState<User[]>([]);
  const [email, setEmail] = useState("");
  const [name, setName] = useState("");
  const [password, setPassword] = useState("");
  const [role, setRole] = useState("member");
  const [err, setErr] = useState("");

  const load = useCallback(async () => setUsers(await api.users()), []);
  useEffect(() => { load(); }, [load]);

  async function create() {
    setErr("");
    try {
      await api.createUser({ email, password, name, role });
      setEmail(""); setName(""); setPassword("");
      await load();
    } catch (e) {
      setErr(e instanceof Error ? e.message : "Create failed. Correct the user fields, then retry.");
    }
  }

  return (
    <div className="panel">
      <header><h2>Users</h2><span className="sub">members of this tenant</span></header>
      <div className="body flush">
        <table>
          <thead><tr><th>User</th><th>Role</th><th></th></tr></thead>
          <tbody>
            {users.map((u) => (
              <tr key={u.id}>
                <td>
                  <div style={{ fontWeight: 600 }}>{u.email}</div>
                  <div className="mono">{u.name || "—"}{u.superadmin ? " · superadmin" : ""}</div>
                </td>
                <td>
                  <select value={u.role} disabled={u.id === me.id}
                          onChange={async (e) => { await api.updateUser(u.id, { role: e.target.value }); await load(); }}>
                    <option value="member">member</option>
                    <option value="admin">admin</option>
                  </select>
                </td>
                <td style={{ textAlign: "right" }}>
                  {u.id !== me.id && (
                    <button className="btn ghost" onClick={async () => { await api.deleteUser(u.id); await load(); }}>Delete</button>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
        <div className="admin-create">
          <input placeholder="email" value={email} onChange={(e) => setEmail(e.target.value)} />
          <input placeholder="name" value={name} onChange={(e) => setName(e.target.value)} />
          <input type="password" placeholder="password (min 8)" value={password} onChange={(e) => setPassword(e.target.value)} />
          <select value={role} onChange={(e) => setRole(e.target.value)}>
            <option value="member">member</option>
            <option value="admin">admin</option>
          </select>
          <button className="btn" onClick={create} disabled={!email || password.length < 8}>Add user</button>
          {err && <span className="inline-err">{err}</span>}
        </div>
      </div>
    </div>
  );
}

// ------------------------------- tenants ----------------------------------- //
function TenantsTab() {
  const [tenants, setTenants] = useState<Tenant[]>([]);
  const [slug, setSlug] = useState("");
  const [name, setName] = useState("");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [err, setErr] = useState("");

  const load = useCallback(async () => setTenants(await api.tenants()), []);
  useEffect(() => { load(); }, [load]);

  async function create() {
    setErr("");
    try {
      await api.createTenant({ slug, name: name || slug, admin_email: email, admin_password: password });
      setSlug(""); setName(""); setEmail(""); setPassword("");
      await load();
    } catch (e) {
      setErr(e instanceof Error ? e.message : "Create failed. Correct the tenant fields, then retry.");
    }
  }

  return (
    <div className="panel">
      <header><h2>Tenants</h2><span className="sub">isolated workspaces on this deployment</span></header>
      <div className="body flush">
        <table>
          <thead><tr><th>Tenant</th><th className="num">Users</th><th className="num">Projects</th></tr></thead>
          <tbody>
            {tenants.map((t) => (
              <tr key={t.id}>
                <td><div style={{ fontWeight: 600 }}>{t.name}</div><div className="mono">{t.slug}</div></td>
                <td className="num">{t.user_count}</td>
                <td className="num">{t.project_count}</td>
              </tr>
            ))}
          </tbody>
        </table>
        <div className="admin-create">
          <input placeholder="slug" value={slug} onChange={(e) => setSlug(e.target.value)} />
          <input placeholder="name" value={name} onChange={(e) => setName(e.target.value)} />
          <input placeholder="admin email" value={email} onChange={(e) => setEmail(e.target.value)} />
          <input type="password" placeholder="admin password (min 8)" value={password} onChange={(e) => setPassword(e.target.value)} />
          <button className="btn" onClick={create} disabled={!slug || password.length < 8}>Create tenant</button>
          {err && <span className="inline-err">{err}</span>}
        </div>
      </div>
    </div>
  );
}
