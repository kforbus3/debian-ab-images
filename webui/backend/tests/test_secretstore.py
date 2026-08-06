"""The secrets-manager integration, against a fake KV v2 server.

Run it directly -- no pytest, no network, no store:

    cd webui/backend && python tests/test_secretstore.py

What it is really guarding is ordering. The passphrase for an encrypted image
must reach the secrets manager *before* the build starts, and a store that
refuses it must stop the build rather than let one proceed whose recovery key
was never persisted. That is not visible in a diff and has no runtime symptom
until the day someone needs the key, so it is asserted here instead.
"""
import json, os, sys, tempfile, threading
from http.server import BaseHTTPRequestHandler, HTTPServer

PROJ = tempfile.mkdtemp()
os.makedirs(os.path.join(PROJ, "output"), exist_ok=True)
os.environ.update(PROJECT_DIR=PROJ, STATIC_DIR="/tmp/none",
                  ADMIN_PASSWORD="ci", SECRET_KEY="ci-secret-key")
os.environ.pop("BAO_TOKEN", None); os.environ.pop("VAULT_TOKEN", None)

STORE = {}          # path -> data dict
CALLS = []


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a): pass

    def _send(self, code, obj):
        body = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        CALLS.append(("GET", self.path, self.headers.get("X-Vault-Token")))
        p = self.path.split("?")[0]
        if p == "/v1/sys/health":
            return self._send(200, {"sealed": False, "version": "2.1.0"})
        if self.headers.get("X-Vault-Token") != "root-token":
            return self._send(403, {"errors": ["permission denied"]})
        if p == "/v1/auth/token/lookup-self":
            return self._send(200, {"data": {"policies": ["default", "ab-images"]}})
        if p == "/v1/secret/config":
            return self._send(200, {"data": {"max_versions": 10, "cas_required": False}})
        if p.startswith("/v1/secret/data/"):
            key = p[len("/v1/secret/data/"):]
            if key not in STORE:
                return self._send(404, {"errors": []})
            return self._send(200, {"data": {"data": STORE[key]}})
        if p.startswith("/v1/secret/metadata/") and "list=true" in self.path:
            prefix = p[len("/v1/secret/metadata/"):]
            keys = [k.split("/")[-1] for k in STORE if k.startswith(prefix + "/")]
            return self._send(200, {"data": {"keys": keys}})
        return self._send(404, {"errors": []})

    def do_POST(self):
        n = int(self.headers.get("Content-Length") or 0)
        body = json.loads(self.rfile.read(n) or b"{}")
        CALLS.append(("POST", self.path, self.headers.get("X-Vault-Token")))
        if self.path == "/v1/auth/approle/login":
            if body.get("role_id") == "role-1" and body.get("secret_id") == "sid-1":
                return self._send(200, {"auth": {"client_token": "root-token", "lease_duration": 600}})
            return self._send(400, {"errors": ["invalid role or secret id"]})
        if self.headers.get("X-Vault-Token") != "root-token":
            return self._send(403, {"errors": ["permission denied"]})
        if self.path.startswith("/v1/secret/data/"):
            STORE[self.path[len("/v1/secret/data/"):]] = body["data"]
            return self._send(200, {"data": {"version": 1}})
        return self._send(404, {"errors": []})


srv = HTTPServer(("127.0.0.1", 0), Handler)
ADDR = f"http://127.0.0.1:{srv.server_port}"
threading.Thread(target=srv.serve_forever, daemon=True).start()

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
from fastapi.testclient import TestClient
from app.main import app
from app import orchestrator as orch, secretstore
from app.jobs import jobs

client = TestClient(app)
tok = client.post("/api/auth/login", data={"username": "admin", "password": "ci"}).json()["access_token"]
H = {"Authorization": f"Bearer {tok}"}
ok = fail = 0


def check(name, cond, extra=""):
    global ok, fail
    if cond:
        ok += 1; print(f"  PASS  {name}")
    else:
        fail += 1; print(f"  FAIL  {name} {extra}")


print("\n== auth ==")
for path in ("/api/secrets/config", "/api/secrets/entries", "/api/secrets/passphrase/x.img"):
    check(f"{path} needs auth", client.get(path).status_code == 401)

