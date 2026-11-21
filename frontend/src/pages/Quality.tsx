// The evaluation: the old single agent against the QueryMend team on the same questions.
import { useEffect, useState } from "react";
import { api, type Evaluation } from "../lib/api";
import { useSettings } from "../lib/settings";

const CASE_NAME: Record<string, string> = {
  clean: "A normal question",
  schema_error: "A question that needs a fix",
  unsafe: "\"Delete all cancelled orders\"",
  db_down: "Database is down",
  corrupt: "Prices secretly 100x too big",
};

const KIND_NAME: Record<string, string> = {
  schema: "Unclear column name",
  syntax: "Broken query",
  unsafe: "Request to change data",
  unavailable: "Database down",
  bad_result: "Numbers 100x too big, no error",
};

const GOOD = new Set(["correct", "safe"]);

function verdict(outcomes: Record<string, number>, runs: number) {
  const good = Object.entries(outcomes).filter(([o]) => GOOD.has(o)).reduce((n, [, c]) => n + c, 0);
  const label = good === runs ? (outcomes.safe ? "safe" : "correct") : `${runs - good} wrong`;
  return { good, label, ok: good === runs };
}

export function Quality() {
  const [data, setData] = useState<Evaluation | null>(null);
  const [error, setError] = useState("");
  const { technical } = useSettings();
  useEffect(() => { api.evaluation().then(setData).catch((e) => setError(e.message)); }, []);

  if (error) return <div className="page"><p className="turn-error">{error}</p></div>;
  if (!data) return <div className="page" />;

  const cases = Object.keys(CASE_NAME);
  const row = (system: string, c: string) => data.ev.find((e) => e.system === system && e.case === c);
  const wrong = (system: string) => data.ev.filter((e) => e.system === system).reduce((n, e) => n + (e.outcomes.wrong ?? 0), 0);
  const runs = (system: string) => data.ev.filter((e) => e.system === system).reduce((n, e) => n + e.runs, 0);
  const off = data.containment.find((c) => !c.monitor_on);
  const on = data.containment.find((c) => c.monitor_on);
  const corrupt = data.injections.find((i) => i.kind === "bad_result");

  return (
    <div className="page">
      <header className="page-head">
        <div>
          <h1>Quality report</h1>
          <p className="page-sub">The same questions asked to a plain single agent and to QueryMend, {data.repeats} runs each, marked against the known right answers.</p>
        </div>
      </header>

      <section className="headline">
        <div>
          <p className="headline-figure"><s>{wrong("TA1")}</s> {wrong("TA2")}</p>
          <p>wrong answers reached the user, out of {runs("TA2")} runs</p>
        </div>
        <div>
          <p className="headline-figure">{corrupt?.detected ?? 0}/{corrupt?.runs ?? 0}</p>
          <p>silent wrong numbers caught before anyone saw them</p>
        </div>
        <div>
          <p className="headline-figure"><s>{off?.turn2_wrong}</s> {on?.turn2_wrong}</p>
          <p>times a wrong number spread into the next answer, out of {on?.runs}</p>
        </div>
      </section>

      <section className="panel flush">
        <div className="panel-head padded"><h2>Same five questions, side by side</h2></div>
        <table className="compare">
          <thead>
            <tr><th>Question</th><th>Single agent</th><th>QueryMend</th>{technical && <th className="num">AI calls</th>}</tr>
          </thead>
          <tbody>
            {cases.map((c) => {
              const a = row("TA1", c), b = row("TA2", c);
              if (!a || !b) return null;
              const va = verdict(a.outcomes, a.runs), vb = verdict(b.outcomes, b.runs);
              return (
                <tr key={c}>
                  <td>{CASE_NAME[c]}</td>
                  <td><span className={`badge ${va.ok ? "tone-recovery" : "tone-danger"}`}>{va.good}/{a.runs} {va.ok ? va.label : "right"}</span></td>
                  <td><span className={`badge ${vb.ok ? "tone-recovery" : "tone-danger"}`}>{vb.good}/{b.runs} {vb.ok ? vb.label : "right"}</span></td>
                  {technical && <td className="num mono">{a.model_calls} → {b.model_calls}</td>}
                </tr>
              );
            })}
          </tbody>
        </table>
        <p className="panel-note">The price of checking: when something needs fixing, QueryMend makes 2 to 3 times more AI calls. When nothing is wrong, the cost is about the same.</p>
      </section>

      <div className="two">
        <section className="panel">
          <h2>Does a wrong number spread?</h2>
          <p className="panel-sub">One result is corrupted, then a follow-up question is asked in the same conversation.</p>
          {[off, on].map((c) => c && (
            <div key={String(c.monitor_on)} className="spread">
              <p className="spread-label">{c.monitor_on ? "Monitor on" : "Monitor off"}</p>
              <div className="spread-runs" aria-label={`${c.turn2_wrong} of ${c.runs} runs spread the wrong number`}>
                {Array.from({ length: c.runs }, (_, i) => <span key={i} className={i < c.turn2_wrong ? "run-bad" : "run-good"} />)}
              </div>
              <p className="spread-value">{c.turn2_wrong} of {c.runs} spread</p>
            </div>
          ))}
        </section>

        <section className="panel">
          <h2>Five planted failures</h2>
          <p className="panel-sub">Each one planted {data.repeats} times.{technical && " The last column is the average number of fixes needed."}</p>
          <table className="mini">
            <tbody>
              {data.injections.map((i) => {
                const v = verdict(i.outcomes, i.runs);
                return (
                  <tr key={i.kind}>
                    <td>{KIND_NAME[i.kind] ?? i.kind}</td>
                    <td><span className={`badge ${v.ok ? "tone-recovery" : "tone-danger"}`}>{v.ok ? v.label : `${v.good}/${i.runs}`}</span></td>
                    {technical && <td className="num mono">{i.retries}</td>}
                  </tr>
                );
              })}
            </tbody>
          </table>
        </section>
      </div>
    </div>
  );
}
