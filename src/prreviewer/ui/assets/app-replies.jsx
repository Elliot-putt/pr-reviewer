const { useState: useStateR, useMemo: useMemoR } = React;

const REPLY_FILTERS = [
  { id: "triage", label: "To triage", match: r => ["pending", "verdict_ready", "verdict_error"].includes(r.status) },
  { id: "done", label: "Done", match: r => ["done", "dismissed"].includes(r.status) },
  { id: "all", label: "All", match: () => true },
];

function timeAgo(iso) {
  if (!iso) return "";
  const secs = Math.max(0, (Date.now() - new Date(iso).getTime()) / 1000);
  if (secs < 60) return "just now";
  if (secs < 3600) return `${Math.floor(secs / 60)} min ago`;
  if (secs < 86400) return `${Math.floor(secs / 3600)} h ago`;
  return `${Math.floor(secs / 86400)} d ago`;
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

function CommentBody({ c }) {
  if (c.bodyHtml) {
    return <div className="rmsg__body md" onClick={openLinkInBrowser} dangerouslySetInnerHTML={{ __html: c.bodyHtml }} />;
  }
  return <span className="rmsg__body plain">{c.body}</span>;
}

function ReplyCard({ r }) {
  const where = r.path ? `${r.path}${r.line ? `:${r.line}` : ""}` : "";
  return (
    <div className={`rcard ${["done", "dismissed"].includes(r.status) ? "is-done" : ""}`}>
      <div className="rcard__top">
        <span className="rcard__path mono" title={where}>{where}</span>
        <span className="rcard__meta">
          {r.outdated && <span className="rcard__tag" title="The lines changed since your comment">Outdated</span>}
          {r.status !== "pending" && <span className="rcard__tag">{r.status.replace("_", " ")}</span>}
          <span className="rcard__time">{timeAgo(r.lastCommentAt)}</span>
        </span>
      </div>
      <div className="rcard__convo">
        {r.comments.map((c, i) => (
          <div key={i} className={`rmsg ${c.mine ? "mine" : "theirs"}`}>
            <span className="rmsg__who" title={c.author}>{c.mine ? "You" : "Them"}</span>
            <CommentBody c={c} />
          </div>
        ))}
      </div>
      <div className="rcard__actions">
        <button className="btn secondary sm" onClick={() => openExternal(r.threadUrl || r.prUrl)}>
          <Icon name="github-logo" /> Open thread <Icon name="arrow-up-right" />
        </button>
      </div>
    </div>
  );
}

function ReplyGroup({ items }) {
  const first = items[0];
  return (
    <section className="rgroup">
      <header className="rgroup__hd">
        <a href="#" className="rgroup__repo mono" onClick={e => { e.preventDefault(); openExternal(first.prUrl); }}>
          {first.repo} <span className="prrow__num">#{first.number}</span>
        </a>
        <span className="rgroup__title">{first.prTitle}</span>
        <span className="rgroup__author">{first.prAuthor}</span>
      </header>
      <div className="rgroup__list">
        {items.map(r => <ReplyCard key={r.id} r={r} />)}
      </div>
    </section>
  );
}

function RepliesView({ replies, error }) {
  const [filter, setFilter] = useStateR("triage");

  const groups = useMemoR(() => {
    const f = REPLY_FILTERS.find(x => x.id === filter) || REPLY_FILTERS[0];
    const byPr = new Map();
    replies.filter(f.match).forEach(r => {
      if (!byPr.has(r.pr)) byPr.set(r.pr, []);
      byPr.get(r.pr).push(r);
    });
    const latest = items => items.reduce((m, r) => (r.lastCommentAt > m ? r.lastCommentAt : m), "");
    return [...byPr.values()]
      .map(items => items.sort((a, b) => (a.lastCommentAt < b.lastCommentAt ? 1 : -1)))
      .sort((a, b) => (latest(a) < latest(b) ? 1 : -1));
  }, [replies, filter]);

  const triageCount = replies.filter(REPLY_FILTERS[0].match).length;

  return (
    <div className="replies">
      <div className="replies__inner">
        <header className="replies__hd">
          <div className="inbox__ttltop">
            <h1 className="inbox__ttl">Replies</h1>
            <span className="inbox__count">{triageCount} to triage</span>
          </div>
          <div className="segfilter">
            {REPLY_FILTERS.map(f => (
              <button key={f.id} className={filter === f.id ? "on" : ""} onClick={() => setFilter(f.id)}>{f.label}</button>
            ))}
          </div>
        </header>
        {error && <div className="setsec__err replies__err"><Icon name="warning" /> {error}</div>}
        {groups.length === 0 && (
          <div className="inbox__empty">
            {filter === "triage" ? "No replies waiting. Replies to your review comments show up here." : "Nothing here."}
          </div>
        )}
        {groups.map(items => <ReplyGroup key={items[0].pr} items={items} />)}
      </div>
    </div>
  );
}

Object.assign(window, { RepliesView });
