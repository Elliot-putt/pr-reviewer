// icons.jsx — inline SVG icon set (icon fonts can't render in this environment:
// their glyphs live in the Unicode Private-Use Area, which gets stripped from
// CSS `content`). Each icon is authored at 24×24. Stroke icons by default;
// `fill: true` for solid brand marks. Sized via font-size (width/height = 1em),
// coloured via currentColor — so existing `.ph` CSS rules keep working.

const ICONS = {
  "warning": { p: '<path d="M12 3.5L2.5 20.5h19L12 3.5z"/><line x1="12" y1="10" x2="12" y2="15"/><circle cx="12" cy="18" r="0.8" fill="currentColor" stroke="none"/>' },
  "magnifying-glass": { p: '<circle cx="10.5" cy="10.5" r="6.5"/><line x1="20" y1="20" x2="15.5" y2="15.5"/>' },
  "git-branch": { p: '<circle cx="6.5" cy="5.5" r="2.5"/><circle cx="6.5" cy="18.5" r="2.5"/><circle cx="17.5" cy="7.5" r="2.5"/><path d="M6.5 8v8"/><path d="M17.5 10c0 4-4 4.5-7 5"/>' },
  "check": { p: '<path d="M5 12.5l4.5 4.5L19 7"/>' },
  "check-circle": { p: '<circle cx="12" cy="12" r="8.5"/><path d="M8.3 12.3l2.6 2.6 5-5.2"/>' },
  "clock-countdown": { p: '<circle cx="12" cy="12" r="8.5"/><path d="M12 7.3V12l3.2 2"/>' },
  "paper-plane-tilt": { p: '<path d="M20.5 3.5L3 10.7l6.6 2.9 2.9 6.6 7.5-16.7z"/><path d="M9.6 13.6l4-4"/>' },
  "arrow-counter-clockwise": { p: '<path d="M4.6 12a7.4 7.4 0 1 0 2.3-5.4L4 9.2"/><path d="M3.6 4.5v4.8h4.8"/>' },
  "envelope": { p: '<rect x="3" y="5.5" width="18" height="13" rx="2.2"/><path d="M4 7.5l8 5.5 8-5.5"/>' },
  "gear-six": { p: '<circle cx="12" cy="12" r="3.3"/><path d="M12 2.8v3M12 18.2v3M21.2 12h-3M5.8 12h-3M18.5 5.5l-2.1 2.1M7.6 16.4l-2.1 2.1M18.5 18.5l-2.1-2.1M7.6 7.6L5.5 5.5"/>' },
  "hash": { p: '<path d="M9.2 3.5L7 20.5M17 3.5l-2.2 17M4.5 8.8h15.5M3.8 15.2h15.5"/>' },
  "listening": { p: '<circle cx="12" cy="12" r="1.8" fill="currentColor" stroke="none"/><path d="M8.6 8.6a4.8 4.8 0 0 0 0 6.8M15.4 8.6a4.8 4.8 0 0 1 0 6.8M6.2 6.2a8.2 8.2 0 0 0 0 11.6M17.8 6.2a8.2 8.2 0 0 1 0 11.6"/>' },
  "paused": { p: '<circle cx="12" cy="12" r="1.8" fill="currentColor" stroke="none"/><path d="M8.6 8.6a4.8 4.8 0 0 0 0 6.8M15.4 8.6a4.8 4.8 0 0 1 0 6.8"/><line x1="4" y1="4" x2="20" y2="20"/>' },
  "file-code": { p: '<path d="M13.5 3H7a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V8.5z"/><path d="M13.5 3v5.5H19"/><path d="M10.4 12.6L8.7 14.3l1.7 1.7M13.6 12.6l1.7 1.7-1.7 1.7"/>' },
  "github-logo": { fill: true, p: '<path d="M12 .5C5.7.5.5 5.7.5 12c0 5.1 3.3 9.4 7.9 10.9.6.1.8-.25.8-.56 0-.27-.01-1-.015-1.96-3.2.7-3.88-1.54-3.88-1.54-.52-1.33-1.28-1.68-1.28-1.68-1.05-.72.08-.7.08-.7 1.16.08 1.77 1.19 1.77 1.19 1.03 1.77 2.7 1.26 3.36.96.1-.75.4-1.26.73-1.55-2.55-.29-5.24-1.28-5.24-5.69 0-1.26.45-2.28 1.19-3.09-.12-.29-.52-1.46.11-3.05 0 0 .97-.31 3.18 1.18a11 11 0 0 1 5.79 0c2.2-1.49 3.17-1.18 3.17-1.18.63 1.59.23 2.76.11 3.05.74.81 1.19 1.83 1.19 3.09 0 4.42-2.69 5.4-5.25 5.68.41.36.78 1.06.78 2.14 0 1.55-.014 2.79-.014 3.17 0 .31.21.67.81.56A11.5 11.5 0 0 0 23.5 12C23.5 5.7 18.3.5 12 .5z"/>' },
  "arrow-up-right": { p: '<path d="M7 17L17 7M8.5 7H17v8.5"/>' },
  "arrow-square-out": { p: '<path d="M13.5 4H20v6.5"/><path d="M20 4l-8.5 8.5"/><path d="M18 13.5V18a2 2 0 0 1-2 2H6a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h4.5"/>' },
  "flask": { p: '<path d="M9 3.2v6.3l-4.7 8.7A1.6 1.6 0 0 0 5.7 20.6h12.6a1.6 1.6 0 0 0 1.4-2.4L15 9.5V3.2"/><path d="M8 3.2h8"/><path d="M7.3 14.5h9.4"/>' },
  "folder-open": { p: '<path d="M3.5 7.5a2 2 0 0 1 2-2h3.4l2 2H18a2 2 0 0 1 2 2v1.5H3.5z"/><path d="M3.5 11h17.2l-2 6.8a2 2 0 0 1-1.9 1.4H5.6a2 2 0 0 1-2-1.6z"/>' },
  "bell": { p: '<path d="M6.2 9.5a5.8 5.8 0 0 1 11.6 0c0 4.6 1.9 5.8 1.9 5.8H4.3s1.9-1.2 1.9-5.8z"/><path d="M10 18.8a2 2 0 0 0 4 0"/>' },
  "sliders-horizontal": { p: '<path d="M4 7.5h9M19 7.5h1M4 16.5h5M15 16.5h5"/><circle cx="16" cy="7.5" r="2.2"/><circle cx="12" cy="16.5" r="2.2"/>' },
  "caret-down": { p: '<path d="M6 9.5l6 6 6-6"/>' },
  "plug": { p: '<path d="M9 3v4.5M15 3v4.5"/><path d="M7 7.5h10v2.5a5 5 0 0 1-10 0z"/><path d="M12 15v6"/>' },
  "plug-charging": { p: '<path d="M9 3v4M15 3v4"/><path d="M7.5 7h9v2a4.5 4.5 0 0 1-9 0z"/><path d="M12 15.5l-1.6 2.5h3l-1.6 2.5"/>' },
  "broadcast": { p: '<circle cx="12" cy="12" r="1.8" fill="currentColor" stroke="none"/><path d="M8.6 8.6a4.8 4.8 0 0 0 0 6.8M15.4 8.6a4.8 4.8 0 0 1 0 6.8M6.2 6.2a8.2 8.2 0 0 0 0 11.6M17.8 6.2a8.2 8.2 0 0 1 0 11.6"/>' },
  "list": { p: '<path d="M8.5 6h11.5M8.5 12h11.5M8.5 18h11.5"/><circle cx="4.5" cy="6" r="1.1" fill="currentColor" stroke="none"/><circle cx="4.5" cy="12" r="1.1" fill="currentColor" stroke="none"/><circle cx="4.5" cy="18" r="1.1" fill="currentColor" stroke="none"/>' },
  "slack-logo": { fill: true, p: '<path d="M5.04 15.17a2.53 2.53 0 0 1-2.52 2.52A2.53 2.53 0 0 1 0 15.17a2.53 2.53 0 0 1 2.52-2.52h2.52v2.52zM6.31 15.17a2.53 2.53 0 0 1 2.52-2.52 2.53 2.53 0 0 1 2.52 2.52v6.31A2.53 2.53 0 0 1 8.83 24a2.53 2.53 0 0 1-2.52-2.52v-6.31zM8.83 5.04a2.53 2.53 0 0 1-2.52-2.52A2.53 2.53 0 0 1 8.83 0a2.53 2.53 0 0 1 2.52 2.52v2.52H8.83zM8.83 6.31a2.53 2.53 0 0 1 2.52 2.52 2.53 2.53 0 0 1-2.52 2.52H2.52A2.53 2.53 0 0 1 0 8.83a2.53 2.53 0 0 1 2.52-2.52h6.31zM18.96 8.83a2.53 2.53 0 0 1 2.52-2.52A2.53 2.53 0 0 1 24 8.83a2.53 2.53 0 0 1-2.52 2.52h-2.52V8.83zM17.69 8.83a2.53 2.53 0 0 1-2.52 2.52 2.53 2.53 0 0 1-2.52-2.52V2.52A2.53 2.53 0 0 1 15.17 0a2.53 2.53 0 0 1 2.52 2.52v6.31zM15.17 18.96a2.53 2.53 0 0 1 2.52 2.52A2.53 2.53 0 0 1 15.17 24a2.53 2.53 0 0 1-2.52-2.52v-2.52h2.52zM15.17 17.69a2.53 2.53 0 0 1-2.52-2.52 2.53 2.53 0 0 1 2.52-2.52h6.31A2.53 2.53 0 0 1 24 15.17a2.53 2.53 0 0 1-2.52 2.52h-6.31z"/>' },
};

function Icon({ name, className = "", style }) {
  const ic = ICONS[name];
  if (!ic) return null;
  return (
    <svg className={"ic ph " + className} viewBox="0 0 24 24" width="1em" height="1em"
      fill={ic.fill ? "currentColor" : "none"} stroke={ic.fill ? "none" : "currentColor"}
      strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"
      style={style} dangerouslySetInnerHTML={{ __html: ic.p }} />
  );
}

Object.assign(window, { ICONS, Icon });
