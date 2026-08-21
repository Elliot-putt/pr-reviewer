// app-data.jsx — data stubs.
// The app is driven entirely by real data from the Python backend; these
// empty stubs keep the demo-era references in other files valid.

// Known author display names/avatars (empty — real PRs render the GitHub login)
const AUTHORS = {};

// Demo inbox seed (removed — fresh installs start with an empty inbox)
const SEED_PRS = [];

// Demo "incoming PR" toast (removed)
const INCOMING_PR = null;

// Demo terminal transcript for mock reviews (removed)
const reviewScript = () => [];

// Demo proposed review comments (removed)
const PROPOSED_COMMENTS = [];

Object.assign(window, { AUTHORS, SEED_PRS, INCOMING_PR, reviewScript, PROPOSED_COMMENTS });
