// The test lab: switch the Monitor off or plant a failure in the next question, to see the team react.
import { useEffect, useRef, useState } from "react";
import { FlaskConical, X } from "lucide-react";
import { useSettings } from "../lib/settings";
import { Switch } from "./Bits";

const FAULT_HELP: Record<string, string> = {
  corrupt_once: "The first result comes back 100 times too big, with no error.",
  corrupt: "Every result comes back 100 times too big.",
  garble_once: "The first query gets a syntax error.",
  flaky: "The database is busy twice, then works.",
  locked: "The database stays busy.",
  db_down: "The database cannot be reached.",
};

export function TestLab() {
  const { setup, monitorOn, setMonitorOn, fault, setFault } = useSettings();
  const [open, setOpen] = useState(false);
  const panel = useRef<HTMLDivElement>(null);
  const armed = !!fault || !monitorOn;

  useEffect(() => {
    if (!open) return;
    const close = (e: MouseEvent) => { if (panel.current && !panel.current.contains(e.target as Node)) setOpen(false); };
    const esc = (e: KeyboardEvent) => { if (e.key === "Escape") setOpen(false); };
    document.addEventListener("mousedown", close);
    document.addEventListener("keydown", esc);
    return () => { document.removeEventListener("mousedown", close); document.removeEventListener("keydown", esc); };
  }, [open]);

  return (
    <div className="lab" ref={panel}>
      <button className={`button button-quiet ${armed ? "lab-armed" : ""}`} onClick={() => setOpen(!open)} aria-expanded={open}>
        <FlaskConical size={16} /> Test lab{armed && <span className="lab-dot" />}
      </button>
      {open && (
        <div className="lab-panel" role="dialog" aria-label="Test lab">
          <div className="lab-head">
            <h3>Test lab</h3>
            <button className="icon-button" onClick={() => setOpen(false)} aria-label="Close"><X size={16} /></button>
          </div>
          <p className="lab-note">Break things on purpose and watch the team handle it. Nothing in the shop data changes.</p>

          <Switch on={monitorOn} onChange={setMonitorOn} label={monitorOn ? "Monitor is checking answers" : "Monitor is off"} />

          <p className="rail-label lab-label">Plant a failure in the next question</p>
          <div className="fault-list">
            <label className={`fault calm ${fault === null ? "picked" : ""}`}>
              <input type="radio" name="fault" checked={fault === null} onChange={() => setFault(null)} />
              <span><strong>No failure</strong><small>Everything works normally.</small></span>
            </label>
            {setup?.faults.map((f) => (
              <label key={f.id} className={`fault ${fault === f.id ? "picked" : ""}`}>
                <input type="radio" name="fault" checked={fault === f.id} onChange={() => setFault(f.id)} />
                <span><strong>{f.name}</strong><small>{FAULT_HELP[f.id]}</small></span>
              </label>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}