print("\n== configuration ==")
r = client.get("/api/secrets/config", headers=H).json()
check("defaults to disabled", r["configured"] is False and r["config"]["enabled"] is False)
check("token never returned", "token" not in r["config"] and r["config"]["token_set"] is False)

CFG = {"enabled": True, "provider": "openbao", "address": ADDR, "mount": "secret",
       "path_prefix": "debian-ab-images", "auth_method": "token", "token": "root-token"}
r = client.post("/api/secrets/test", json=CFG, headers=H).json()
check("test connection succeeds", r["ok"] is True, r)
check("test reports the KV mount", r.get("info", {}).get("mount") == "secret", r)

bad = client.post("/api/secrets/test", json={**CFG, "token": "wrong"}, headers=H).json()
check("bad token fails with an actionable message",
      bad["ok"] is False and "refused the credential" in bad["error"], bad)
unreachable = client.post("/api/secrets/test", json={**CFG, "address": "http://127.0.0.1:1"},
                          headers=H).json()
check("unreachable store reports the address",
      unreachable["ok"] is False and "could not reach" in unreachable["error"], unreachable)

r = client.put("/api/secrets/config", json={**CFG, "address": "http://127.0.0.1:1"}, headers=H)
check("enabling an unreachable store is refused", r.status_code == 400, r.text)
r = client.get("/api/secrets/config", headers=H).json()
check("...and nothing was saved", r["configured"] is False, r)

r = client.put("/api/secrets/config", json=CFG, headers=H)
check("saving a working store succeeds", r.status_code == 200, r.text)
check("...and it reads back configured", r.json()["configured"] is True)
check("config file is 0600",
      oct(os.stat(secretstore.config_path()).st_mode & 0o777) == "0o600")

r = client.put("/api/secrets/config", json={**CFG, "token": "", "namespace": "ns1"}, headers=H)
check("blank token keeps the saved one", r.status_code == 200 and r.json()["config"]["token_set"], r.text)
check("...and other fields still update", r.json()["config"]["namespace"] == "ns1")
client.put("/api/secrets/config", json={**CFG, "namespace": ""}, headers=H)

print("\n== naming ==")
check("compression suffix stripped (.zst)", secretstore.secret_name("a-ab.img.zst") == "a-ab.img")
check("compression suffix stripped (.gz)", secretstore.secret_name("a-ab.img.gz") == "a-ab.img")
check("uncompressed name unchanged", secretstore.secret_name("a-ab.img") == "a-ab.img")
p = secretstore.generate_passphrase()
check("passphrase is 256-bit URL-safe", len(p) >= 40 and all(c.isalnum() or c in "-_" for c in p), p)

print("\n== build stores the passphrase before starting ==")
started = {}


async def fake_start(**kw):
    started.update(kw)

    class J:
        id = "job-1"; status = "running"
        def public(self): return {"id": self.id, "status": self.status}
    return J()


jobs.start = fake_start
orch.preflight = lambda: []

OPTS = {"distro": "debian", "suite": "trixie", "arch": "amd64", "password": "pw",
        "encrypt": True, "unlock": "tpm2", "store_passphrase": True}
r = client.post("/api/builds", json=OPTS, headers=H)
check("build accepted without a typed passphrase", r.status_code == 200, r.text)
stored_at = r.json().get("passphrase_stored_at")
check("response reports where it was stored",
      stored_at == "secret/debian-ab-images/debian-trixie-amd64-ab.img", stored_at)
entry = STORE.get("debian-ab-images/debian-trixie-amd64-ab.img")
check("the store actually holds it", bool(entry and entry.get("passphrase")), STORE)
check("stored metadata identifies the image",
      entry and entry.get("unlock") == "tpm2" and entry.get("arch") == "amd64", entry)
check("the builder gets the same passphrase in LUKS_PASS",
      started.get("env", {}).get("LUKS_PASS") == entry["passphrase"])
check("the passphrase is not on the command line",
      entry["passphrase"] not in " ".join(started.get("cmd", [])))

print("\n== a store that will not take it stops the build ==")
STORE.clear(); started.clear()
client.put("/api/secrets/config", json={**CFG, "token": "root-token"}, headers=H)
_orig = secretstore.KVv2Store.put
secretstore.KVv2Store.put = lambda self, n, d: (_ for _ in ()).throw(
    secretstore.SecretStoreError("permission denied"))
