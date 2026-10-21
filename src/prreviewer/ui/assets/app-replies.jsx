const { useState: useStateR, useMemo: useMemoR, useEffect: useEffectR, useRef: useRefR } = React;

const REPLY_FILTERS = [
  { id: "triage", label: "To triage", match: r => ["pending", "verdict_ready", "verdict_error"].includes(r.status) },
  { id: "done", label: "Done", match: r => ["done", "dismissed"].includes(r.status) },
  { id: "all", label: "All", match: () => true },
];

const SEVERITY_RE = /^\s*\*\*(critical|important|minor|nit|suggestion|question)\*\*\s*:?\s*/i;
const SEVERITY_ORDER = ["critical", "important", "minor", "nit", "suggestion", "question"];

const VERDICT_UI = {
  addressed:     { label: "Addressed",         tone: "green", mark: "✓" },
  pushback_fair: { label: "Conceded",          tone: "green", mark: "✓", tip: "Their pushback holds up against the code" },
  partial:       { label: "Partial",           tone: "amber", mark: "!" },
  pushback_hold: { label: "Your point stands", tone: "blue",  mark: "↩" },
};

const ACTION_LABEL = {
  resolved: "✓ Resolved",
  replied: "Replied",
  replied_resolved: "Replied + resolved",
  dismissed: "Dismissed",
};

function ago(iso) {
  if (!iso) return "";
  const secs = Math.max(0, (Date.now() - new Date(iso).getTime()) / 1000);
  if (secs < 60) return "now";
  if (secs < 3600) return `${Math.floor(secs / 60)}m`;
  if (secs < 86400) return `${Math.floor(secs / 3600)}h`;
  return `${Math.floor(secs / 86400)}d`;
}

function openExternal(url) {
  if (!url) return;
  if (window.pywebview && window.pywebview.api && window.pywebview.api.open_url) window.pywebview.api.open_url(url);
  else window.open(url, "_blank");
}

function openLinkInBrowser(e) {
  const a = e.target.closest("a[href]");
  if (!a) return;
  e.preventDefault();
  openExternal(a.href);
}

function severityOf(body) {
  const m = (body || "").match(SEVERITY_RE);
  return m ? m[1].toLowerCase() : "";
}

