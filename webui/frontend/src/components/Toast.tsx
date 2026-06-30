import { createContext, useCallback, useContext, useState, ReactNode } from "react";
import { CheckCircle2, XCircle, Info, X } from "lucide-react";

type Kind = "success" | "error" | "info";
const Ctx = createContext<{ success:(m:string)=>void; error:(m:string)=>void; info:(m:string)=>void }>(null as any);
let n = 0;

export function ToastProvider({ children }: { children: ReactNode }) {
  const [items, setItems] = useState<{ id:number; kind:Kind; msg:string }[]>([]);
  const push = useCallback((kind: Kind, msg: string) => {
    const id = ++n;
    setItems((s) => [...s, { id, kind, msg }]);
    setTimeout(() => setItems((s) => s.filter((x) => x.id !== id)), 4500);
  }, []);
  const icon = { success: CheckCircle2, error: XCircle, info: Info };
  const color = {
    success: "border-emerald-500/40 bg-emerald-500/10 text-emerald-200",
    error: "border-red-500/40 bg-red-500/10 text-red-200",
    info: "border-brand-500/40 bg-brand-500/10 text-brand-200",
  };
  return (
    <Ctx.Provider value={{ success:(m)=>push("success",m), error:(m)=>push("error",m), info:(m)=>push("info",m) }}>
      {children}
      <div className="fixed bottom-4 right-4 z-50 flex w-80 flex-col gap-2">
        {items.map((t) => { const I = icon[t.kind]; return (
          <div key={t.id} className={`flex items-start gap-2 rounded-lg border px-3 py-2.5 text-sm shadow-lg ${color[t.kind]}`}>
            <I size={16} className="mt-0.5 shrink-0" /><span className="flex-1 break-words">{t.msg}</span>
            <button onClick={() => setItems((s) => s.filter((y) => y.id !== t.id))}><X size={14} className="opacity-60" /></button>
          </div>); })}
      </div>
    </Ctx.Provider>
  );
}
export const useToast = () => useContext(Ctx);
