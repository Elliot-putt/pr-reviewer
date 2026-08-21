// app-bridge.jsx — real pywebview API wiring and xterm.js terminal component
// Loaded AFTER app-main.jsx so we can patch window globals safely.

const { useState: useStateBr, useEffect: useEffectBr, useRef: useRefBr } = React;

/* --------------------------------------------------------------------------
   XTermTerminal — real PTY-backed terminal using xterm.js over websocket
   -------------------------------------------------------------------------- */
function XTermTerminal({ prId, wsPort, running }) {
  const containerRef = useRefBr(null);
  const containerId = `xterm-${prId.replace(/[^a-z0-9]/gi, '-')}`;

  useEffectBr(() => {
    if (!prId || !containerRef.current) return;
    window.__mountXterm && window.__mountXterm(containerId, prId, wsPort);
    return () => { window.__destroyXterm && window.__destroyXterm(prId); };
  }, [prId, wsPort]);

  return (
    <div className="term">
      <div className="term__bar">
        <span className="term__dots"><i /><i /><i /></span>
        <span className="term__title mono">claude — code-review</span>
        <span className="term__tag">
          {running
            ? <><Spinner size={10} color="#7dd3fc" /> running</>
            : <><Icon name="check" /> idle</>}
        </span>
      </div>
      <div id={containerId} ref={containerRef}
        style={{ flex: 1, minHeight: 0, overflow: 'hidden', background: '#0d0f14' }} />
    </div>
  );
}

/* --------------------------------------------------------------------------
   BridgedReviewWorkspace — replaces Terminal with XTermTerminal when there
   is a real PTY session (indicated by wsPort being set), and wires openPR to
   call the Python API.
   -------------------------------------------------------------------------- */
function IdleCountdown({ deadline }) {
  const [now, setNow] = React.useState(Date.now());
  React.useEffect(() => {
    if (!deadline) return;
    const id = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(id);
  }, [deadline]);
  if (!deadline) return null;
  const left = Math.floor(deadline - now / 1000);
  if (left <= 0) return null;
  const m = Math.floor(left / 60);
  const s = String(left % 60).padStart(2, "0");
  return (
    <span className="ws__idle mono" title="Idle session — auto-closes unless you type in the terminal">
      <Icon name="clock-countdown" /> session closes in {m}:{s}
    </span>
  );
}

function WsFooter({ pr, sess, posted, githubLogin, onApprove, onMarkReviewed, onAddressComments, onRequestReview }) {
  const alreadyApproved = posted && (pr.reviewDecision === "approved" || (sess && sess.decision === "approve"));
  const canApprove = githubLogin && pr.author && pr.author !== githubLogin;
  const isMyPr = githubLogin && pr.author === githubLogin;
  const reviewedLabel = pr.reviewDecision === "changes_requested" ? "Changes requested" : "Review complete";
  return (
    <div className="ws__decision">
      <IdleCountdown deadline={pr.sessionDeadline} />
      {isMyPr ? (
        <button className={"dseg" + (posted ? " disabled" : "")} disabled={posted} onClick={onRequestReview}>
          <Icon name="git-pull-request" /> {posted ? "Review requested" : "Re-request Review"}
        </button>
      ) : (
        <>
          {canApprove && (
            <button className={"dseg" + (alreadyApproved ? " disabled on approve" : "")} disabled={alreadyApproved} onClick={onApprove}>
              <Icon name="check-circle" /> {alreadyApproved ? "Approved" : "Approve"}
            </button>
          )}
          <button className={"dseg" + (posted ? " disabled" : "")} disabled={posted} onClick={onMarkReviewed}>
            <Icon name="check-square" /> {posted ? reviewedLabel : "Mark as reviewed"}
          </button>
        </>
      )}
    </div>
  );
}

