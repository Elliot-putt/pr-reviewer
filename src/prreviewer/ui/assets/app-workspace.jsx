// app-workspace.jsx — terminal, comments, review workspace, menu dropdown, toast
const { useState: useStateW, useEffect: useEffectW, useRef: useRefW } = React;

/* ---------- Terminal pane ---------- */
function Terminal({ lines, running }) {
  const endRef = useRefW(null);
  useEffectW(() => { if (endRef.current) endRef.current.scrollTop = endRef.current.scrollHeight; }, [lines]);
  return (
    <div className="term">
      <div className="term__bar">
        <span className="term__dots"><i /><i /><i /></span>
        <span className="term__title mono">claude — code-review</span>
        <span className="term__tag">{running ? <><Spinner size={10} color="#7dd3fc" /> running</> : <><Icon name="check" /> idle</>}</span>
      </div>
      <div className="term__body mono" ref={endRef}>
        {lines.map((l, i) => {
          if (l.k === "blank") return <div key={i} className="tl-blank">&nbsp;</div>;
          if (l.k === "cursor") return <div key={i} className="tl-cursor"><span className="caret" /></div>;
          return <div key={i} className={`tl tl-${l.k}`}>{l.t}</div>;
        })}
        {running && lines.length > 0 && lines[lines.length - 1].k !== "cursor" && (
          <div className="tl-cursor"><span className="caret" /></div>
        )}
      </div>
    </div>
  );
}

/* ---------- Comment row ---------- */
const SEV = {
  issue:      { label: "Issue",      cls: "sev-issue" },
  suggestion: { label: "Suggestion", cls: "sev-sugg" },
  nit:        { label: "Nit",        cls: "sev-nit" },
};
function CommentRow({ c, onToggle, revealed }) {
  const sev = SEV[c.severity] || SEV.suggestion;
  return (
    <div className={`cmt ${c.checked ? "on" : ""} ${revealed ? "reveal" : ""}`} onClick={() => onToggle(c.id)}>
      <span className={`cmt__check ${c.checked ? "on" : ""}`}>{c.checked && <Icon name="check" />}</span>
      <div className="cmt__main">
        <div className="cmt__top">
          <span className="cmt__loc mono"><Icon name="file-code" />{c.file}<span className="cmt__line">:{c.line}</span></span>
          <span className={`cmt__sev ${sev.cls}`}>{sev.label}</span>
        </div>
        <div className="cmt__title">{c.title}</div>
        <div className="cmt__body">{c.body}</div>
      </div>
    </div>
  );
}

