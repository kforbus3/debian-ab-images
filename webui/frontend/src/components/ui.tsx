import { ReactNode, ButtonHTMLAttributes, InputHTMLAttributes, SelectHTMLAttributes } from "react";
import { clsx } from "clsx";
import { Loader2 } from "lucide-react";

export function Button({ variant="primary", size="md", loading, className, children, ...p }:
  ButtonHTMLAttributes<HTMLButtonElement> & { variant?:"primary"|"secondary"|"danger"|"ghost"; size?:"sm"|"md"; loading?:boolean }) {
  const v = {
    primary: "bg-brand-600 hover:bg-brand-500 text-white",
    secondary: "bg-zinc-800 hover:bg-zinc-700 text-zinc-100 border border-zinc-700",
    danger: "bg-red-600 hover:bg-red-500 text-white",
    ghost: "hover:bg-zinc-800 text-zinc-300",
  };
  const s = { sm: "px-2.5 py-1.5 text-xs", md: "px-3.5 py-2 text-sm" };
  return <button className={clsx("inline-flex items-center justify-center gap-1.5 rounded-lg font-medium transition disabled:opacity-50", v[variant], s[size], className)} disabled={loading||p.disabled} {...p}>
    {loading && <Loader2 size={14} className="animate-spin" />}{children}
  </button>;
}
export function Card({ className, children }: { className?: string; children: ReactNode }) {
  return <div className={clsx("rounded-xl border border-zinc-800 bg-zinc-900/60", className)}>{children}</div>;
}
export function Input({ className, ...p }: InputHTMLAttributes<HTMLInputElement>) {
  return <input className={clsx("w-full rounded-lg border border-zinc-700 bg-zinc-950 px-3 py-2 text-sm outline-none placeholder:text-zinc-500 focus:border-brand-500", className)} {...p} />;
}
export function Select({ className, children, ...p }: SelectHTMLAttributes<HTMLSelectElement>) {
  return <select className={clsx("w-full rounded-lg border border-zinc-700 bg-zinc-950 px-3 py-2 text-sm outline-none focus:border-brand-500", className)} {...p}>{children}</select>;
}
export function Label({ children }: { children: ReactNode }) {
  return <label className="mb-1 block text-xs font-medium text-zinc-400">{children}</label>;
}
export function Badge({ children, color="zinc" }: { children: ReactNode; color?: string }) {
  const m: Record<string,string> = {
    zinc:"bg-zinc-700/50 text-zinc-300", green:"bg-emerald-500/15 text-emerald-300",
    blue:"bg-sky-500/15 text-sky-300", amber:"bg-amber-500/15 text-amber-300",
    red:"bg-red-500/15 text-red-300", brand:"bg-brand-500/20 text-brand-400",
  };
  return <span className={clsx("inline-flex items-center rounded-full px-2 py-0.5 text-xs font-medium", m[color]||m.zinc)}>{children}</span>;
}
export function Spinner() { return <div className="flex items-center justify-center py-16 text-zinc-500"><Loader2 className="animate-spin" /></div>; }
export function PageHeader({ title, subtitle, actions }: { title:string; subtitle?:string; actions?:ReactNode }) {
  return <div className="mb-6 flex items-end justify-between gap-4">
    <div><h1 className="text-xl font-semibold">{title}</h1>{subtitle && <p className="mt-0.5 text-sm text-zinc-500">{subtitle}</p>}</div>
    {actions && <div className="flex gap-2">{actions}</div>}
  </div>;
}
export function LogView({ lines }: { lines: string[] }) {
  return <pre className="max-h-[28rem] overflow-auto rounded-lg border border-zinc-800 bg-black p-3 text-xs leading-relaxed text-zinc-300 whitespace-pre-wrap">
    {lines.length ? lines.join("\n") : "Waiting for output…"}
  </pre>;
}
