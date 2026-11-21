// The left rail: brand, navigation, recent conversations, store and system status.
import { NavLink, useNavigate } from "react-router-dom";
import { BarChart3, BookOpen, Compass, MessageSquareText, Plus, ShieldAlert } from "lucide-react";
import { useSettings } from "../lib/settings";
import { Switch } from "./Bits";

export function Logo() {
  return (
    <svg width="30" height="30" viewBox="0 0 32 32" aria-hidden="true">
      <rect width="32" height="32" rx="8" fill="var(--brand)" />
      <path d="M9 16.5l4.5 4.5L23 11.5" fill="none" stroke="#D6E4DC" strokeWidth="3.2" strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  );
}

export function Sidebar() {
  const { setup, status, userId, setUserId, technical, setTechnical, conversations } = useSettings();
  const navigate = useNavigate();
  const ready = status?.ollama && status.chat_model_ready && status.database;

  return (
    <aside className="sidebar">
      <div className="brand">
        <Logo />
        <div>
          <strong>QueryMend</strong>
          <span>Answers you can trust</span>
        </div>
      </div>

      <button className="button button-brand new-question" onClick={() => navigate("/")}>
        <Plus size={17} /> New question
      </button>

      <nav className="nav">
        <NavLink to="/" end><MessageSquareText size={18} /> Ask</NavLink>
        <NavLink to="/failures"><ShieldAlert size={18} /> Caught failures</NavLink>
        <NavLink to="/quality"><BarChart3 size={18} /> Quality report</NavLink>
        <NavLink to="/glossary"><BookOpen size={18} /> Your words</NavLink>
        <NavLink to="/tour"><Compass size={18} /> Project tour</NavLink>
      </nav>

      <div className="recent">
        <p className="rail-label">Recent questions</p>
        {conversations.length === 0 && <p className="recent-empty">Your questions will appear here.</p>}
        <ul>
          {conversations.slice(0, 12).map((c) => (
            <li key={c.id}>
              <NavLink to={`/c/${c.id}`} title={c.title}>{c.title}</NavLink>
            </li>
          ))}
        </ul>
      </div>

      <div className="rail-foot">
        <div className="tech-switch">
          <Switch on={technical} onChange={setTechnical} label="Technical details" />
          <small>{technical ? "Showing queries, data and internals" : "Plain answers only"}</small>
        </div>
        <label className="store-picker">
          <span className="rail-label">Store</span>
          <select value={userId} onChange={(e) => setUserId(e.target.value)}>
            {setup?.users.map((u) => <option key={u.id} value={u.id}>{u.name}</option>)}
          </select>
        </label>
        <p className={`system ${ready ? "ok" : "down"}`}>
          <span className="dot" />
          {status === null ? "Server not reachable" : ready ? `${status.chat_model} ready, running offline` : "AI model not running"}
        </p>
      </div>
    </aside>
  );
}
