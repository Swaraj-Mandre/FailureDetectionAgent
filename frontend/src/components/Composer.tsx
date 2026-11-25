// The question box at the bottom of the Ask page, with the "+" that adds a shop's own data.
import { useEffect, useRef, useState } from "react";
import { ArrowUp, FlaskConical, Plus, ShieldOff, X } from "lucide-react";
import { useSettings } from "../lib/settings";
import { ACCEPT } from "./UploadDialog";

export function Composer({ busy, onAsk }: { busy: boolean; onAsk: (question: string) => void }) {
  const { setup, fault, setFault, monitorOn, setMonitorOn, shop, openUpload } = useSettings();
  const [text, setText] = useState("");
  const box = useRef<HTMLTextAreaElement>(null);
  const picker = useRef<HTMLInputElement>(null);
  const [over, setOver] = useState(false);

  useEffect(() => {
    const el = box.current;
    if (!el) return;
    el.style.height = "auto";
    if (!text) return;   // a long placeholder must not stretch the empty box
    el.style.height = `${Math.min(el.scrollHeight, 160)}px`;
  }, [text]);

  const submit = () => {
    const question = text.trim();
    if (!question || busy) return;
    onAsk(question);
    setText("");
  };

  const faultName = setup?.faults.find((f) => f.id === fault)?.name;

  return (
    <div className="composer-wrap">
      {(fault || !monitorOn) && (
        <div className="armed">
          {fault && (
            <span className="tag"><FlaskConical size={13} /> Next question gets: {faultName}
              <button onClick={() => setFault(null)} aria-label="Remove the test failure"><X size={12} /></button>
            </span>
          )}
          {!monitorOn && (
            <span className="tag tag-warn"><ShieldOff size={13} /> Monitor is off
              <button onClick={() => setMonitorOn(true)} aria-label="Turn the Monitor back on"><X size={12} /></button>
            </span>
          )}
        </div>
      )}
      <div className={`composer ${over ? "over" : ""}`}
        onDragOver={(e) => { if (e.dataTransfer.types.includes("Files")) { e.preventDefault(); setOver(true); } }}
        onDragLeave={() => setOver(false)}
        onDrop={(e) => {
          if (!e.dataTransfer.files.length) return;
          e.preventDefault();
          setOver(false);
          if (!busy) openUpload([...e.dataTransfer.files]);
        }}>
        <input ref={picker} type="file" multiple hidden accept={ACCEPT} aria-label="Shop data files"
          onChange={(e) => { const files = [...(e.target.files ?? [])]; e.target.value = ""; if (files.length) openUpload(files); }} />
        <button className="add" onClick={() => picker.current?.click()} disabled={busy}
          aria-label="Add your shop data" title="Add your shop data: Excel, CSV or SQL">
          <Plus size={19} />
        </button>
        <textarea
          ref={box}
          rows={1}
          value={text}
          placeholder={shop?.uploaded ? `Ask about ${shop.name}` : "Ask about customers, products, orders or revenue"}
          onChange={(e) => setText(e.target.value)}
          onKeyDown={(e) => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); submit(); } }}
          aria-label="Your question"
        />
        <button className="send" onClick={submit} disabled={busy || !text.trim()} aria-label="Ask">
          <ArrowUp size={18} />
        </button>
      </div>
    </div>
  );
}
