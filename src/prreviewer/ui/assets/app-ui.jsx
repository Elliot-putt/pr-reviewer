// app-ui.jsx — shared UI components for the PR auto-review app
const { useState, useEffect, useRef } = React;

/* ---------- status vocabulary ---------- */
const STATUS = {
  waiting:         { label: "Waiting for review",  short: "Waiting",         tone: "yellow", icon: "clock-countdown",    tooltip: "Waiting for you to start a code review" },
  checkout:        { label: "Checking out",        short: "Checking out",    tone: "gray",   icon: "git-branch", spin: true, tooltip: "Checking out the PR branch locally" },
  reviewing:       { label: "Reviewing",           short: "Reviewing",       tone: "blue",   icon: "magnifying-glass", pulse: true, tooltip: "Claude is actively reviewing the code" },
  ready:           { label: "Review ready",        short: "Ready",           tone: "green",  icon: "check-circle",       tooltip: "Review complete — ready to post to GitHub" },
  posted:          { label: "Review posted",       short: "Posted",          tone: "muted",  icon: "paper-plane-tilt",   tooltip: "Review has been posted to GitHub" },
  changes:         { label: "Changes requested",   short: "Changes",         tone: "red",    icon: "arrow-counter-clockwise", tooltip: "Changes were requested on this PR" },
  needs_attention: { label: "Needs attention",     short: "Needs attention", tone: "orange", icon: "chat-circle-dots",   tooltip: "Your PR has unresolved review comments — click Address Comments to work through them" },
  merged:          { label: "Merged",              short: "Merged",          tone: "purple", icon: "git-merge",          tooltip: "This PR has been merged" },
  closed:          { label: "Closed",              short: "Closed",          tone: "muted",  icon: "x-circle",           tooltip: "This PR was closed without merging" },
};

function Spinner({ size = 12, color }) {
  return <span className="spinner" style={{ width: size, height: size, borderColor: color ? `${color}40` : undefined, borderTopColor: color }} />;
}

/* StatusBadge — treatment: 'pill' | 'dot' | 'glyph' */
function StatusBadge({ status, treatment = "pill", size = "md" }) {
  const s = STATUS[status] || STATUS.waiting;
  const cls = `tone-${s.tone}`;
  const tip = s.tooltip || s.label;

  if (treatment === "dot") {
    return (
      <span className={`sb-dot ${cls} ${size}`} title={tip}>
        <span className={`dotmark ${s.pulse ? "pulse" : ""}`}>
          {s.spin ? <Spinner size={9} /> : null}
        </span>
        <span className="sb-dot__label">{s.short}</span>
      </span>
    );
  }
  if (treatment === "glyph") {
    return (
      <span className={`sb-glyph ${cls} ${size}`} title={tip}>
        {s.spin ? <Spinner size={11} /> : <Icon name={s.icon} className={s.pulse ? "pulse-i" : ""} />}
        <span>{s.short}</span>
      </span>
    );
  }
  // pill (default)
  return (
    <span className={`sb-pill ${cls} ${size} ${s.pulse ? "is-pulse" : ""}`} title={tip}>
      {s.spin ? <Spinner size={10} /> : <span className={`pdot ${s.pulse ? "pulse" : ""}`} />}
      <span>{size === "lg" ? s.label : s.short}</span>
    </span>
  );
}

function Avatar({ author, size = 26 }) {
  const known = AUTHORS[author];
  const initials = known
    ? known.initials
    : (author || "?").replace(/-/g, " ").split(" ").map(w => w[0] || "").join("").toUpperCase().slice(0, 2) || "?";
  const tint = known ? known.tint : "#6b7280";
  const label = known ? known.name : author;
  return (
    <span className="avatar" style={{ width: size, height: size, background: tint, fontSize: size * 0.4 }} title={label}>
      {initials}
    </span>
  );
}

