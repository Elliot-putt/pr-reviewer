// app-main.jsx — app state, the review flow, tweaks
const { useState: useStateM, useEffect: useEffectM, useRef: useRefM, useMemo } = React;

const TWEAK_DEFAULTS = /*EDITMODE-BEGIN*/{
  "rowDensity": "regular",
  "badgeTreatment": "pill",
  "workspaceLayout": "split",
  "accent": "#5359EA",
  "terminalTheme": "midnight"
}/*EDITMODE-END*/;

function makeFilters(githubLogin) {
  return [
    { id: "all",             label: "All",              tooltip: "Show all tracked PRs" },
    { id: "to_review",       label: "To Review",        tooltip: "PRs from others waiting for your code review",
      match: p => (!githubLogin || p.author !== githubLogin) && ["waiting", "checkout", "reviewing"].includes(p.status) },
    { id: "needs_attention", label: "Needs Attention",  tooltip: "Your PRs with unresolved review comments",
      // Keep your own PRs visible here while comments are being addressed —
      // starting the session moves status to checkout/reviewing.
      match: p => p.status === "needs_attention"
        || (!!githubLogin && p.author === githubLogin && ["checkout", "reviewing"].includes(p.status)) },
    { id: "done",            label: "Done",             tooltip: "Code reviewed or comments addressed",
      match: p => ["ready", "posted"].includes(p.status) },
  ];
}
const FILTERS = makeFilters(""); // default (no login) — used by demo flow

function fullSession(pr, posted) {
  const lines = reviewScript(pr);
  const comments = PROPOSED_COMMENTS.map(c => ({ ...c, checked: posted ? true : c.checked }));
  return { lines, running: false, comments, revealCount: comments.length, posted: !!posted, decision: posted ? "approve" : null };
}

