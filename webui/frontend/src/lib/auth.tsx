import { createContext, useContext, useEffect, useState, ReactNode } from "react";
import { api, tokens } from "./api";

interface AuthCtx {
  authed: boolean;
  loading: boolean;
  login: (password: string) => Promise<void>;
  logout: () => void;
}
const Ctx = createContext<AuthCtx>(null as any);

export function AuthProvider({ children }: { children: ReactNode }) {
  const [authed, setAuthed] = useState(false);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    if (!tokens.value) { setLoading(false); return; }
    api.get("/auth/check").then(() => setAuthed(true)).catch(() => tokens.clear()).finally(() => setLoading(false));
  }, []);

  async function login(password: string) {
    const form = new URLSearchParams({ username: "admin", password });
    const { data } = await api.post("/auth/login", form, { headers: { "Content-Type": "application/x-www-form-urlencoded" } });
    tokens.set(data.access_token);
    setAuthed(true);
  }
  function logout() { tokens.clear(); setAuthed(false); }

  return <Ctx.Provider value={{ authed, loading, login, logout }}>{children}</Ctx.Provider>;
}
export const useAuth = () => useContext(Ctx);
