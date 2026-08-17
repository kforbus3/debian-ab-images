import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import { Boxes, KeyRound } from "lucide-react";
import { useAuth } from "../lib/auth";
import { api, apiError } from "../lib/api";
import { Button, Card, Input, Label } from "../components/ui";

type Methods = { password: boolean; oidc: { enabled: boolean; display_name: string } };

export default function Login() {
  const { login, ssoExchange, authed } = useAuth();
  const navigate = useNavigate();
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [methods, setMethods] = useState<Methods | null>(null);
  if (authed) navigate("/", { replace: true });

  useEffect(() => {
    // What to render is the server's call: the SSO button only exists when
    // OIDC is configured. If the request fails, the password form alone is
    // the right fallback — it always works.
    api.get("/auth/methods")
      .then((r) => setMethods(r.data))
      .catch(() => setMethods({ password: true, oidc: { enabled: false, display_name: "" } }));

    // The OIDC callback lands here with the outcome in the URL fragment —
    // never sent to the server, so no access log sees it. #sso=<code> is a
    // one-time code traded for the session; #sso_error=<why> is a refusal
    // (unmapped groups, disabled account, username collision) to show.
    const frag = new URLSearchParams(window.location.hash.slice(1));
    const code = frag.get("sso");
    const ssoError = frag.get("sso_error");
    if (code || ssoError) window.history.replaceState(null, "", "/login");
    if (ssoError) setError(ssoError);
    if (code) {
      setBusy(true);
      ssoExchange(code)
        .then(() => navigate("/", { replace: true }))
        .catch((err) => setError(apiError(err)))
        .finally(() => setBusy(false));
    }
  }, []);

  async function submit(e: React.FormEvent) {
    e.preventDefault(); setError(""); setBusy(true);
    try { await login(username, password); navigate("/", { replace: true }); }
    catch (err) { setError(apiError(err)); } finally { setBusy(false); }
  }
  return (
    <div className="flex h-full items-center justify-center px-4">
      <Card className="w-full max-w-sm p-8">
        <div className="mb-6 flex flex-col items-center gap-2 text-center">
          <Boxes className="text-brand-400" size={32} />
          <h1 className="text-lg font-semibold">Flipside</h1>
          <p className="text-xs text-zinc-500">Sign in to manage images & provisioning</p>
        </div>
        {methods?.oidc.enabled && (
          <div className="mb-4 space-y-4">
            <Button type="button" variant="secondary" className="w-full" loading={busy}
                    onClick={() => { window.location.href = "/api/auth/oidc/login"; }}>
              <KeyRound size={14} /> {methods.oidc.display_name || "Single sign-on"}
            </Button>
            <div className="flex items-center gap-3 text-xs text-zinc-600">
              <div className="h-px flex-1 bg-zinc-800" />or<div className="h-px flex-1 bg-zinc-800" />
            </div>
          </div>
        )}
        <form onSubmit={submit} className="space-y-4">
          <div><Label>Username</Label><Input value={username} onChange={(e) => setUsername(e.target.value)} placeholder="admin" autoComplete="username" autoFocus required /></div>
          <div><Label>Password</Label><Input type="password" value={password} onChange={(e) => setPassword(e.target.value)} autoComplete="current-password" required /></div>
          {error && <p className="text-sm text-red-400">{error}</p>}
          <Button type="submit" loading={busy} className="w-full">Sign in</Button>
        </form>
      </Card>
    </div>
  );
}
