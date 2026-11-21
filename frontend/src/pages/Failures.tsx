// Every failure the Monitor caught, read from the failure log.
import { Fragment, useEffect, useState } from "react";
import { ChevronDown } from "lucide-react";
import { api, type Failures as Data } from "../lib/api";
import { action, FAILURE_NAME, when } from "../lib/words";
import { useSettings } from "../lib/settings";

export function Failures() {
  const [data, setData] = useState<Data | null>(null);
  const [filter, setFilter] = useState<string | null>(null);
  const [open, setOpen] = useState<number | null>(null);
  const [error, setError] = useState("");
  const { technical } = useSettings();

  useEffect(() => { api.failures().then(setData).catch((e) => setError(e.message)); }, []);

  const most = Math.max(1, ...(data?.counts.map((c) => c.count) ?? [1]));
  const total = data?.counts.reduce((n, c) => n + c.count, 0) ?? 0;
  const rows = data?.rows.filter((r) => !filter || r.failure_type === filter) ?? [];

  return (
    <div className="page">
      <header className="page-head">
        <div>
          <h1>Caught failures</h1>
          <p className="page-sub">Every problem the Monitor caught, and what Recovery did about it. None of these reached a user as a wrong number.</p>
        </div>
      </header>

      {error && <p className="turn-error">{error}</p>}

      {data && (
        <>
          <section className="panel">
            <div className="panel-head">
              <h2>{total} failures caught</h2>
              {filter && <button className="link" onClick={() => setFilter(null)}>Show all</button>}
            </div>
            <ul className="bars">
              {data.counts.map((c) => (
                <li key={c.type}>
                  <button className={`bar-row ${filter === c.type ? "picked" : ""}`} onClick={() => setFilter(filter === c.type ? null : c.type)}>
                    <span className="bar-label">{FAILURE_NAME[c.type] ?? c.type}</span>
                    <span className="bar-track"><span className="bar-fill" style={{ width: `${(c.count / most) * 100}%` }} /></span>
                    <span className="bar-value">{c.count}</span>
                  </button>
                </li>
              ))}
            </ul>
          </section>

          <section className="panel flush">
            <table className="log">
              <thead>
                <tr><th>When</th><th>Problem</th><th>What the team did</th>{technical && <th className="num">Retries left</th>}{technical && <th />}</tr>
              </thead>
              <tbody>
                {rows.map((r, i) => (
                  <Fragment key={i}>
                    <tr className={`${open === i ? "open" : ""} ${technical ? "clickable" : ""}`} onClick={() => technical && setOpen(open === i ? null : i)}>
                      <td className="nowrap">{when(r.logged_at)}</td>
                      <td><span className={`badge ${r.failure_type === "unsafe" || r.failure_type === "unavailable" ? "tone-danger" : "tone-monitor"}`}>{FAILURE_NAME[r.failure_type] ?? r.failure_type}</span></td>
                      <td>{action(r.action)}</td>
                      {technical && <td className="num">{r.retries_left}</td>}
                      {technical && <td><ChevronDown size={15} className={open === i ? "flip" : ""} /></td>}
                    </tr>
                    {technical && open === i && (
                      <tr className="log-detail">
                        <td colSpan={5}>
                          {r.error && <p><strong>What went wrong:</strong> {r.error}</p>}
                          {r.sql && <pre>{r.sql}</pre>}
                        </td>
                      </tr>
                    )}
                  </Fragment>
                ))}
              </tbody>
            </table>
            {rows.length === 0 && <p className="empty">No failures of this kind yet.</p>}
          </section>
        </>
      )}
    </div>
  );
}
