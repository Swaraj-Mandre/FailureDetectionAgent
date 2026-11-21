// The question box at the bottom of the Ask page.
import { useEffect, useRef, useState } from "react";
import { ArrowUp, FlaskConical, ShieldOff, X } from "lucide-react";
import { useSettings } from "../lib/settings";

export function Composer({ busy, onAsk }: { busy: boolean; onAsk: (question: string) => void }) {
  const { setup, fault, setFault, monitorOn, setMonitorOn } = useSettings();
  const [text, setText] = useState("");
  const box = useRef<HTMLTextAreaElement>(null);

  useEffect(() => {
    const el = box.current;
    if (!el) return;
    el.style.height = "auto";
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
      <div className="composer">
        <textarea
          ref={box}
          rows={1}
          value={text}
          placeholder="Ask about customers, products, orders or revenue"
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