r = client.post("/api/builds", json=OPTS, headers=H)
check("build refused when the passphrase cannot be stored", r.status_code == 502, r.status_code)
check("...with an explanation of why nothing was built",
      "not started" in r.json()["detail"], r.text)
check("...and no job was launched", not started)
secretstore.KVv2Store.put = _orig

print("\n== store_passphrase with no store configured ==")
client.put("/api/secrets/config", json={**CFG, "enabled": False}, headers=H)
r = client.post("/api/builds", json=OPTS, headers=H)
check("refused with the fix in the message",
      r.status_code == 400 and "Secrets Manager" in r.json()["detail"], r.text)
r = client.post("/api/builds", json={**OPTS, "store_passphrase": False}, headers=H)
check("typed-passphrase path still enforced", r.status_code == 400, r.text)
r = client.post("/api/builds", json={**OPTS, "store_passphrase": False,
                                     "luks_passphrase": "typed"}, headers=H)
check("typed passphrase still works", r.status_code == 200, r.text)
check("...and no store write happened", not STORE)
check("...and no stored-at is reported", not r.json().get("passphrase_stored_at"))

print("\n== unencrypted builds are untouched ==")
started.clear()
r = client.post("/api/builds", json={"distro": "debian", "suite": "trixie", "password": "pw"}, headers=H)
check("plain build unaffected", r.status_code == 200, r.text)
check("...LUKS_PASS not set", "LUKS_PASS" not in started.get("env", {}))

print("\n== retrieval ==")
client.put("/api/secrets/config", json=CFG, headers=H)
r = client.post("/api/builds", json=OPTS, headers=H)
secret = STORE["debian-ab-images/debian-trixie-amd64-ab.img"]["passphrase"]
r = client.get("/api/secrets/entries", headers=H).json()
check("entries lists the image", r["entries"] == ["debian-trixie-amd64-ab.img"], r)
r = client.get("/api/secrets/passphrase/debian-trixie-amd64-ab.img", headers=H)
check("reveal returns the passphrase", r.json().get("passphrase") == secret, r.text)
r = client.get("/api/secrets/passphrase/debian-trixie-amd64-ab.img.zst", headers=H)
check("reveal works via the compressed name", r.json().get("passphrase") == secret, r.text)
r = client.get("/api/secrets/passphrase/nope.img", headers=H)
check("unknown image is 404", r.status_code == 404, r.text)
r = client.get("/api/secrets/passphrase/..%2F..%2Fetc%2Fpasswd", headers=H)
check("path traversal refused", r.status_code in (400, 404), r.status_code)

print("\n== bundle build reads it back ==")
img = "debian-trixie-amd64-ab.img.zst"
open(os.path.join(PROJ, "output", img), "w").write("x")
with open(os.path.join(PROJ, "output", img + ".json"), "w") as f:
    json.dump({"encrypted": True, "unlock": "tpm2"}, f)
started.clear()
r = client.post("/api/bundles/build", json={"image": img}, headers=H)
check("no passphrase prompt needed", r.status_code == 200, r.text)
check("...the stored one reaches the builder",
      started.get("env", {}).get("LUKS_PASS") == secret, started.get("env"))

STORE.clear()
started.clear()
r = client.post("/api/bundles/build", json={"image": img}, headers=H)
check("still refuses when the store has no entry", r.status_code == 400, r.text)
check("...and says a passphrase is needed", "passphrase is needed" in r.json()["detail"], r.text)

print("\n== approle ==")
r = client.post("/api/secrets/test", json={**CFG, "auth_method": "approle", "token": "",
                                           "role_id": "role-1", "secret_id": "sid-1"},
                headers=H).json()
check("approle login works", r["ok"] is True, r)
r = client.post("/api/secrets/test", json={**CFG, "auth_method": "approle", "token": "",
                                           "role_id": "role-1", "secret_id": "wrong"},
                headers=H).json()
check("bad approle fails", r["ok"] is False, r)

print(f"\n{ok} passed, {fail} failed")
sys.exit(1 if fail else 0)
