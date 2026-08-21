// app-settings.jsx — settings UI wired to real Python config
const { useState: useStateS, useEffect: useEffectS, useRef: useRefS, useCallback } = React;

function Field({ label, hint, children, wide }) {
  return (
    <div className={`field ${wide ? "wide" : ""}`}>
      <div className="field__label">{label}{hint && <span className="field__hint">{hint}</span>}</div>
      {children}
    </div>
  );
}

function Toggle({ on, onChange, label, desc }) {
  return (
    <button className="setrow" onClick={() => onChange(!on)}>
      <div className="setrow__txt">
        <div className="setrow__ttl">{label}</div>
        {desc && <div className="setrow__desc">{desc}</div>}
      </div>
      <span className={`switch ${on ? "on" : ""}`}><span className="switch__knob" /></span>
    </button>
  );
}

function ChannelPicker({ value, onChange }) {
  const [channels, setChannels] = useStateS(null);   // null = not fetched yet
  const [loading, setLoading] = useStateS(false);
  const [error, setError] = useStateS("");
  const [open, setOpen] = useStateS(false);
  const [query, setQuery] = useStateS("");

  const selected = (channels || []).find(c => c.id === value);

  function openPicker() {
    setOpen(true);
    setError("");
    if (channels === null && !loading) {
      setLoading(true);
      window.__pyApi.listSlackChannels().then(res => {
        setLoading(false);
        if (res && res.ok) setChannels(res.channels || []);
        else { setChannels([]); setError((res && res.error) || "Could not load channels"); }
      }).catch(e => { setLoading(false); setChannels([]); setError(String(e)); });
    }
  }

  const q = query.trim().toLowerCase().replace(/^#/, "");
  const matches = (channels || []).filter(c =>
    !q || c.name.toLowerCase().includes(q) || c.id.toLowerCase().includes(q)
  ).slice(0, 50);

  return (
    <div className="combo">
      <input
        className="inp mono"
        type="text"
        value={open ? query : (selected ? `#${selected.name}` : value)}
        onFocus={() => { setQuery(""); openPicker(); }}
        onBlur={() => setTimeout(() => setOpen(false), 150)}
        onChange={e => { setQuery(e.target.value); if (!open) openPicker(); }}
        placeholder={open ? "Search channels…" : "e.g. C0XXXXXXXXX"}
      />
      {open && (
        <div className="combo__list">
          {loading && <div className="combo__msg"><Spinner size={12} /> Loading channels…</div>}
          {!loading && error && <div className="combo__msg err">{error}</div>}
          {!loading && !error && matches.length === 0 && (
            <div className="combo__msg">No matches — you can paste a channel ID directly</div>
          )}
          {!loading && matches.map(c => (
            <div
              key={c.id}
              className={"combo__opt" + (c.id === value ? " sel" : "")}
              onMouseDown={() => { onChange(c.id); setOpen(false); }}
            >
              <span className="combo__name">{c.isPrivate ? "🔒" : "#"} {c.name}</span>
              <span className="combo__meta mono">{c.id}{c.isMember ? " · bot in channel" : ""}</span>
            </div>
          ))}
          {!loading && !error && q && /^C[A-Z0-9]{8,}$/i.test(query.trim()) && (
            <div className="combo__opt" onMouseDown={() => { onChange(query.trim().toUpperCase()); setOpen(false); }}>
              <span className="combo__name">Use ID “{query.trim().toUpperCase()}”</span>
            </div>
          )}
        </div>
      )}
    </div>
  );
}

function SettingsView({ listening, setListening, slackConnected, setSlackConnected }) {
  // All field values tracked as controlled state so we can read + save them
  const [fields, setFields] = useStateS({
    slackAppToken:  "",
    slackBotToken:  "",
    slackChannelId: "",
    githubToken:    "",
    codeRoot:       "",
    claudeBin:      "",
    claudeModel:    "",
    reviewCommand:  "",
    addressCommand: "",
    skillsRepo:     "",
    sessionIdleMinutes: "",
  });
  const [loaded, setLoaded] = useStateS(false);
  const [githubConnected, setGithubConnected] = useStateS(false);
  const [saving, setSaving] = useStateS(false);
  const [saveStatus, setSaveStatus] = useStateS(null);
  const [saveErr, setSaveErr] = useStateS("");
  const [connecting, setConnecting] = useStateS(false);
  const [connectErr, setConnectErr] = useStateS("");
  const [autoReview, setAutoReview] = useStateS(false);
  const [nativeNotifications, setNativeNotifications] = useStateS(true);
  const origFields = useRefS({});  // last-loaded values, to detect edited token masks

  function applyConfig(cfg) {
    if (!cfg) return;
    origFields.current = {
      slackAppToken: cfg.slackAppToken || "",
      slackBotToken: cfg.slackBotToken || "",
      githubToken:   cfg.githubToken   || "",
    };
    setFields({
      slackAppToken:  cfg.slackAppToken  || "",
      slackBotToken:  cfg.slackBotToken  || "",
      slackChannelId: cfg.slackChannelId || "",
      githubToken:    cfg.githubToken    || "",
      codeRoot:       cfg.codeRoot       || "",
      claudeBin:      cfg.claudeBin      || "",
      claudeModel:    cfg.claudeModel    || "",
      reviewCommand:  cfg.reviewCommand  || "",
      addressCommand: cfg.addressCommand || "",
      skillsRepo:     cfg.skillsRepo     || "",
      sessionIdleMinutes: cfg.sessionIdleMinutes != null ? String(cfg.sessionIdleMinutes) : "",
    });
    setAutoReview(!!cfg.autoReview);
    setNativeNotifications(cfg.nativeNotifications !== false);
    setSlackConnected(!!cfg.slackConnected);
    setGithubConnected(!!cfg.githubConnected);
    setLoaded(true);
  }

  // Load config once pywebview is ready
  useEffectS(() => {
    function load() {
      window.__pyApi.getSettings()
        .then(applyConfig)
        .catch(e => console.error("getSettings failed:", e));
    }

    if (window.pywebview && window.pywebview.api) {
      load();
    } else {
      // pywebview fires this event once the bridge is ready
      window.addEventListener('pywebviewready', load, { once: true });
    }

    // Also listen for Python pushing settings updates (after save / connect)
    function onUpdated(e) { applyConfig(e.detail); }
    window.addEventListener('settings-updated', onUpdated);
    return () => window.removeEventListener('settings-updated', onUpdated);
  }, []);

  function setField(key, val) {
    setFields(prev => ({ ...prev, [key]: val }));
  }

  function handleSave() {
    setSaving(true);
    setSaveStatus(null);
    setSaveErr("");

    // Send everything except unchanged tokens. A value containing bullets is a
    // masked (unchanged) token; empty token fields also mean unchanged. All
    // other fields are sent even when empty, so they can be deliberately cleared.
    const TOKEN_KEYS = ["slackAppToken", "slackBotToken", "githubToken"];
    const payload = {};
    const brokenTokens = [];
    for (const [k, v] of Object.entries(fields)) {
      const trimmed = (v || "").trim();
      if (trimmed.includes("•")) {
        // A partially-edited mask can't be saved — flag it instead of silently dropping.
        if (TOKEN_KEYS.includes(k) && trimmed !== (origFields.current[k] || "")) brokenTokens.push(k);
        continue;
      }
      if (TOKEN_KEYS.includes(k) && !trimmed) continue;
      payload[k] = trimmed;
    }
    if (brokenTokens.length) {
      setSaving(false);
      setSaveStatus("err");
      setSaveErr("Paste the full new token — masked values can't be partially edited (" + brokenTokens.join(", ") + ")");
      return;
    }
    // Always include booleans
    payload.autoReview = autoReview;
    payload.nativeNotifications = nativeNotifications;

    window.__pyApi.saveSettings(payload)
      .then(res => {
        setSaving(false);
        if (res && res.ok) {
          setSaveStatus("ok");
          setTimeout(() => setSaveStatus(null), 3000);
        } else {
          setSaveStatus("err");
          setSaveErr("Save failed — check the console");
        }
      })
      .catch(e => {
        setSaving(false);
        setSaveStatus("err");
        setSaveErr(String(e));
      });
  }

  function handleConnect() {
    setConnecting(true);
    setConnectErr("");

    // If the user typed new (unmasked) Slack tokens, save them first then connect.
    const newTokens = {};
    for (const k of ["slackAppToken", "slackBotToken", "slackChannelId"]) {
      const v = (fields[k] || "").trim();
      if (v && !v.includes("•")) newTokens[k] = v;
    }

    const doConnect = () => {
      window.__pyApi.connectSlack()
        .then(res => {
          setConnecting(false);
          if (res && res.ok) {
            setSlackConnected(true);
          } else {
            setConnectErr(res && res.error ? res.error : "Connection failed");
          }
        })
        .catch(e => { setConnecting(false); setConnectErr(String(e)); });
    };

    if (Object.keys(newTokens).length > 0) {
      // Save new tokens first, then connect
      window.__pyApi.saveSettings(newTokens).then(doConnect).catch(doConnect);
    } else {
      // Tokens already in .env — connect directly (Python will reload .env)
      doConnect();
    }
  }

  function handleDisconnect() {
    window.__pyApi.disconnectSlack && window.__pyApi.disconnectSlack();
    setSlackConnected(false);
    setConnectErr("");
  }

  const placeholder = loaded ? "" : "Loading…";

  return (
    <div className="settings">
      <div className="settings__inner">
        <header className="settings__head">
          <h2 className="settings__h">Settings</h2>
          <p className="settings__sub">Configure how Review listens, checks out branches, and runs your review skill.</p>
        </header>

        {/* ── Slack ── */}
        <section className="setsec">
          <div className="setsec__hd">
            <Icon name="slack-logo" />
            <div>
              <div className="setsec__ttl">Slack</div>
              <div className="setsec__sub">Socket Mode connection to your workspace</div>
            </div>
            <span className={`connpill ${slackConnected ? "on" : "off"}`}>
              <span className={`livedot ${slackConnected ? "on" : "off"}`} />
              {slackConnected ? "Connected" : "Disconnected"}
            </span>
          </div>
          <div className="setsec__body">
            <Field label="App-level token" hint="xapp-">
              <input
                className="inp mono"
                type="password"
                value={fields.slackAppToken}
                onChange={e => setField("slackAppToken", e.target.value)}
                placeholder={fields.slackAppToken ? "" : (loaded ? "Not set — enter xapp- token" : placeholder)}
              />
            </Field>
            <Field label="Bot token" hint="xoxb-">
              <input
                className="inp mono"
                type="password"
                value={fields.slackBotToken}
                onChange={e => setField("slackBotToken", e.target.value)}
                placeholder={fields.slackBotToken ? "" : (loaded ? "Not set — enter xoxb- token" : placeholder)}
              />
            </Field>
            <Field label="Channel" hint="searchable — needs a saved bot token" wide>
              <ChannelPicker
                value={fields.slackChannelId}
                onChange={id => setField("slackChannelId", id)}
              />
            </Field>
            {connectErr && (
              <div className="setsec__err"><Icon name="warning" /> {connectErr}</div>
            )}
            <div className="setsec__actions">
              {slackConnected
                ? <button className="btn secondary" onClick={handleDisconnect}><Icon name="plug" /> Disconnect</button>
                : <button className="btn primary" onClick={handleConnect} disabled={connecting}>
                    {connecting ? <><Spinner size={13} /> Connecting…</> : <><Icon name="plug-charging" /> Connect</>}
                  </button>
              }
            </div>
          </div>
        </section>

        {/* ── GitHub ── */}
        <section className="setsec">
          <div className="setsec__hd">
            <Icon name="github-logo" />
            <div>
              <div className="setsec__ttl">GitHub</div>
              <div className="setsec__sub">Personal access token for reading PRs and posting reviews</div>
            </div>
            <span className={`connpill ${githubConnected ? "on" : "off"}`}>
              <span className={`livedot ${githubConnected ? "on" : "off"}`} />
              {githubConnected ? "Configured" : "Not set"}
            </span>
          </div>
          <div className="setsec__body">
            <Field label="Personal access token" wide>
              <input
                className="inp mono"
                type="password"
                value={fields.githubToken}
                onChange={e => setField("githubToken", e.target.value)}
                placeholder={fields.githubToken ? "" : (loaded ? "Not set — enter GitHub PAT" : placeholder)}
              />
            </Field>
          </div>
        </section>

        {/* ── Repository ── */}
        <section className="setsec">
          <div className="setsec__hd">
            <Icon name="folder-open" />
            <div><div className="setsec__ttl">Repositories</div><div className="setsec__sub">Folder containing your local clones — each PR resolves to the folder matching its GitHub repo name</div></div>
          </div>
          <div className="setsec__body">
            <Field label="Code root folder" wide>
              <div className="inp-row">
                <input
                  className="inp mono"
                  type="text"
                  value={fields.codeRoot}
                  onChange={e => setField("codeRoot", e.target.value)}
                  placeholder={loaded ? "e.g. /Users/you/code" : placeholder}
                />
                <button
                  type="button"
                  className="btn-browse"
                  title="Choose folder…"
                  onClick={() => {
                    window.__pyApi.pickFolder(fields.codeRoot).then(res => {
                      if (res && res.ok && res.path) setField("codeRoot", res.path);
                    });
                  }}
                >
                  <Icon name="folder-open" /> Browse
                </button>
              </div>
            </Field>
          </div>
        </section>

        {/* ── Review ── */}
        <section className="setsec">
          <div className="setsec__hd">
            <Icon name="magnifying-glass" />
            <div><div className="setsec__ttl">Review</div><div className="setsec__sub">Claude Code commands and auto-review behaviour</div></div>
          </div>
          <div className="setsec__body">
            <Field label="Code review command" hint="runs on others' PRs">
              <input
                className="inp mono"
                type="text"
                value={fields.reviewCommand}
                onChange={e => setField("reviewCommand", e.target.value)}
                placeholder="/code-review"
              />
            </Field>
            <Field label="Address command" hint="runs on your PRs">
              <input
                className="inp mono"
                type="text"
                value={fields.addressCommand}
                onChange={e => setField("addressCommand", e.target.value)}
                placeholder="/address-comments"
              />
            </Field>
            <Field label="Claude Code path">
              <input
                className="inp mono"
                type="text"
                value={fields.claudeBin}
                onChange={e => setField("claudeBin", e.target.value)}
                placeholder="claude"
              />
            </Field>
            <Field label="Model">
              <select
                className="inp mono"
                value={fields.claudeModel}
                onChange={e => setField("claudeModel", e.target.value)}
              >
                <option value="sonnet">sonnet — fast, great for reviews</option>
                <option value="opus">opus — most capable day-to-day</option>
                <option value="haiku">haiku — fastest, lightweight</option>
                <option value="fable">fable — most intelligent, slowest</option>
                <option value="">CLI default (last used)</option>
                {/* Preserve a custom model id set via .env */}
                {!["sonnet", "opus", "haiku", "fable", ""].includes(fields.claudeModel) && (
                  <option value={fields.claudeModel}>{fields.claudeModel} (custom)</option>
                )}
              </select>
            </Field>
            <Field label="Skills repo" hint="fallback source">
              <input
                className="inp mono"
                type="text"
                value={fields.skillsRepo}
                onChange={e => setField("skillsRepo", e.target.value)}
                placeholder="spectre-websites"
              />
            </Field>
            <Field label="Idle timeout (min)" hint="0 = never">
              <input
                className="inp mono"
                type="text"
                value={fields.sessionIdleMinutes}
                onChange={e => setField("sessionIdleMinutes", e.target.value.replace(/[^0-9]/g, ""))}
                placeholder="20"
              />
            </Field>
          </div>
          <div className="setsec__rows">
            <Toggle
              on={autoReview}
              onChange={setAutoReview}
              label="Auto-review on new PR"
              desc="Start the code review immediately when a PR lands — it runs in the background so it's ready when you open it."
            />
          </div>
        </section>

        {/* ── Notifications ── */}
        <section className="setsec">
          <div className="setsec__hd">
            <Icon name="bell" />
            <div><div className="setsec__ttl">Notifications</div><div className="setsec__sub">macOS notifications for new PRs and review activity</div></div>
          </div>
          <div className="setsec__rows">
            <Toggle
              on={nativeNotifications}
              onChange={setNativeNotifications}
              label="Native notifications"
              desc="Show a macOS notification when a new PR lands, a review completes, or your PR has new comments."
            />
            <Toggle on={listening} onChange={setListening} label="Listening" desc="Watch the Slack channel for new pull requests. Toggle off to pause without disconnecting." />
          </div>
        </section>

        {/* ── Save bar ── */}
        <div className="setsec__savebar">
          <button className="btn primary" onClick={handleSave} disabled={saving}>
            {saving
              ? <><Spinner size={13} /> Saving…</>
              : saveStatus === "ok"
                ? <><Icon name="check" /> Saved</>
                : "Save changes"}
          </button>
          <span className="setsec__savehint">
            {saveStatus === "err"
              ? <span style={{color:"var(--color-red-600)"}}>{saveErr || "Save failed"}</span>
              : <>Changes are written to your <span className="mono">.env</span> file</>}
          </span>
        </div>

      </div>
    </div>
  );
}

Object.assign(window, { SettingsView });
