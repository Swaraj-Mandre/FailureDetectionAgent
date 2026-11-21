// Small shared pieces: role marks, badges, the SQL block and the data preview.
import { useState } from "react";
import { Check, ChevronDown, Copy } from "lucide-react";
import type { Outcome, Preview, Role } from "../lib/api";
import { OUTCOME, ROLE_NAME } from "../lib/words";

const INITIAL: Record<Role, string> = { executor: "E", monitor: "M", recovery: "R", person: "Y", stop: "S" };

export function RoleMark({ role, size = 28 }: { role: Role; size?: number }) {
  return (
    <span className={`role-mark role-${role}`} style={{ width: size, height: size }} aria-label={ROLE_NAME[role]}>
      {INITIAL[role]}
    </span>
  );
}

export function OutcomeBadge({ outcome }: { outcome: Outcome }) {
  const { label, tone } = OUTCOME[outcome];
  return <span className={`badge tone-${tone}`}>{label}</span>;
}

export function SqlBlock({ query, open: startOpen = false }: { query: string; open?: boolean }) {
  const [open, setOpen] = useState(startOpen);
  const [copied, setCopied] = useState(false);
  const copy = () => {
    navigator.clipboard?.writeText(query);
    setCopied(true);
    setTimeout(() => setCopied(false), 1200);
  };
  return (
    <div className="sql">
      <button className="sql-toggle" onClick={() => setOpen(!open)} aria-expanded={open}>
        <ChevronDown size={14} className={open ? "flip" : ""} />
        {open ? "Hide the query" : "Show the query"}
      </button>
      {open && (
        <div className="sql-body">
          <pre>{query}</pre>
          <button className="icon-button" onClick={copy} aria-label="Copy the query">
            {copied ? <Check size={14} /> : <Copy size={14} />}
          </button>
        </div>
      )}
    </div>
  );
}

export function DataPreview({ preview, struck = false, limit = 6 }: { preview: Preview; struck?: boolean; limit?: number }) {
  if (preview.error) return <p className="tool-error">{preview.error}</p>;
  return (
    <div className={`preview ${struck ? "struck" : ""}`}>
      <table>
        <thead>
          <tr>{preview.columns.map((c) => <th key={c}>{c}</th>)}</tr>
        </thead>
        <tbody>
          {preview.rows.slice(0, limit).map((row, i) => (
            <tr key={i}>{row.map((cell, j) => <td key={j} className={cell === "(hidden)" ? "hidden-cell" : ""}>{cell}</td>)}</tr>
          ))}
        </tbody>
      </table>
      {preview.total > limit && <p className="preview-more">and {preview.total - limit} more rows</p>}
      {preview.hidden > 0 && <p className="preview-more">Personal columns are hidden in this preview.</p>}
    </div>
  );
}

export function Switch({ on, onChange, label }: { on: boolean; onChange: (on: boolean) => void; label: string }) {
  return (
    <button role="switch" aria-checked={on} className={`switch ${on ? "on" : ""}`} onClick={() => onChange(!on)}>
      <span className="switch-track"><span className="switch-thumb" /></span>
      <span>{label}</span>
    </button>
  );
}