function previewText(body) {
  return (body || "")
    .replace(SEVERITY_RE, "")
    .replace(/```suggestion[\s\S]*?```/g, " [suggested change] ")
    .replace(/```[\s\S]*?```/g, " [code] ")
    .replace(/!\[[^\]]*\]\([^)]*\)/g, "")
    .replace(/\[([^\]]+)\]\([^)]*\)/g, "$1")
    .replace(/^\s*(?:[>#]+|[-*]\s)\s*/gm, "")
    .replace(/\*\*|__|`/g, "")
    .replace(/\s+/g, " ")
    .trim();
}

function fileName(path) {
  return (path || "").split("/").pop();
}

function isOpenStatus(r) {
  return ["pending", "verdict_ready", "verdict_error"].includes(r.status);
}

function isStale(r) {
  return !!(r.verdict && r.verdictSha && r.headSha && r.verdictSha !== r.headSha);
}

function sevOf(r) {
  return severityOf((r.comments.find(c => c.mine) || {}).body);
}

function sevRank(r) {
  const i = SEVERITY_ORDER.indexOf(sevOf(r));
  return i === -1 ? SEVERITY_ORDER.length : i;
}

function CommentBody({ c, clamp }) {
  if (c.bodyHtml) {
    return <div className={`rp-md ${clamp ? "is-clamped" : ""}`} onClick={openLinkInBrowser} dangerouslySetInnerHTML={{ __html: c.bodyHtml }} />;
  }
  return <div className={`rp-md is-plain ${clamp ? "is-clamped" : ""}`}>{c.body}</div>;
}

function Severity({ sev }) {
  if (!sev) return <span className="rp-dot" />;
  return (
    <>
      <span className={`rp-dot sev-${sev}`} />
      <span className={`rp-sev sev-${sev}`}>{sev.toUpperCase()}</span>
    </>
  );
}

function Status({ r }) {
  if (!isOpenStatus(r)) {
    if (r.auto) return <span className="rp-status tone-blue" title="Resolved by Auto reply">Auto ✓ {Math.round((r.autoConfidence || 0) * 100)}%</span>;
    return <span className="rp-status tone-green">{ACTION_LABEL[r.action] || r.status}</span>;
  }
  if (r.autoResolving) {
    return <span className="rp-status tone-blue" title="Passed the Auto reply threshold — 👍 + resolving on GitHub"><span className="rp-spin is-blue" /> Auto-resolving…</span>;
  }
  if (r.verdict) {
    const v = VERDICT_UI[r.verdict.verdict] || { label: r.verdict.verdict, tone: "gray", mark: "·" };
    return (
      <span className={`rp-status tone-${v.tone}`} title={v.tip || r.verdict.reason}>
        {v.mark} {v.label} <span className="rp-status__pct">{Math.round((r.verdict.confidence || 0) * 100)}%</span>
      </span>
    );
  }
  if (r.status === "verdict_error") return <span className="rp-status tone-gray">Couldn't check</span>;
  if (r.note) return <span className="rp-status tone-gray">{r.note}</span>;
  return <span className="rp-status" title="Claude is checking the fix"><span className="rp-skel rp-skel--status" /></span>;
}

function Menu({ items }) {
  const [open, setOpen] = useStateR(false);
  const [up, setUp] = useStateR(false);
  const ref = useRefR(null);
  useEffectR(() => {
    if (!open) return;
    const close = e => { if (ref.current && !ref.current.contains(e.target)) setOpen(false); };
    document.addEventListener("mousedown", close);
    return () => document.removeEventListener("mousedown", close);
  }, [open]);
  const shown = items.filter(Boolean);
  return (
    <span className="rp-menu" ref={ref}>
      <button className="rp-btn ghost" aria-label="More actions" onClick={e => {
        e.stopPropagation();
        setUp(e.currentTarget.getBoundingClientRect().bottom + 190 > window.innerHeight);
        setOpen(!open);
      }}>⋯</button>
      {open && (
        <div className={`rp-menu__list ${up ? "is-up" : ""}`}>
          {shown.map(it => (
            <button key={it.label} className="rp-menu__item" disabled={it.disabled}
              onClick={e => { e.stopPropagation(); setOpen(false); it.onClick(); }}>
              {it.label}
            </button>
          ))}
        </div>
      )}
    </span>
  );
}

function ReplyBox({ r, busy, act, onCancel }) {
  const suggested = r.verdict ? r.verdict.suggested_reply || "" : "";
  const [draft, setDraft] = useStateR(suggested);
  useEffectR(() => { setDraft(suggested); }, [suggested]);
  const empty = !draft.trim();
  return (
    <div className="rp-reply" onClick={e => e.stopPropagation()}>
      <textarea className="rp-reply__ta" autoFocus rows={Math.min(8, Math.max(2, Math.ceil((draft || "").length / 80) + 1))}
        value={draft} onChange={e => setDraft(e.target.value)} placeholder="Reply as you…" disabled={!!busy} />
      <div className="rp-reply__bar">
        <span className="rp-reply__hint">{suggested ? "Suggested by Claude · edit before sending" : "Posts as you on GitHub"}</span>
        <button className="rp-btn ghost" onClick={onCancel} disabled={!!busy}>Cancel</button>
        <button className="rp-btn" disabled={empty || !!busy} onClick={() => act("reply", draft)}>
          {busy === "reply" ? "Sending…" : "Send"}
        </button>
        <button className="rp-btn primary" disabled={empty || !!busy} onClick={() => act("reply_resolve", draft)}>
          {busy === "reply_resolve" ? "Sending…" : "Send + Resolve"}
        </button>
      </div>
    </div>
  );
}

function isChecking(r) {
  return r.status === "pending" && !r.verdict && !r.note && !r.autoResolving;
}

function SkeletonCards({ n = 3 }) {
  return (
    <div className="rp-prs" aria-busy="true" aria-label="Loading replies">
      {Array.from({ length: n }).map((_, i) => (
        <div key={i} className="rp-pr rp-skelcard" style={{ animationDelay: `${i * 80}ms` }}>
          <div className="rp-skel" style={{ width: "34%", height: 10 }} />
          <div className="rp-skel" style={{ width: `${72 - i * 9}%`, height: 14, marginTop: 9 }} />
          <div className="rp-skel" style={{ width: "22%", height: 10, marginTop: 9, marginLeft: "auto" }} />
        </div>
      ))}
    </div>
  );
}

function Thread({ r, onRecheck, onAction, leaving }) {
  const [open, setOpen] = useStateR(false);
  const [full, setFull] = useStateR(false);
  const [more, setMore] = useStateR(false);
  const [replying, setReplying] = useStateR(false);
  const [busy, setBusy] = useStateR(null);
  const [localErr, setLocalErr] = useStateR("");
  useEffectR(() => { setBusy(null); if (!isOpenStatus(r)) setReplying(false); }, [r.status, r.actionError, r.updatedAt]);

  const sev = sevOf(r);
  const firstMine = r.comments.find(c => c.mine);
  const latest = r.comments[r.comments.length - 1];
  const theirs = latest && !latest.mine ? latest : r.comments.slice().reverse().find(c => !c.mine);
  const between = r.comments.length - (firstMine ? 1 : 0) - (theirs ? 1 : 0);
  const where = `${fileName(r.path)}${r.line ? `:${r.line}` : ""}`;
  const openStatus = isOpenStatus(r);
  const error = localErr || r.actionError;
  const locked = !!busy || !!r.autoResolving;

  function act(action, body) {
    setBusy(action);
    setLocalErr("");
    Promise.resolve(onAction(action, r.id, body)).then(res => {
      if (!res || !res.ok) { setBusy(null); setLocalErr((res && res.error) || "Something went wrong"); }
      else if (action === "terminal") setBusy(null);
    }).catch(err => { setBusy(null); setLocalErr(String(err)); });
  }

  if (!open) {
    return (
      <button className={`rp-row ${openStatus ? "" : "is-done"} ${r.autoResolving ? "is-auto" : ""} ${leaving ? "is-leaving" : ""}`} onClick={() => setOpen(true)}>
        <Severity sev={sev} />
        <span className="rp-file mono" title={r.path}>{where}</span>
        <span className="rp-row__preview">— {previewText(theirs ? theirs.body : "")}</span>
        <Status r={r} />
        <span className="rp-chev">›</span>
      </button>
    );
  }

  return (
    <div className={`rp-thread ${openStatus ? "" : "is-done"} ${r.autoResolving ? "is-auto" : ""} ${leaving ? "is-leaving" : ""}`}>
      <button className="rp-thread__hd" onClick={() => setOpen(false)}>
        <Severity sev={sev} />
        <span className="rp-file mono" title={r.path}>{where}</span>
        {r.outdated && <span className="rp-tag" title="The lines changed since your comment">outdated</span>}
        <span className="rp-chev is-open">⌄</span>
      </button>

      {firstMine && !full && (
        <div className="rp-you" onClick={() => setFull(true)} title="Show the full thread">
          <b>You:</b> {previewText(firstMine.body)}
        </div>
      )}
      {full && (
        <div className="rp-full">
          {r.comments.filter(c => c !== theirs).map((c, i) => (
            <div key={i} className={`rp-msg ${c.mine ? "mine" : ""}`}>
              <div className="rp-msg__hd"><b>{c.mine ? "You" : c.author}</b> · {ago(c.createdAt)}</div>
              <CommentBody c={c} />
            </div>
          ))}
          <button className="rp-link" onClick={() => setFull(false)}>Hide earlier messages</button>
        </div>
      )}
      {!full && between > 0 && (
        <button className="rp-link rp-between" onClick={() => setFull(true)}>{between} more {between === 1 ? "message" : "messages"}</button>
      )}

      {theirs && (
        <div className="rp-theirs">
          <div className="rp-msg__hd"><b>{theirs.author}</b> · {ago(theirs.createdAt)}</div>
          <CommentBody c={theirs} clamp={!more} />
          {!more && (theirs.body || "").length > 280 && <button className="rp-link" onClick={() => setMore(true)}>Show more</button>}
        </div>
      )}

      {r.autoResolving && (
        <div className="rp-why tone-blue">Passed your Auto reply threshold — adding 👍 and resolving on GitHub.</div>
      )}
      {openStatus && r.verdict && !r.autoResolving && (
        <div className={`rp-why tone-${(VERDICT_UI[r.verdict.verdict] || {}).tone || "gray"}`}>
          {r.verdict.reason}
          {r.evidence === "partial" && <span className="rp-why__note"> · checked the PR diff only</span>}
          {isStale(r) && <span className="rp-why__note is-warn"> · code changed since this check</span>}
        </div>
      )}
      {isChecking(r) && (
        <div className="rp-why rp-why--skel" aria-label="Claude is checking the fix">
          <span className="rp-skel" style={{ width: "92%" }} />
          <span className="rp-skel" style={{ width: "64%" }} />
        </div>
      )}
      {openStatus && r.status === "verdict_error" && (
        <div className="rp-why tone-gray">Couldn't get a verdict — look at it yourself or re-check.</div>
      )}

      {replying && openStatus && <ReplyBox r={r} busy={busy} act={act} onCancel={() => setReplying(false)} />}
      {error && <div className="rp-err">{error}</div>}

      {!replying && (
        <div className="rp-actions">
          {openStatus && (
            <button className="rp-btn primary" disabled={locked} onClick={() => act("resolve")} title="👍 their reply and resolve the thread">
              {busy === "resolve" ? "Resolving…" : r.autoResolving ? "Auto-resolving…" : "Resolve"}
            </button>
          )}
          {openStatus && (
            <button className="rp-btn" disabled={locked} onClick={() => setReplying(true)}>
              {r.verdict && r.verdict.suggested_reply ? "Reply (suggested)" : "Reply"}
            </button>
          )}
          {!openStatus && r.auto && (
            <button className="rp-btn" disabled={!!busy} onClick={() => act("undo")} title="Unresolve the thread and remove the 👍">
              {busy === "undo" ? "Undoing…" : "Undo"}
            </button>
          )}
          <Menu items={[
            openStatus && { label: busy === "terminal" ? "Opening…" : "Open in terminal", onClick: () => act("terminal"), disabled: locked },
            openStatus && { label: "Re-check", onClick: () => onRecheck(r.id) },
            { label: "Open on GitHub ↗", onClick: () => openExternal(r.threadUrl || r.prUrl) },
            openStatus && { label: "Dismiss", onClick: () => act("dismiss"), disabled: locked },
          ]} />
          <span className="rp-actions__status"><Status r={r} /></span>
        </div>
      )}
    </div>
  );
}

function RereviewBar({ first, onAction }) {
  const [busy, setBusy] = useStateR(false);
  const [err, setErr] = useStateR("");
  useEffectR(() => { setBusy(false); }, [first.prRereview]);
  if (first.prRereview === "running") {
    return (
      <div className="rp-rr is-running" onClick={e => e.stopPropagation()}>
        <span>✓ All resolved · re-review started</span>
        <button className="rp-btn" onClick={() => onAction("show", first.prId)}>Open in Inbox</button>
      </div>
    );
  }
  if (first.prRereview !== "ready") return null;
  return (
    <div className="rp-rr" onClick={e => e.stopPropagation()}>
      <span><b>✓ All your threads are resolved</b> and there are new commits since your review.</span>
      {err && <span className="rp-rr__err">{err}</span>}
      <button className="rp-btn primary" disabled={busy} onClick={() => {
        setBusy(true);
        setErr("");
        Promise.resolve(onAction("rereview", first.pr)).then(res => {
          if (!res || !res.ok) { setBusy(false); setErr((res && res.error) || "Couldn't start"); }
        }).catch(e => { setBusy(false); setErr(String(e)); });
      }}>{busy ? "Starting…" : "Re-review"}</button>
    </div>
  );
}

function PrGroup({ items, onRecheck, onAction, leavingIds }) {
  const [open, setOpen] = useStateR(false);
  const first = items[0];
  const checking = items.some(isChecking);
  const allLeaving = items.every(r => leavingIds.has(r.id)) && first.prRereview !== "ready";
  const latestAt = items.reduce((m, r) => (r.lastCommentAt > m ? r.lastCommentAt : m), "");
  const title = (first.prTitle || "").replace(/\s*\[[A-Z]+-\d+\]\s*/g, " ").replace(/^\w+\([^)]*\):\s*/, "").trim();
  const worst = items.reduce((w, r) => Math.min(w, sevRank(r)), SEVERITY_ORDER.length);
  const worstSev = SEVERITY_ORDER[worst] || "";
  return (
    <section className={`rp-pr ${open ? "is-open" : ""} ${allLeaving ? "is-leaving" : ""}`}>
      <div className="rp-pr__hd" onClick={() => setOpen(!open)} role="button" aria-expanded={open}>
        <span className={`rp-pr__rail sev-${worstSev}`} />
        <div className="rp-pr__main">
          <div className="rp-pr__meta mono">{first.repo} #{first.number} · {first.prAuthor}</div>
          <div className="rp-pr__title" title={first.prTitle}>{title || first.prTitle}</div>
        </div>
        <div className="rp-pr__side">
          <a href="#" onClick={e => { e.preventDefault(); e.stopPropagation(); openExternal(first.prUrl); }}>GitHub ↗</a>
          <span>
            {first.prThreads > 0 ? `${first.prResolved}/${first.prThreads} resolved · ` : ""}{ago(latestAt)}
            {!open && <> · <b>{items.length}</b> {items.length === 1 ? "reply" : "replies"}</>}
          </span>
        </div>
        <span className={`rp-chev ${open ? "is-open" : ""}`}>{open ? "⌄" : "›"}</span>
        {checking && <span className="rp-pr__progress" title="Claude is checking replies on this PR" />}
      </div>
      <RereviewBar first={first} onAction={onAction} />
      {open && (
        <div className="rp-pr__threads">
          {items.map(r => <Thread key={r.id} r={r} onRecheck={onRecheck} onAction={onAction} leaving={leavingIds.has(r.id)} />)}
        </div>
      )}
    </section>
  );
}

const LEAVE_MS = 2400;

function useLeaving(replies) {
  const prev = useRefR(new Map());
  const [leaving, setLeaving] = useStateR(() => new Map());
  useEffectR(() => {
    const justDone = [];
    replies.forEach(r => {
      const was = prev.current.get(r.id);
      if (was && REPLY_FILTERS[0].match({ status: was }) && !REPLY_FILTERS[0].match(r)) justDone.push(r.id);
    });
    prev.current = new Map(replies.map(r => [r.id, r.status]));
    if (!justDone.length) return;
    setLeaving(m => { const n = new Map(m); justDone.forEach(id => n.set(id, Date.now())); return n; });
    setTimeout(() => setLeaving(m => {
      const n = new Map(m);
      justDone.forEach(id => n.delete(id));
      return n;
    }), LEAVE_MS);
  }, [replies]);
  return leaving;
}

function RepliesView({ replies, error, claudeError, onRecheck, onAction, loading }) {
  const [filter, setFilter] = useStateR("triage");
  const leaving = useLeaving(replies);
  const leavingIds = useMemoR(() => new Set(leaving.keys()), [leaving]);

  const groups = useMemoR(() => {
    const f = REPLY_FILTERS.find(x => x.id === filter) || REPLY_FILTERS[0];
    const byPr = new Map();
    replies.filter(r => f.match(r) || (filter === "triage" && (r.prRereview === "ready" || leavingIds.has(r.id)))).forEach(r => {
      if (!byPr.has(r.pr)) byPr.set(r.pr, []);
      byPr.get(r.pr).push(r);
    });
    const latest = items => items.reduce((m, r) => (r.lastCommentAt > m ? r.lastCommentAt : m), "");
    return [...byPr.values()]
      .map(items => items.sort((a, b) => (sevRank(a) - sevRank(b)) || (a.lastCommentAt < b.lastCommentAt ? 1 : -1)))
      .sort((a, b) => (latest(a) < latest(b) ? 1 : -1));
  }, [replies, filter, leavingIds]);

  const triage = useMemoR(() => replies.filter(REPLY_FILTERS[0].match).length, [replies]);

  return (
    <div className="replies">
      <div className="replies__inner">
        <header className="rp-head">
          <h1 className="rp-head__ttl">Replies</h1>
          <div className="rp-tabs">
            {REPLY_FILTERS.map(f => (
              <button key={f.id} className={filter === f.id ? "on" : ""} onClick={() => setFilter(f.id)}>
                {f.label}{f.id === "triage" && triage > 0 && <span className="rp-tabs__n">{triage}</span>}
              </button>
            ))}
          </div>
        </header>
        {error && <div className="rp-banner is-err">{error}</div>}
        {claudeError && <div className="rp-banner is-warn">{claudeError}</div>}
        {loading && <SkeletonCards />}
        {!loading && groups.length === 0 && (
          <div className="inbox__empty">
            {filter === "triage" ? "Nothing to triage. Replies to your review comments show up here." : "Nothing here."}
          </div>
        )}
        <div className="rp-prs">
          {!loading && groups.map(items => <PrGroup key={items[0].pr} items={items} onRecheck={onRecheck} onAction={onAction} leavingIds={leavingIds} />)}
        </div>
      </div>
    </div>
  );
}

Object.assign(window, { RepliesView });
