import { createContext, useContext, useEffect, useState, ReactNode } from "react";
import { api, tokens } from "./api";

export type Role = "viewer" | "operator" | "admin";
const RANK: Record<Role, number> = { viewer: 0, operator: 1, admin: 2 };
export const roleAtLeast = (role: Role, min: Role) => RANK[role] >= RANK[min];

interface AuthCtx {
  authed: boolean;
  loading: boolean;
  username: string;
  role: Role;
  // What the UI offers must follow what the API enforces: mutating controls
  // need canOperate, admin pages need isAdmin. Both come from /auth/check, so
  // they cannot drift from the backend's own answer.
  canOperate: boolean;
  isAdmin: boolean;
  login: (username: string, password: string) => Promise<void>;
  // SSO callback handoff: trade the one-time code from the URL fragment for
  // the session token, stored exactly like a password login's.
  ssoExchange: (code: string) => Promise<void>;
  logout: () => void;
}
const Ctx = createContext<AuthCtx>(null as any);

export function AuthProvider({ children }: { children: ReactNode }) {
  const [authed, setAuthed] = useState(false);
  const [loading, setLoading] = useState(true);
  const [username, setUsername] = useState("");
  const [role, setRole] = useState<Role>("viewer");

  useEffect(() => {
    if (!tokens.value) { setLoading(false); return; }
    api.get("/auth/check")
      .then((r) => { setUsername(r.data.username); setRole(r.data.role); setAuthed(true); })
      .catch(() => tokens.clear())
      .finally(() => setLoading(false));
  }, []);

  async function login(user: string, password: string) {
    const form = new URLSearchParams({ username: user, password });
    const { data } = await api.post("/auth/login", form, { headers: { "Content-Type": "application/x-www-form-urlencoded" } });
    tokens.set(data.access_token);
    setUsername(data.username); setRole(data.role); setAuthed(true);
  }
  async function ssoExchange(code: string) {
    const { data } = await api.post("/auth/oidc/exchange", { code });
    tokens.set(data.access_token);
    setUsername(data.username); setRole(data.role); setAuthed(true);
  }
  function logout() {
    // Revoke server-side first: clearing localStorage alone would leave the
    // session valid for anyone who has seen the token.
    api.post("/auth/logout").catch(() => {});
    tokens.clear(); setAuthed(false); setUsername(""); setRole("viewer");
  }

  return <Ctx.Provider value={{
    authed, loading, username, role,
    canOperate: roleAtLeast(role, "operator"), isAdmin: role === "admin",
    login, ssoExchange, logout,
  }}>{children}</Ctx.Provider>;
}
export const useAuth = () => useContext(Ctx);