/* ---------- PR row ---------- */
function PRRow({ pr, active, onClick, density = "regular", badge = "pill", myLogin = "" }) {
  const isMyPr = myLogin && pr.author === myLogin;
  const roleLabel = myLogin ? (isMyPr ? "My PR" : "To Review") : null;
  const roleTip = isMyPr
    ? "This is your PR — use Address Comments to respond to review feedback"
    : "Someone else's PR — run a code review to check the diff";
  return (
    <button className={`prrow ${density} ${active ? "active" : ""} ${pr.isNew ? "isnew" : ""}`} onClick={onClick}>
      <span className="prrow__rail" />
      <div className="prrow__main">
        <div className="prrow__top">
          <span className="prrow__repo mono">{pr.repo} <span className="prrow__num">#{pr.number}</span></span>
          <div className="prrow__topmeta">
            {roleLabel && (
              <span className={`prrow__role ${isMyPr ? "mine" : "review"}`} title={roleTip}>{roleLabel}</span>
            )}
            <span className="prrow__time">{pr.landed}</span>
          </div>
        </div>
        <div className="prrow__title">{pr.title}</div>
        <div className="prrow__bottom">
          <span className="prrow__who"><Avatar author={pr.author} size={18} /> {(AUTHORS[pr.author] || {}).name || pr.author}</span>
          {density !== "compact" && (
            <span className="prrow__diff mono">
              <span className="add">+{pr.additions}</span>
              <span className="del">−{pr.deletions}</span>
            </span>
          )}
        </div>
        {density === "comfortable" && (
          <div className="prrow__branch mono"><Icon name="git-branch" /> {pr.branch}</div>
        )}
      </div>
      <div className="prrow__status">
        <StatusBadge status={pr.status} treatment={badge} />
      </div>
    </button>
  );
}

/* ---------- Sidebar ---------- */
function Sidebar({ view, setView, counts, listening, setListening, slackConnected, channelName }) {
  return (
    <aside className="sidebar">
      <div className="sidebar__brand">
        <span className="appicon"><span className="appicon__mark" dangerouslySetInnerHTML={{ __html: window.SPECTRE_MARK }} /></span>
        <div>
          <div className="appicon__name">Review</div>
          <div className="appicon__sub">Spectre · PR triage</div>
        </div>
      </div>

      <nav className="sidenav">
        <button className={`sidenav__item ${view === "inbox" ? "active" : ""}`} onClick={() => setView("inbox")}>
          <Icon name="envelope" /> <span>Inbox</span>
          {counts.open > 0 && <span className="sidenav__count">{counts.open}</span>}
        </button>
        <button className={`sidenav__item ${view === "settings" ? "active" : ""}`} onClick={() => setView("settings")}>
          <Icon name="gear-six" /> <span>Settings</span>
        </button>
      </nav>

      <div className="sidebar__spacer" />

      <div className="slackcard">
        <div className="slackcard__row">
          <span className="slackcard__logo"><Icon name="slack-logo" /></span>
          <div className="slackcard__txt">
            <div className="slackcard__ttl">Slack</div>
            <div className="slackcard__meta">
              <span className={`livedot ${slackConnected ? "on" : "off"}`} />
              {slackConnected ? "Socket Mode · live" : "Disconnected"}
            </div>
          </div>
        </div>
        <div className="slackcard__chan mono"><Icon name="hash" />{channelName || "no channel set"}</div>
      </div>

      <button className={`listen ${listening ? "on" : ""}`} onClick={() => setListening(!listening)}>
        <span className="listen__l">
          <Icon name={listening ? "listening" : "paused"} />
          {listening ? "Listening for PRs" : "Paused"}
        </span>
        <span className={`switch ${listening ? "on" : ""}`}><span className="switch__knob" /></span>
      </button>
    </aside>
  );
}

Object.assign(window, { STATUS, Spinner, StatusBadge, Avatar, PRRow, Sidebar });
