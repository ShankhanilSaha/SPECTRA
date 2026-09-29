import type { ReactNode } from "react";

import { go, useCase } from "../state";
import { GROUP_TITLE, markKey, neighbours, nextEvidence, STEPS, stepDef, stepStatus, type StepId } from "../steps";
import { Badge, Button, Notice } from "./ui";

/**
 * One step of the examination: where it sits on the path, what it is for, its content,
 * and the way on. Steps 4–7 name the evidence item they are looking at.
 */
export function StepPage(props: { step: StepId; lead?: ReactNode; children: ReactNode; navExtra?: ReactNode }) {
  const c = useCase();
  const def = stepDef(props.step);
  const perItem = def.group === "evidence";
  const item = perItem ? c.info?.evidence.find((e) => e.id === c.evidenceId) : undefined;

  let body = props.children;
  if (c.infoError && !c.info) {
    body = (
      <Notice tone="error" title="The engine could not read this case">
        <p>{c.infoError}</p>
        <p>
          Nothing below can be shown until it can. <a href="#/settings">Check Settings</a>, then{" "}
          <button type="button" className="link" onClick={c.changed}>try again</button>.
        </p>
      </Notice>
    );
  } else if (perItem && c.info && !c.evidenceId) {
    body = (
      <Notice title="No evidence yet">
        This step works on one evidence item at a time. <a href="#/evidence">Add evidence</a> first.
      </Notice>
    );
  }

  return (
    <div className="page step-page">
      <header className="page-head">
        <div className="page-head-text">
          <div className="eyebrow">
            Step {def.n} of {STEPS.length} · {GROUP_TITLE[def.group]}
          </div>
          <h1>{def.title}</h1>
          <p className="lead">{props.lead ?? def.purpose}</p>
        </div>
        {item && (
          <div className="page-head-aside evidence-chip" title="The evidence item this step works on — switch it in the sidebar">
            <strong>{item.id}</strong>
            <span>{item.label || item.kind}</span>
            <Badge tone="accent">class {item.provenance_class}</Badge>
          </div>
        )}
      </header>
      {body}
      <StepNav step={props.step}>{props.navExtra}</StepNav>
    </div>
  );
}

/**
 * The foot of every step: back, and on. "On" is the primary button once the step is done;
 * before that it still moves on, and for a step it is legitimate to leave undone (no
 * clock offset, no scene documents) it says so and remembers the choice.
 */
export function StepNav(props: { step: StepId; children?: ReactNode }) {
  const c = useCase();
  const progress = c.info?.progress ?? null;
  const def = stepDef(props.step);
  const { prev, next } = neighbours(props.step);
  const status = stepStatus(props.step, progress, c.evidence, c.marks);
  const complete = status.state === "done" || status.state === "na" || status.state === "skipped";
  const skipping = !complete && Boolean(def.skipLabel);
  const nextStatus = next ? stepStatus(next.id, progress, c.evidence, c.marks) : null;
  const other = props.step === "recover" ? nextEvidence(progress, c.marks, c.evidenceId) : null;

  const moveOn = () => {
    if (!next) return;
    if (skipping) c.mark("skipped", markKey(def.id, c.evidenceId));
    go(next.id);
  };

  return (
    <footer className="step-nav">
      <div className="step-nav-left">
        {prev && (
          <Button kind="ghost" onClick={() => go(prev.id)}>
            ← {prev.title}
          </Button>
        )}
      </div>
      <div className="step-nav-mid">{props.children}</div>
      <div className="step-nav-right">
        {status.state === "skipped" && (
          <Button kind="ghost" onClick={() => c.mark("skipped", markKey(def.id, c.evidenceId), false)}>
            Undo skip
          </Button>
        )}
        {other && (
          <Button
            kind="primary"
            onClick={() => {
              c.setEvidenceId(other.evidenceId!);
              go(other.step);
            }}
          >
            Examine {other.evidenceId} next →
          </Button>
        )}
        {next ? (
          <Button
            kind={complete && !other ? "primary" : "secondary"}
            onClick={moveOn}
            disabled={nextStatus?.state === "locked"}
            title={nextStatus?.state === "locked" ? `${next.title}: ${nextStatus.summary}` : undefined}
          >
            {skipping ? `${def.skipLabel} →` : `Continue to ${next.title} →`}
          </Button>
        ) : (
          <Button kind={complete ? "primary" : "secondary"} onClick={() => go("overview")}>
            Back to the case overview
          </Button>
        )}
      </div>
    </footer>
  );
}