function BridgedReviewWorkspace({ pr, sess, lines, running, comments, onToggle, revealCount, layout, onPost, posted, decision, setDecision, wsPort, hasRealSession, onStartReview, sessionStarted, githubLogin, onApprove, onMarkReviewed, onAddressComments, onRequestReview }) {
  if (!pr) return <EmptyWorkspace />;
  const a = AUTHORS[pr.author];
  const checkedCount = comments.filter(c => c.checked).length;
  const showComments = pr.status === "ready" || pr.status === "posted";
  const isActive = pr.status === "checkout" || pr.status === "reviewing" || pr.status === "ready";
  const isMyPr = githubLogin && pr.author === githubLogin;
  const termTitle = isMyPr ? "claude — address-comments" : "claude — code-review";
  const startAction = isMyPr ? onAddressComments : onStartReview;
  const startLabel = isMyPr ? "Address Comments" : "Start Review";
  const startHint = isMyPr ? "Checks out the branch and runs the address-comments skill" : "Checks out the branch and runs the review skill";
  const startIcon = isMyPr ? "chat-circle-dots" : "magnifying-glass";

  // Keep xterm mounted for the lifetime of the real PTY session, including after marking reviewed.
  const ptyActive = hasRealSession && wsPort;

  const termEl = ptyActive
    ? <XTermTerminal prId={pr.id} wsPort={wsPort} running={running} />
    : sessionStarted && !hasRealSession
      ? <Terminal lines={lines} running={running} />
      : hasRealSession && !isActive
        ? (
          // Python call in flight — waiting for checkout status push
          <div className="term">
            <div className="term__bar">
              <span className="term__dots"><i /><i /><i /></span>
              <span className="term__title mono">{termTitle}</span>
              <span className="term__tag"><Spinner size={10} color="#7dd3fc" /> starting…</span>
            </div>
            <div className="term__body term__body--empty" style={{color:'var(--color-default-400)',fontSize:13}}>
              Setting up session…
            </div>
          </div>
        )
        : (
          <div className="term">
            <div className="term__bar">
              <span className="term__dots"><i /><i /><i /></span>
              <span className="term__title mono">{termTitle}</span>
              <span className="term__tag"><Icon name="check" /> idle</span>
            </div>
            <div className="term__body term__body--empty">
              <button className="startbtn" onClick={startAction}>
                <Icon name={startIcon} /> {startLabel}
              </button>
              <div className="startbtn__hint">{startHint}</div>
            </div>
          </div>
        );

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
          <span className="ws__diff mono"><span className="add">+{pr.additions}</span> <span className="del">−{pr.deletions}</span> · {pr.files || pr.filesChanged} files</span>
          <a className="ws__gh" href="#" onClick={e => { e.preventDefault(); const url = `https://github.com/${pr.owner}/${pr.repo}/pull/${pr.number}`; if (window.pywebview && window.pywebview.api && window.pywebview.api.open_url) { window.pywebview.api.open_url(url); } else { window.open(url, '_blank'); } }}><Icon name="github-logo" />Open on GitHub <Icon name="arrow-up-right" /></a>
        </div>
      </header>

      <div className={`ws__work layout-fullterm`}>
        {termEl}
      </div>

      <footer className="ws__foot">
        <WsFooter
          pr={pr} sess={sess} posted={posted} githubLogin={githubLogin}
          onApprove={onApprove} onMarkReviewed={onMarkReviewed} onAddressComments={onAddressComments}
          onRequestReview={onRequestReview}
        />
      </footer>
    </section>
  );
}

/* --------------------------------------------------------------------------
   BridgedApp — wraps App, patches real PR events and Python API calls
   -------------------------------------------------------------------------- */