function App() {
  const [t, setTweak] = useTweaks(TWEAK_DEFAULTS);
  const [view, setView] = useStateM("inbox");
  const [prs, setPrs] = useStateM(() => SEED_PRS.map(p => ({ ...p })));
  const [selectedId, setSelectedId] = useStateM(null);
  const [filter, setFilter] = useStateM("all");
  const [listening, setListening] = useStateM(true);
  const [slackConnected, setSlackConnected] = useStateM(true);
  const [menuOpen, setMenuOpen] = useStateM(false);
  const [toast, setToast] = useStateM(null);
  const [sessions, setSessions] = useStateM({}); // prId -> session
  const timers = useRefM([]);
  const incomingFired = useRefM(false);

  // apply accent + terminal theme to document
  useEffectM(() => {
    document.documentElement.style.setProperty("--color-primary-600", t.accent);
    document.documentElement.style.setProperty("--color-primary-700", shade(t.accent, -16));
    document.documentElement.setAttribute("data-term", t.terminalTheme);
  }, [t.accent, t.terminalTheme]);

  function pushTimer(fn, ms) { const id = setTimeout(fn, ms); timers.current.push(id); return id; }
  function clearTimers() { timers.current.forEach(clearTimeout); timers.current = []; }
  useEffectM(() => () => clearTimers(), []);

  const setStatus = (id, status, extra = {}) =>
    setPrs(prev => prev.map(p => p.id === id ? { ...p, status, ...extra } : p));

  function updateSession(id, patch) {
    setSessions(prev => ({ ...prev, [id]: { ...prev[id], ...(typeof patch === "function" ? patch(prev[id]) : patch) } }));
  }

  // animate the review flow for a freshly-opened waiting PR
  function runFlow(pr) {
    clearTimers();
    const script = reviewScript(pr);
    const comments = PROPOSED_COMMENTS.map(c => ({ ...c }));
    updateSession(pr.id, { lines: [], running: true, comments, revealCount: 0, posted: false, decision: null });
    setStatus(pr.id, "checkout");

    pushTimer(() => {
      setStatus(pr.id, "reviewing");
      // type lines
      let acc = [];
      script.forEach((ln, i) => {
        const delay = 700 + i * 240 + (ln.k === "blank" ? -120 : 0);
        pushTimer(() => {
          acc = [...acc, ln];
          updateSession(pr.id, { lines: acc });
        }, delay);
      });
      const doneAt = 700 + script.length * 240 + 200;
      pushTimer(() => {
        updateSession(pr.id, { running: false });
        setStatus(pr.id, "ready");
        // reveal comments
        comments.forEach((_, i) => pushTimer(() => updateSession(pr.id, { revealCount: i + 1 }), 250 + i * 320));
      }, doneAt);
    }, 1100);
  }

  function openPR(id) {
    setView("inbox");
    setMenuOpen(false);
    setSelectedId(id);
    const pr = prs.find(p => p.id === id);
    if (!pr) return;
    if (sessions[id]) return; // already has a session
    if (pr.status === "posted") { updateSession(id, fullSession(pr, true)); return; }
    if (pr.status === "ready") { updateSession(id, fullSession(pr, false)); return; }
    // waiting / checkout / reviewing → run animated flow
    runFlow(pr);
  }

  function toggleComment(cid) {
    if (!selectedId) return;
    updateSession(selectedId, s => ({ comments: s.comments.map(c => c.id === cid ? { ...c, checked: !c.checked } : c) }));
  }

  function postComments() {
    if (!selectedId) return;
    const sess = sessions[selectedId];
    const n = sess.comments.filter(c => c.checked).length;
    updateSession(selectedId, { posted: true });
    setStatus(selectedId, "posted", { postedCount: n });
  }
  function setDecision(d) { if (selectedId) updateSession(selectedId, { decision: d }); }

  function simulateNewPR() {
    if (incomingFired.current) return;
    incomingFired.current = true;
    setMenuOpen(false);
    const pr = { ...INCOMING_PR, isNew: true };
    setPrs(prev => [pr, ...prev]);
    setToast(pr);
    pushTimer(() => setToast(null), 6500);
    pushTimer(() => setPrs(prev => prev.map(p => p.id === pr.id ? { ...p, isNew: false } : p)), 1800);
  }

  const counts = useMemo(() => ({
    open: prs.filter(p => !["posted", "merged", "closed"].includes(p.status)).length,
    toReview: prs.filter(p => ["waiting", "checkout", "reviewing"].includes(p.status)).length,
    attention: prs.filter(p => p.status === "needs_attention").length,
  }), [prs]);

  const filters = useMemo(() => makeFilters(""), []);

  const filtered = useMemo(() => {
    const f = filters.find(x => x.id === filter);
    return f && f.match ? prs.filter(p => f.match(p)) : prs;
  }, [prs, filter, filters]);

  const selected = prs.find(p => p.id === selectedId);
  const sess = selectedId ? sessions[selectedId] : null;

  return (
    <div className="desktop">
      {/* macOS menu bar */}
      <div className="menubar">
        <div className="menubar__l">
          <span className="menubar__apple">⌘</span>
          <span className="menubar__app">Review</span>
          <span className="menubar__menu">File</span>
          <span className="menubar__menu">Edit</span>
          <span className="menubar__menu">View</span>
          <span className="menubar__menu">Window</span>
          <span className="menubar__menu">Help</span>
        </div>
        <div className="menubar__r">
          <button className={`menubar__status ${menuOpen ? "open" : ""}`} onClick={() => setMenuOpen(o => !o)} title="Review">
            <span className="menubar__mark" dangerouslySetInnerHTML={{ __html: window.SPECTRE_MARK }} />
            {counts.open > 0 && <span className="menubar__badge">{counts.open}</span>}
          </button>
          <Icon name="broadcast" />
          <Icon name="magnifying-glass" />
          <Icon name="list" />
          <span className="menubar__clock">{new Date().toLocaleDateString(undefined,{weekday:"short",month:"short",day:"numeric"})} · 9:41</span>
        </div>
        {menuOpen && (
          <>
            <div className="menubar__scrim" onClick={() => setMenuOpen(false)} />
            <MenuDropdown prs={prs} counts={counts} listening={listening} setListening={setListening}
              onOpen={() => setMenuOpen(false)} onPick={openPR} onSimulate={simulateNewPR} />
          </>
        )}
      </div>

      {/* main window */}
      <div className="window">
        <div className="window__bar">
          <span className="lights"><i className="r" /><i className="y" /><i className="g" /></span>
          <span className="window__title">Review — {view === "settings" ? "Settings" : "Inbox"}</span>
          <span className="window__rt">
            <button className="winbtn" onClick={simulateNewPR} title="Simulate a new PR landing in Slack"><Icon name="flask" /> Simulate PR</button>
          </span>
        </div>
        <div className="window__body">
          <Sidebar view={view} setView={setView} counts={counts} listening={listening} setListening={setListening} slackConnected={slackConnected} />
          {view === "inbox" ? (
            <>
              <div className="inbox">
                <div className="inbox__hd">
                  <div className="inbox__ttltop">
                    <h1 className="inbox__ttl">Inbox</h1>
                    <span className="inbox__count">{counts.open} open</span>
                  </div>
                  <div className="segfilter">
                    {filters.map(f => (
                      <button key={f.id} className={filter === f.id ? "on" : ""} onClick={() => setFilter(f.id)} title={f.tooltip}>{f.label}</button>
                    ))}
                  </div>
                </div>
                <div className="inbox__list">
                  {filtered.length === 0 && <div className="inbox__empty">Nothing here right now.</div>}
                  {filtered.map(pr => (
                    <PRRow key={pr.id} pr={pr} active={pr.id === selectedId} onClick={() => openPR(pr.id)}
                      density={t.rowDensity} badge={t.badgeTreatment} />
                  ))}
                </div>
              </div>
              <ReviewWorkspace
                pr={selected} lines={sess ? sess.lines : []} running={sess ? sess.running : false}
                comments={sess ? sess.comments : []} onToggle={toggleComment} revealCount={sess ? sess.revealCount : 0}
                layout={t.workspaceLayout} onPost={postComments} posted={sess ? sess.posted : false}
                decision={sess ? sess.decision : null} setDecision={setDecision} />
            </>
          ) : (
            <SettingsView listening={listening} setListening={setListening} slackConnected={slackConnected} setSlackConnected={setSlackConnected} />
          )}
        </div>
      </div>

      <Toast toast={toast} onClick={() => toast && openPR(toast.id)} onClose={() => setToast(null)} />

      <TweaksPanel>
        <TweakSection label="Inbox" />
        <TweakRadio label="Row density" value={t.rowDensity} options={["compact", "regular", "comfortable"]} onChange={v => setTweak("rowDensity", v)} />
        <TweakRadio label="Status badge" value={t.badgeTreatment} options={["pill", "dot", "glyph"]} onChange={v => setTweak("badgeTreatment", v)} />
        <TweakSection label="Review workspace" />
        <TweakRadio label="Layout" value={t.workspaceLayout} options={["split", "stacked"]} onChange={v => setTweak("workspaceLayout", v)} />
        <TweakSelect label="Terminal theme" value={t.terminalTheme} options={["midnight", "carbon", "indigo"]} onChange={v => setTweak("terminalTheme", v)} />
        <TweakSection label="Accent" />
        <TweakColor label="Primary" value={t.accent} options={["#5359EA", "#4F6BED", "#2F6F6A", "#5B5570"]} onChange={v => setTweak("accent", v)} />
      </TweaksPanel>
    </div>
  );
}

function shade(hex, amt) {
  const n = parseInt(hex.slice(1), 16);
  let r = (n >> 16) + amt, g = ((n >> 8) & 255) + amt, b = (n & 255) + amt;
  r = Math.max(0, Math.min(255, r)); g = Math.max(0, Math.min(255, g)); b = Math.max(0, Math.min(255, b));
  return "#" + ((1 << 24) + (r << 16) + (g << 8) + b).toString(16).slice(1);
}

// Root is rendered by app-bridge.jsx (BridgedApp). App() is kept for reference.
