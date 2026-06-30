import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { Boxes } from "lucide-react";
import { useAuth } from "../lib/auth";
import { apiError } from "../lib/api";
import { Button, Card, Input, Label } from "../components/ui";

export default function Login() {
  const { login, authed } = useAuth();
  const navigate = useNavigate();
  const [password, setPassword] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  if (authed) navigate("/", { replace: true });

  async function submit(e: React.FormEvent) {
    e.preventDefault(); setError(""); setBusy(true);
    try { await login(password); navigate("/", { replace: true }); }
    catch (err) { setError(apiError(err)); } finally { setBusy(false); }
  }
  return (
    <div className="flex h-full items-center justify-center px-4">
      <Card className="w-full max-w-sm p-8">
        <div className="mb-6 flex flex-col items-center gap-2 text-center">
          <Boxes className="text-brand-400" size={32} />
          <h1 className="text-lg font-semibold">Debian A/B Images</h1>
          <p className="text-xs text-zinc-500">Sign in to manage images & provisioning</p>
        </div>
        <form onSubmit={submit} className="space-y-4">
          <div><Label>Password</Label><Input type="password" value={password} onChange={(e) => setPassword(e.target.value)} autoFocus required /></div>
          {error && <p className="text-sm text-red-400">{error}</p>}
          <Button type="submit" loading={busy} className="w-full">Sign in</Button>
        </form>
      </Card>
    </div>
  );
}