function BridgedApp() {
  const [t, setTweak] = useTweaks(TWEAK_DEFAULTS);
  const [view, setView] = useStateBr("inbox");
  // Start with seed data; swap to empty once we know we're in real (configured) mode.
  // Start empty — real PRs arrive from Python; demo data is only seeded if the
  // app turns out to be unconfigured (prevents dummy rows flashing on startup).
  const [prs, setPrs] = useStateBr([]);
  const [isConfigured, setIsConfigured] = useStateBr(false);
  const [selectedId, setSelectedId] = useStateBr(null);
  const [filter, setFilter] = useStateBr("to_review");
  const [listening, setListening] = useStateBr(true);
  function toggleListening(val) {
    setListening(val);
    if (window.pywebview && window.pywebview.api && window.pywebview.api.set_listening) {
      window.pywebview.api.set_listening(val);
    }
  }
  const [slackConnected, setSlackConnected] = useStateBr(false);
  const [menuOpen, setMenuOpen] = useStateBr(false);
  const [toast, setToast] = useStateBr(null); // kept for demo flow only
  const [sessions, setSessions] = useStateBr({});
  const [wsPort, setWsPort] = useStateBr(8766);
  const [realSessions, setRealSessions] = useStateBr({}); // prId -> bool (has live PTY)
  const [backfilling, setBackfilling] = useStateBr(false);
  const [lastRefreshed, setLastRefreshed] = useStateBr(null);
  const [updateInfo, setUpdateInfo] = useStateBr(null);  // {latest, url} from Python update check
  const [githubLogin, setGithubLogin] = useStateBr("");
  const timers = useRefBr([]);
  const incomingFired = useRefBr(false);

  // Apply accent + terminal theme
  useEffectBr(() => {
    document.documentElement.style.setProperty("--color-primary-600", t.accent);
    document.documentElement.style.setProperty("--color-primary-700", shade(t.accent, -16));
    document.documentElement.setAttribute("data-term", t.terminalTheme);
  }, [t.accent, t.terminalTheme]);

  // Deep-link: if ?pr=3871 is in the URL, auto-select that PR once PRs are loaded
  const deepLinkPr = React.useMemo(() => {
    const params = new URLSearchParams(window.location.search);
    const n = params.get("pr");
    return n ? Number(n) : null;
  }, []);

  // Load settings from Python API, then hydrate PR list if configured
  useEffectBr(() => {
    function load() {
      window.__pyApi.getSettings().then(cfg => {
        if (!cfg) return;
        setSlackConnected(!!cfg.slackConnected);
        if (cfg.listening != null) setListening(!!cfg.listening);
        if (cfg.wsPort) setWsPort(cfg.wsPort);
        if (cfg.githubLogin) setGithubLogin(cfg.githubLogin);
        if (cfg.isConfigured) {
          setIsConfigured(true);
          // Load whatever is already in the store (may be empty before backfill)
          window.__pyApi.getPrs().then(livePrs => {
            const visible = (livePrs || []).filter(p => p.status !== "merged" && p.status !== "closed");
            setPrs(visible.length ? visible : []);
            // Auto-select PR from deep-link notification
            const deepLinked = deepLinkPr && visible.find(p => p.number === deepLinkPr);
            if (deepLinked) {
              setSelectedId(deepLinked.id);
            }
          }).catch(() => setPrs([]));
        } else {
          // Unconfigured — show the demo inbox
          setPrs(SEED_PRS.map(p => ({ ...p })));
        }
      }).catch(() => setPrs(SEED_PRS.map(p => ({ ...p }))));
    }

    if (window.pywebview && window.pywebview.api) {
      load();
    } else {
      window.addEventListener('pywebviewready', load, { once: true });
    }

    // Backfill lifecycle banners from Python
    function onBackfillStart() { setBackfilling(true); }
    function onBackfillDone() {
      setBackfilling(false);
      setLastRefreshed(new Date());
      window.__pyApi.getPrs().then(livePrs => {
        const visible = (livePrs || []).filter(p => p.status !== "merged" && p.status !== "closed");
        if (visible.length) setPrs(visible);
      }).catch(() => {});
    }
    window.addEventListener('backfill-start', onBackfillStart);
    window.addEventListener('backfill-done', onBackfillDone);
    return () => {
      window.removeEventListener('backfill-start', onBackfillStart);
      window.removeEventListener('backfill-done', onBackfillDone);
    };
  }, []);

  // Listen for Python-pushed events
  useEffectBr(() => {
    function onPrDetected(e) {
      const pr = e.detail;
      if (!pr || !pr.id) return;
      if (pr.status === "merged" || pr.status === "closed") return;
      setPrs(prev => {
        if (prev.find(p => p.id === pr.id)) return prev;
        return [{ ...pr, isNew: true }, ...prev];
      });
      setTimeout(() => setPrs(prev => prev.map(p => p.id === pr.id ? { ...p, isNew: false } : p)), 1800);
    }

    function onPrUpdated(e) {
      const d = e.detail || {};
      // Full PR dict (from poller)
      if (d.id) {
        setPrs(prev => prev.map(p => p.id === d.id ? { ...p, ...d } : p));
        return;
      }
      // Minimal status update
      const { pr_id, status } = d;
      if (!pr_id || !status) return;
      setPrs(prev => prev.map(p => p.id === pr_id ? { ...p, status } : p));
    }

    function onRealSessionStarted(e) {
      const { prId } = e.detail || {};
      if (!prId) return;
      setRealSessions(prev => ({ ...prev, [prId]: true }));
    }

    window.__appEventBus.addEventListener('pr-detected', onPrDetected);
    window.__appEventBus.addEventListener('pr-updated', onPrUpdated);
    function onUpdateAvailable(e) { if (e.detail && e.detail.latest) setUpdateInfo(e.detail); }
    window.addEventListener('update-available', onUpdateAvailable);
    window.addEventListener('real-session-started', onRealSessionStarted);
    return () => {
      window.__appEventBus.removeEventListener('pr-detected', onPrDetected);
      window.__appEventBus.removeEventListener('pr-updated', onPrUpdated);
      window.removeEventListener('update-available', onUpdateAvailable);
      window.removeEventListener('real-session-started', onRealSessionStarted);
    };
  }, []);

  function pushTimer(fn, ms) { const id = setTimeout(fn, ms); timers.current.push(id); return id; }
  function clearTimers() { timers.current.forEach(clearTimeout); timers.current = []; }
  useEffectBr(() => () => clearTimers(), []);

  const setStatus = (id, status, extra = {}) =>
    setPrs(prev => prev.map(p => p.id === id ? { ...p, status, ...extra } : p));

  function updateSession(id, patch) {
    setSessions(prev => ({ ...prev, [id]: { ...prev[id], ...(typeof patch === "function" ? patch(prev[id]) : patch) } }));
  }

  // Run the animated mock flow (for SEED_PRS / demo PRs)
  function runMockFlow(pr) {
    clearTimers();
    const script = reviewScript(pr);
    const comments = PROPOSED_COMMENTS.map(c => ({ ...c }));
    updateSession(pr.id, { lines: [], running: true, comments, revealCount: 0, posted: false, decision: null });
    setStatus(pr.id, "checkout");

    pushTimer(() => {
      setStatus(pr.id, "reviewing");
      let acc = [];
      script.forEach((ln, i) => {
        const delay = 700 + i * 240 + (ln.k === "blank" ? -120 : 0);
        pushTimer(() => { acc = [...acc, ln]; updateSession(pr.id, { lines: acc }); }, delay);
      });
      const doneAt = 700 + script.length * 240 + 200;
      pushTimer(() => {
        updateSession(pr.id, { running: false });
        setStatus(pr.id, "ready");
        comments.forEach((_, i) => pushTimer(() => updateSession(pr.id, { revealCount: i + 1 }), 250 + i * 320));
      }, doneAt);
    }, 1100);
  }

  // In configured mode every PR is real; in demo mode seed PRs use the mock animation
  function isSeedPr(pr) {
    return !isConfigured && SEED_PRS.some(s => s.id === pr.id);
  }

  // Selecting a PR just shows its details — does NOT auto-start review
  function openPR(id) {
    setView("inbox");
    setMenuOpen(false);
    setSelectedId(id);
    const pr = prs.find(p => p.id === id);
    if (!pr) return;
    if (sessions[id]) return; // already has a session

    // Restore completed sessions immediately
    if (pr.status === "posted") { updateSession(id, fullSession(pr, true)); return; }
    if (pr.status === "ready")  { updateSession(id, fullSession(pr, false)); return; }

    // Demo PRs: pre-populate session state without starting the flow
    if (isSeedPr(pr)) {
      updateSession(id, { lines: [], running: false, comments: [], revealCount: 0, posted: false, decision: null });
    }
    // Real PRs: empty session — user clicks "Start Review" to begin
  }

  // Called by the Start Review button in the workspace
  function startReview(id) {
    const pr = prs.find(p => p.id === id);
    if (!pr) return;

    if (isSeedPr(pr)) {
      runMockFlow(pr);
    } else {
      updateSession(id, { lines: [], running: true, comments: [], revealCount: 0, posted: false, decision: null });
      setStatus(id, "checkout");
      setRealSessions(prev => ({ ...prev, [id]: true }));
      window.__pyApi.startReview(id).catch(err => {
        console.error("start_review failed:", err);
        updateSession(id, { running: false });
      });
    }
  }

  function toggleComment(cid) {
    if (!selectedId) return;
    updateSession(selectedId, s => ({ comments: s.comments.map(c => c.id === cid ? { ...c, checked: !c.checked } : c) }));
  }

  function postComments() {
    if (!selectedId) return;
    const sess = sessions[selectedId];
    const checkedIds = (sess ? sess.comments.filter(c => c.checked).map(c => c.id) : []);
    const dec = sess ? (sess.decision || "comment") : "comment";
    const n = checkedIds.length;

    if (realSessions[selectedId]) {
      window.__pyApi.postReview(selectedId, checkedIds, dec).catch(err => console.error("post_review failed:", err));
    }

    updateSession(selectedId, { posted: true });
    advanceSelection(selectedId);
    setStatus(selectedId, "posted", { postedCount: n });
  }

  function setDecision(d) { if (selectedId) updateSession(selectedId, { decision: d }); }

  function advanceSelection(doneId) {
    const idx = filtered.findIndex(p => p.id === doneId);
    const next = filtered[idx + 1] || filtered[idx - 1];
    setSelectedId(next ? next.id : null);
  }

  function approvePr() {
    if (!selectedId) return;
    window.__pyApi.approvePr(selectedId).catch(err => console.error("approve_pr failed:", err));
    updateSession(selectedId, { posted: true });
    advanceSelection(selectedId);
    setStatus(selectedId, "posted");
  }

  function triggerRefresh() {
    if (backfilling) return;
    if (window.pywebview && window.pywebview.api && window.pywebview.api.trigger_backfill) {
      window.pywebview.api.trigger_backfill();
    } else {
      // Fallback: just re-fetch what's in the store
      window.__pyApi.getPrs().then(livePrs => {
        const visible = (livePrs || []).filter(p => p.status !== "merged" && p.status !== "closed");
        setPrs(visible);
        setLastRefreshed(new Date());
      }).catch(() => {});
    }
  }

  function markReviewed() {
    if (!selectedId) return;
    window.__pyApi.markReviewed(selectedId).catch(err => console.error("mark_reviewed failed:", err));
    updateSession(selectedId, { posted: true });
    advanceSelection(selectedId);
    setStatus(selectedId, "posted");
  }

  function requestReview() {
    if (!selectedId) return;
    window.__pyApi.requestReview(selectedId).catch(err => console.error("request_review failed:", err));
    updateSession(selectedId, { posted: true });
    advanceSelection(selectedId);
    setStatus(selectedId, "posted");
  }

  function addressComments() {
    if (!selectedId) return;
    const pr = prs.find(p => p.id === selectedId);
    if (!pr) return;
    updateSession(selectedId, { lines: [], running: true, comments: [], revealCount: 0, posted: false, decision: null });
    setStatus(selectedId, "checkout");
    setRealSessions(prev => ({ ...prev, [selectedId]: true }));
    window.pywebview.api.start_address_comments(selectedId).catch(err => console.error("start_address_comments failed:", err));
  }

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

  const counts = React.useMemo(() => ({
    open: prs.filter(p => !["posted", "merged", "closed"].includes(p.status)).length,
    toReview: prs.filter(p => ["waiting", "checkout", "reviewing"].includes(p.status) && p.author !== githubLogin).length,
    attention: prs.filter(p => p.status === "needs_attention").length,
  }), [prs, githubLogin]);

  const filters = React.useMemo(() => makeFilters(githubLogin), [githubLogin]);

  const filtered = React.useMemo(() => {
    const f = filters.find(x => x.id === filter);
    return f && f.match ? prs.filter(p => f.match(p)) : prs;
  }, [prs, filter, filters]);

  const selected = prs.find(p => p.id === selectedId);
  const sess = selectedId ? sessions[selectedId] : null;

  return (
    <div className="desktop">
      {updateInfo && (
        <div className="updbar">
          <Icon name="arrow-counter-clockwise" />
          <span>Version {updateInfo.latest} is available.</span>
          <a href="#" onClick={e => { e.preventDefault(); if (window.pywebview && window.pywebview.api) window.pywebview.api.open_url(updateInfo.url); }}>
            See what's new
          </a>
          <button className="updbar__x" title="Dismiss" onClick={() => setUpdateInfo(null)}>✕</button>
        </div>
      )}
      {/* main window */}
      <div className="window">
        <div className="window__body">
          <Sidebar view={view} setView={setView} counts={counts} listening={listening} setListening={toggleListening} slackConnected={slackConnected} />
          {view === "inbox" ? (
            <>
              <div className="inbox">
                <div className="inbox__hd">
                  <div className="inbox__ttltop">
                    <h1 className="inbox__ttl">Inbox</h1>
                    <span className="inbox__count">{counts.open} open</span>
                  </div>
                  <div className="inbox__refresh">
                    {lastRefreshed && (
                      <span className="inbox__refreshed">
                        Updated {lastRefreshed.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })}
                      </span>
                    )}
                    <button className={`inbox__refreshbtn ${backfilling ? "spinning" : ""}`} onClick={triggerRefresh} disabled={backfilling} title="Refresh PR list">
                      <Icon name="arrow-counter-clockwise" />
                    </button>
                  </div>
                  <div className="segfilter">
                    {filters.map(f => (
                      <button key={f.id} className={filter === f.id ? "on" : ""} onClick={() => setFilter(f.id)} title={f.tooltip}>{f.label}</button>
                    ))}
                  </div>
                  {filters.find(f => f.id === filter)?.tooltip && (
                    <div className="filter__desc">{filters.find(f => f.id === filter).tooltip}</div>
                  )}
                </div>
                {backfilling && (
                  <div className="inbox__backfill">
                    <Spinner size={13} color="var(--color-primary-600)" />
                    Fetching recent PRs from Slack…
                  </div>
                )}
                <div className="inbox__list">
                  {!backfilling && filtered.length === 0 && (
                    <div className="inbox__empty">
                      {isConfigured ? "No PRs detected yet. Post a GitHub PR link in your Slack channel." : "Nothing here right now."}
                    </div>
                  )}
                  {filtered.map(pr => (
                    <PRRow key={pr.id} pr={pr} active={pr.id === selectedId} onClick={() => openPR(pr.id)}
                      density={t.rowDensity} badge={t.badgeTreatment} myLogin={githubLogin} />
                  ))}
                </div>
              </div>
              <BridgedReviewWorkspace
                pr={selected}
                sess={sess}
                lines={sess ? sess.lines : []}
                running={sess ? sess.running : false}
                comments={sess ? sess.comments : []}
                onToggle={toggleComment}
                revealCount={sess ? sess.revealCount : 0}
                layout={t.workspaceLayout}
                onPost={postComments}
                posted={sess ? sess.posted : false}
                decision={sess ? sess.decision : null}
                setDecision={setDecision}
                wsPort={wsPort}
                hasRealSession={selectedId ? !!realSessions[selectedId] : false}
                onStartReview={() => selectedId && startReview(selectedId)}
                sessionStarted={!!sess}
                githubLogin={githubLogin}
                onApprove={approvePr}
                onMarkReviewed={markReviewed}
                onAddressComments={addressComments}
                onRequestReview={requestReview}
              />
            </>
          ) : (
            <SettingsView listening={listening} setListening={toggleListening} slackConnected={slackConnected} setSlackConnected={setSlackConnected} />
          )}
        </div>
      </div>

      {/* Toast removed — new PRs trigger Mac notifications instead */}

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

// Signal to app-main.jsx that we handle the root render.
window.__BRIDGE_LOADED = true;
// Render BridgedApp as the root component.
ReactDOM.createRoot(document.getElementById("root")).render(<BridgedApp />);
