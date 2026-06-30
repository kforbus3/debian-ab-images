import axios from "axios";

const KEY = "dab_token";

export const tokens = {
  get value() { return localStorage.getItem(KEY); },
  set(t: string) { localStorage.setItem(KEY, t); },
  clear() { localStorage.removeItem(KEY); },
};

export const api = axios.create({ baseURL: "/api" });
api.interceptors.request.use((c) => {
  const t = tokens.value;
  if (t) c.headers.Authorization = `Bearer ${t}`;
  return c;
});
api.interceptors.response.use(
  (r) => r,
  (e) => {
    if (e.response?.status === 401 && tokens.value && window.location.pathname !== "/login") {
      tokens.clear();
      window.location.href = "/login";
    }
    return Promise.reject(e);
  }
);

export function apiError(e: unknown): string {
  if (axios.isAxiosError(e)) return (e.response?.data as any)?.detail || e.message || "Request failed";
  return e instanceof Error ? e.message : "Unexpected error";
}

export function fmtBytes(n: number): string {
  if (n >= 1e9) return (n / 1e9).toFixed(2) + " GB";
  if (n >= 1e6) return (n / 1e6).toFixed(1) + " MB";
  if (n >= 1e3) return (n / 1e3).toFixed(0) + " KB";
  return n + " B";
}
