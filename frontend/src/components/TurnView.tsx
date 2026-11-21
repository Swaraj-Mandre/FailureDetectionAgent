// One question and everything that happened to it: the live trail, the approval and the answer.
import { useState } from "react";
import { ChevronDown, EyeOff, Eye, FlaskConical, ShieldOff } from "lucide-react";
import type { Preview, Role, Step, Turn } from "../lib/api";
import { describe, plural, ROLE_JOB, ROLE_NAME } from "../lib/words";
import { DataPreview, OutcomeBadge, RoleMark, SqlBlock } from "./Bits";

interface Props {
  turn: Turn;
  faultName?: string;
  retryBudget: number;
  onDecide?: (decision: "approve" | "reject") => void;
}

export function TurnView({ turn, faultName, retryBudget, onDecide }: Props) {
  // open for a live run, closed for history unless the link ends in #how
  const [showTrail, setShowTrail] = useState(!!turn.running || window.location.hash === "#how");
  const trailOpen = turn.running || !!turn.approval || showTrail;

  return (
    <article className="turn">
      <div className="question">
        <p>{turn.question}</p>
        {(turn.fault || !turn.monitor_on) && (
          <div className="question-tags">
            {turn.fault && <span className="tag"><FlaskConical size={13} /> Test failure: {faultName ?? turn.fault}</span>}
            {!turn.monitor_on && <span className="tag tag-warn"><ShieldOff size={13} /> Monitor off</span>}
          </div>
        )}
      </div>

      <div className="reply">
        {turn.running && <Pipeline steps={turn.steps} />}

        {turn.approval && onDecide && (
          <div className="approval" role="alertdialog" aria-label="Personal data approval">
            <RoleMark role="person" />
            <div>
              <h3>This answer includes customers' email and phone numbers.</h3>
              <p>QueryMend paused before showing them. Do you want them in the answer?</p>
              <div className="approval-actions">
                <button className="button button-person" onClick={() => onDecide("approve")}><Eye size={16} /> Show them</button>
                <button className="button button-quiet" onClick={() => onDecide("reject")}><EyeOff size={16} /> Keep them hidden</button>
              </div>
            </div>
          </div>
        )}

        {turn.answer !== undefined && (
          <div className="answer">
            <div className="answer-head">
              {turn.outcome && <OutcomeBadge outcome={turn.outcome} />}
              {turn.stats && (
                <span className="answer-stats">
                  {plural(turn.stats.tool_calls, "query", "queries")} · {turn.stats.retries} of {retryBudget} retries · {turn.stats.seconds} s
                </span>
              )}
            </div>
            <p className="answer-text">{turn.answer}</p>
            {turn.table && <div className="answer-table"><DataPreview preview={turn.table} limit={12} /></div>}
          </div>
        )}

        {turn.error && <p className="turn-error">{turn.error}</p>}

        {!turn.running && !turn.approval && turn.steps.length > 0 && (
          <button className="trail-toggle" onClick={() => setShowTrail(!showTrail)} aria-expanded={trailOpen}>
            <ChevronDown size={15} className={trailOpen ? "flip" : ""} />
            How QueryMend got this answer
          </button>
        )}
        {trailOpen && <Trail turn={turn} />}
      </div>
    </article>
  );
}

/** The four agents in a row, with the one working right now lit up. */
function Pipeline({ steps }: { steps: Step[] }) {
  const last = steps.at(-1);
  const active: Role = !last ? "executor" : last.role === "stop" ? "recovery" : last.role;
  const roles: Role[] = ["executor", "monitor", "recovery", "person"];
  return (
    <div className="pipeline" aria-live="polite">
      {roles.map((role) => (
        <div key={role} className={`pipe ${role === active ? `pipe-on pipe-${role}` : ""}`}>
          <RoleMark role={role} size={22} />
          <span className="pipe-name">{ROLE_NAME[role]}</span>
          <span className="pipe-job">{ROLE_JOB[role]}</span>
        </div>
      ))}
    </div>
  );
}

/** Every step in order. Each query the Executor ran sits under the step that ran it. */
function Trail({ turn }: { turn: Turn }) {
  let query = 0;
  const items = turn.steps.map((step, i) => {
    const ran = step.role === "executor" && step.text.startsWith("calls");
    const sql = ran ? turn.queries[query] : undefined;
    const result: Preview | undefined = ran ? turn.results[query] : undefined;
    if (ran) query += 1;
    const next = turn.steps[i + 1];
    const blocked = !!next && next.role === "monitor" && describe(next).alarm;
    return { step, sql, result, blocked };
  });

  return (
    <ol className="trail">
      {items.map(({ step, sql, result, blocked }, i) => {
        const { title, detail, alarm } = describe(step);
        return (
          <li key={i} className={`trail-item ${alarm ? "alarm" : ""}`}>
            <RoleMark role={step.role} size={26} />
            <div className="trail-body">
              <p className="trail-title">
                <span className={`trail-role ink-${step.role}`}>{ROLE_NAME[step.role]}</span>
                {title}
              </p>
              {detail && <p className="trail-detail">{detail}</p>}
              {sql && <SqlBlock query={sql} />}
              {result && <DataPreview preview={result} struck={blocked} />}
            </div>
          </li>
        );
      })}
      {turn.running && (
        <li className="trail-item waiting">
          <span className="dots" aria-label="Working"><i /><i /><i /></span>
          <p className="trail-detail">Working on it</p>
        </li>
      )}
    </ol>
  );
}
