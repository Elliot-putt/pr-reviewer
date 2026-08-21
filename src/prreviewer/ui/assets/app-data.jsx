// app-data.jsx — mock data for the PR auto-review app
// Repo context: Spectre marketing / website front-end work.

const AUTHORS = {
  maya:   { name: "Maya Okonkwo",  initials: "MO", tint: "#5359EA" },
  theo:   { name: "Theo Lindqvist", initials: "TL", tint: "#16a34a" },
  priya:  { name: "Priya Raman",   initials: "PR", tint: "#db2777" },
  sam:    { name: "Sam Whitfield", initials: "SW", tint: "#ca8a04" },
  ines:   { name: "Inês Carvalho", initials: "IC", tint: "#2563eb" },
};

// status keys: waiting · checkout · reviewing · ready · posted · changes
const SEED_PRS = [
  {
    id: "pr-318",
    title: "Lazy-load testimonial carousel below the fold",
    repo: "spectre-website",
    number: 318,
    branch: "perf/lazy-testimonials",
    author: "maya",
    landed: "2 min ago",
    status: "waiting",
    additions: 64, deletions: 12, files: 4,
  },
  {
    id: "pr-315",
    title: "Refactor primary nav to use design tokens",
    repo: "spectre-website",
    number: 315,
    branch: "refactor/nav-tokens",
    author: "theo",
    landed: "18 min ago",
    status: "ready",
    additions: 132, deletions: 88, files: 7,
  },
  {
    id: "pr-314",
    title: "Fix hero CTA contrast on small screens",
    repo: "spectre-website",
    number: 314,
    branch: "fix/hero-cta-contrast",
    author: "priya",
    landed: "41 min ago",
    status: "reviewing",
    additions: 18, deletions: 6, files: 2,
  },
  {
    id: "pr-311",
    title: "Add pricing page annual / monthly toggle",
    repo: "spectre-marketing",
    number: 311,
    branch: "feat/pricing-toggle",
    author: "ines",
    landed: "1 hr ago",
    status: "posted",
    additions: 210, deletions: 24, files: 9,
    postedCount: 3,
  },
  {
    id: "pr-309",
    title: "Upgrade Nuxt to 3.12 and drop legacy polyfills",
    repo: "spectre-website",
    number: 309,
    branch: "chore/nuxt-3-12",
    author: "sam",
    landed: "2 hr ago",
    status: "posted",
    additions: 540, deletions: 612, files: 31,
    postedCount: 5,
  },
  {
    id: "pr-307",
    title: "Fix broken og:image on blog detail pages",
    repo: "spectre-marketing",
    number: 307,
    branch: "fix/blog-og-image",
    author: "theo",
    landed: "4 hr ago",
    status: "posted",
    additions: 22, deletions: 9, files: 3,
    postedCount: 2,
  },
];

// A new PR that the "simulate" action drops into the channel.
const INCOMING_PR = {
  id: "pr-321",
  title: "Animate the value-prop cards on scroll into view",
  repo: "spectre-website",
  number: 321,
  branch: "feat/value-prop-reveal",
  author: "ines",
  landed: "just now",
  status: "waiting",
  additions: 96, deletions: 8, files: 5,
};

// Terminal lines for a Claude Code /code-review run. Kept short ("lightly suggested").
// Each line: { d: delay ms before showing, t: text, k: kind }
// kinds: prompt · sys · run · ok · note · ai · diff-add · diff-del · spin
function reviewScript(pr) {
  return [
    { k: "prompt", t: `~/code/${pr.repo} $ git fetch && git checkout ${pr.branch}` },
    { k: "sys", t: `Switched to branch '${pr.branch}'` },
    { k: "sys", t: `Your branch is up to date with 'origin/${pr.branch}'.` },
    { k: "blank" },
    { k: "prompt", t: `~/code/${pr.repo} $ claude /code-review --pr ${pr.number}` },
    { k: "ai", t: `● Running code-review skill on PR #${pr.number}` },
    { k: "run", t: `  Diff: ${pr.files} files · +${pr.additions} −${pr.deletions}` },
    { k: "run", t: `  Reading project conventions from CLAUDE.md …` },
    { k: "run", t: `  Loading design tokens from colors_and_type.css …` },
    { k: "blank" },
    { k: "ai", t: `● Analysing components/ValuePropCard.vue` },
    { k: "note", t: `  ↳ inline #5359EA found — should reference a token` },
    { k: "ai", t: `● Analysing composables/useReveal.ts` },
    { k: "note", t: `  ↳ IntersectionObserver not disconnected on unmount` },
    { k: "ai", t: `● Analysing pages/index.vue` },
    { k: "blank" },
    { k: "ok", t: `✓ Review complete — 4 comments proposed, 0 blocking` },
    { k: "cursor" },
  ];
}

// Proposed review comments for the active run.
const PROPOSED_COMMENTS = [
  {
    id: "c1",
    file: "components/ValuePropCard.vue",
    line: 47,
    severity: "issue",
    checked: true,
    title: "Hard-coded brand colour",
    body: "border-color: #5359EA is hard-coded here. Use var(--color-primary-600) so the card tracks the design system if the indigo ever shifts.",
  },
  {
    id: "c2",
    file: "composables/useReveal.ts",
    line: 22,
    severity: "issue",
    checked: true,
    title: "Observer never disconnected",
    body: "The IntersectionObserver is created in onMounted but never torn down. Add observer.disconnect() in onUnmounted to avoid a leak on route changes.",
  },
  {
    id: "c3",
    file: "components/ValuePropCard.vue",
    line: 12,
    severity: "nit",
    checked: false,
    title: "Prefer transform over top",
    body: "Animating top triggers layout on every frame. Animate transform: translateY() instead and the reveal will stay on the compositor.",
  },
  {
    id: "c4",
    file: "pages/index.vue",
    line: 134,
    severity: "suggestion",
    checked: true,
    title: "Respect reduced-motion",
    body: "Consider gating the scroll reveal behind @media (prefers-reduced-motion: no-preference) so the cards appear instantly for motion-sensitive visitors.",
  },
];

Object.assign(window, { AUTHORS, SEED_PRS, INCOMING_PR, reviewScript, PROPOSED_COMMENTS });