/* ---------- Empty state ---------- */
function EmptyWorkspace({ channelName }) {
  return (
    <div className="empty">
      <div className="empty__art">
        <Icon name="magnifying-glass" />
      </div>
      <div className="empty__ttl">Select a pull request</div>
      <div className="empty__sub">Pick a PR from the inbox to check out the branch and run the review skill. New PRs from {channelName ? <span className="mono">#{channelName}</span> : "your Slack channel"} land here automatically.</div>
    </div>
  );
}

/* ---------- Review workspace ---------- */
function ReviewWorkspace({ pr, lines, running, comments, onToggle, revealCount, layout, onPost, posted, decision, setDecision }) {
  if (!pr) return <EmptyWorkspace />;
  const a = AUTHORS[pr.author];
  const checkedCount = comments.filter(c => c.checked).length;
  const showComments = pr.status === "ready" || pr.status === "posted";

  const termEl = <Terminal lines={lines} running={running} />;
  const commentsEl = (
    <div className="cmtpane">
      <div className="cmtpane__hd">
        <div className="cmtpane__ttl">Proposed comments {showComments && <span className="cmtpane__n">{comments.length}</span>}</div>
        {showComments && !posted && <span className="cmtpane__hint">Tick the ones to post</span>}
      </div>
      <div className="cmtpane__list">
        {!showComments && (
          <div className="cmtpane__wait">
            {running ? <><Spinner size={14} color="#5359EA" /> Claude is reviewing the diff…</> : "Comments appear here once the review finishes."}
          </div>
        )}
        {showComments && comments.map((c, i) => (
          <CommentRow key={c.id} c={c} onToggle={onToggle} revealed={i < revealCount} />
        ))}
      </div>
    </div>
  );

  return (
    <section className="ws">
      <header className="ws__head">
        <div className="ws__headtop">
          <span className="ws__repo mono">{pr.repo} <span className="prrow__num">#{pr.number}</span></span>
          <StatusBadge status={pr.status} treatment="pill" size="lg" />
        </div>
        <h2 className="ws__title">{pr.title}</h2>
        <div className="ws__meta">
          <span className="ws__branch mono"><Icon name="git-branch" />{pr.branch}</span>
          <span className="ws__who"><Avatar author={pr.author} size={20} /> {a ? a.name : pr.author}</span>
          <span className="ws__diff mono"><span className="add">+{pr.additions}</span> <span className="del">−{pr.deletions}</span> · {pr.files} files</span>
          <a className="ws__gh" href="#" onClick={e => e.preventDefault()}><Icon name="github-logo" />Open on GitHub <Icon name="arrow-up-right" /></a>
        </div>
      </header>

      <div className={`ws__work layout-${layout}`}>
        {termEl}
        {commentsEl}
      </div>

      <footer className="ws__foot">
        <div className="ws__decision">
          <button className={`dseg ${decision === "approve" ? "on approve" : ""}`} onClick={() => setDecision("approve")}>
            <Icon name="check-circle" /> Approve
          </button>
          <button className={`dseg ${decision === "changes" ? "on changes" : ""}`} onClick={() => setDecision("changes")}>
            <Icon name="arrow-counter-clockwise" /> Request changes
          </button>
        </div>
        <button className={`postbtn ${(!showComments || posted) ? "disabled" : ""}`} disabled={!showComments || posted} onClick={onPost}>
          {posted
            ? <><Icon name="check-circle" /> <span>Posted {pr.postedCount || checkedCount} comments</span></>
            : <><Icon name="paper-plane-tilt" /> <span>Post {checkedCount} comment{checkedCount === 1 ? "" : "s"} to PR</span></>}
        </button>
      </footer>
    </section>
  );
}

/* ---------- Menu bar dropdown ---------- */
function MenuDropdown({ prs, counts, listening, setListening, onOpen, onPick, onSimulate }) {
  const recent = prs.slice(0, 4);
  return (
    <div className="mdrop">
      <div className="mdrop__hd">
        <div>
          <div className="mdrop__ttl">Review</div>
          <div className="mdrop__sub">{counts.waiting} waiting · {counts.reviewing} in review</div>
        </div>
        <button className={`switch ${listening ? "on" : ""}`} onClick={() => setListening(!listening)}><span className="switch__knob" /></button>
      </div>
      <div className="mdrop__list">
        {recent.map(pr => (
          <button key={pr.id} className="mdrop__row" onClick={() => onPick(pr.id)}>
            <StatusBadge status={pr.status} treatment="dot" size="sm" />
            <span className="mdrop__rowttl">{pr.title}</span>
            <span className="mdrop__rownum mono">#{pr.number}</span>
          </button>
        ))}
      </div>
      <div className="mdrop__ft">
        <button className="mdrop__act" onClick={onSimulate}><Icon name="flask" /> Simulate new PR</button>
        <button className="mdrop__act primary" onClick={onOpen}><Icon name="arrow-square-out" /> Open window</button>
      </div>
    </div>
  );
}

/* ---------- Toast (native notification) ---------- */
function Toast({ toast, onClick, onClose }) {
  if (!toast) return null;
  return (
    <div className="toast" onClick={onClick}>
      <span className="toast__icon"><span dangerouslySetInnerHTML={{ __html: window.SPECTRE_MARK }} /></span>
      <div className="toast__body">
        <div className="toast__app">Review · now</div>
        <div className="toast__ttl">New PR waiting for review</div>
        <div className="toast__txt">{toast.title} <span className="mono">#{toast.number}</span></div>
      </div>
      <button className="toast__x" onClick={(e) => { e.stopPropagation(); onClose(); }}>×</button>
    </div>
  );
}

Object.assign(window, { Terminal, CommentRow, EmptyWorkspace, ReviewWorkspace, MenuDropdown, Toast, SEV });
