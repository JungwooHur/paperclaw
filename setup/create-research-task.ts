/**
 * Register the nightly research paper collection task.
 * Single task at 11:30 PM KST — fetch, classify, translate, and send summary.
 * Run with: npx tsx setup/create-research-task.ts
 */
import {
  initDatabase,
  createTask,
  deleteTask,
  getTasksForGroup,
  getRegisteredGroup,
  setRegisteredGroup,
} from '../src/db.js';
import { CronExpressionParser } from 'cron-parser';

initDatabase();

const GROUP_FOLDER = process.env.MAIN_GROUP_FOLDER || 'main';
const CHAT_JID = process.env.CHAT_JID;
const TIMEZONE = process.env.TZ || 'Asia/Seoul';

if (!CHAT_JID) {
  console.error('CHAT_JID env var is required (your WhatsApp JID, e.g. 821012345678@s.whatsapp.net)');
  process.exit(1);
}

// --- Clean up old tasks ---
const OLD_TASK_IDS = [
  'research-papers-daily',
  'research-papers-collect',
  'research-papers-summary',
  'research-papers-nightly',
];
for (const id of OLD_TASK_IDS) {
  const existing = getTasksForGroup(GROUP_FOLDER).find((t) => t.id === id);
  if (existing) {
    deleteTask(id);
    console.log(`Deleted old task: ${id}`);
  }
}

// --- Update main group timeout to 5 hours ---
const mainGroup = getRegisteredGroup(CHAT_JID);
if (mainGroup) {
  const { jid: _jid, ...group } = mainGroup;
  group.containerConfig = {
    ...group.containerConfig,
    timeout: 18_000_000, // 5 hours
  };
  setRegisteredGroup(CHAT_JID, group);
  console.log('Updated main group timeout to 5 hours');
}

// --- Single nightly task at 11:30 PM KST ---
const NIGHTLY_CRON = '30 23 * * *'; // 11:30 PM KST
const nightlyPrompt = `
You are running the nightly research paper task. Fetch new papers, classify, add to Notion with Korean translations, and send a WhatsApp summary. Complete everything in ONE session.

## Token Budget
You have a LIMITED budget. Process each paper fully in one pass (classify → Notion → translate) before moving to the next. This avoids re-reading papers.

- After ~40 tool calls: skip translations for remaining papers
- After ~60 tool calls: stop processing, finalize immediately
- ALWAYS finalize (save results + send WhatsApp) no matter what

## Setup
- Config: /workspace/group/research-papers/config.json
- Env vars: $NOTION_TOKEN, $NOTION_RESEARCH_DB
- Results: /workspace/group/research-papers/today-results.json

## Step 1: Fetch candidates
\`\`\`bash
cd /workspace/group/research-papers && python3 collect_papers.py --fetch-only 2>/dev/null
\`\`\`
Save output. Write safety-net results file:
\`\`\`bash
echo '{"date":"'$(date +%Y-%m-%d)'","total":0,"papers":[],"status":"in_progress"}' > /workspace/group/research-papers/today-results.json
\`\`\`

## Step 2: Monthly HuggingFace (1st of month only)
Check config.json \`lastHfMonth\`. If not current month:
- WebFetch \`https://huggingface.co/papers/month/\`, pick top 10
- Get metadata from Semantic Scholar, add to candidate list
- Update config.json \`lastHfMonth\`

## Step 3: No papers fallback
If 0 candidates from Steps 1-2:
\`\`\`bash
cd /workspace/group/research-papers && python3 collect_papers.py --backfill --backfill-limit 10 2>/dev/null
\`\`\`

## Step 4: Process each paper (ONE PASS per paper)
For each paper, do everything in sequence, then save before moving to the next:

**4a. Classify** from title/abstract (already in fetched metadata — no extra fetch needed):
- 분야: RL, World Model, Autonomous Navigation, VLA, Control, Computer Vision, SLAM, State Estimation, Scene Representation, Generative Models (add new as needed)
- Journal/Conference: TRO, RAL, IJRR, ICRA, IROS, CoRL, RSS, NeurIPS, ICML, ICLR, CVPR, etc. Skip for arXiv-only.
- 연구실: Check config.researcherLabMap first, then infer from affiliations

**4b. Add paper via the idempotent CLI — NEVER use raw curl POST.**

Use \`collect_papers.py --add-paper\`. It runs URL + title duplicate checks AND keeps a session-cache, so it's safe against Notion's eventually-consistent query index (which has caused 3-5x same-minute duplicates in the past when raw curl was used).

Pipe the paper JSON (same shape as \`--fetch-only\` returned) to stdin; pass classification as flags:

\`\`\`bash
echo '<paper_json>' | python3 collect_papers.py --add-paper \\
  --areas "RL,Control" \\
  --labs "ETH RSL Marco Hutter,MIT" \\
  --venue "TRO"
\`\`\`

Omit \`--venue\` for arXiv-only papers; omit \`--labs\` if none apply. Exit code + stdout tells you what happened:
- \`ADDED <page_id>\` — new page created, save the ID for step 4c
- \`SKIPPED already-in-notion\` — duplicate caught by URL or title check
- \`SKIPPED session-cache\` — already added in this session
- \`ERROR <message>\` — investigate before retrying

Raw \`curl -X POST /v1/pages\` is **forbidden** for nightly adds: bypasses dedup, has caused repeat-duplicates. If you need the page ID after ADDED, the ID is on the stdout line.

**4c. Translate + Figure (if budget allows and has arXiv ID):**
NEVER extract figures from PDF. Use HTML or screenshots.

Fetch ar5iv HTML ONCE: \`https://ar5iv.labs.arxiv.org/html/ARXIV_ID\` (or \`https://arxiv.org/html/ARXIV_ID\`)
From this single fetch, extract Abstract and core section text. Use Method/Methodology if it exists; if not (e.g., survey, position paper), use the most important section (framework, key analysis, proposed approach).

**Figure extraction:**
- Primary: Find \`<img>\` tags near the Method section, use the \`src\` URL directly (already public)
- Fallback: Use \`agent-browser\` to open the page, screenshot the figure, upload for a public URL:
  \`\`\`bash
  agent-browser open "https://ar5iv.labs.arxiv.org/html/ARXIV_ID"
  agent-browser snapshot -i
  # scroll to Method figure
  agent-browser screenshot /tmp/figure.png
  curl -F 'file=@/tmp/figure.png' https://0x0.st
  \`\`\`

Translate Abstract + Method to Korean. Append all to Notion page:
\`\`\`
PATCH https://api.notion.com/v1/blocks/PAGE_ID/children
{
  "children": [
    { "heading_2": { "rich_text": [{ "text": { "content": "초록 (Abstract)" } }] } },
    { "paragraph": { "rich_text": [{ "text": { "content": "Korean abstract..." } }] } },
    { "heading_2": { "rich_text": [{ "text": { "content": "방법론 (Method)" } }] } },
    { "image": { "type": "external", "external": { "url": "FIGURE_URL" } } },
    { "paragraph": { "rich_text": [{ "text": { "content": "Korean method..." } }] } }
  ]
}
\`\`\`
Split at 2000 chars per block. Skip image block if no figure found.

**4d. Save incrementally** — update today-results.json after each paper.

## Step 5: Finalize (MANDATORY)
**5a.** Write final today-results.json with status "completed" and relevance scores:
\`\`\`json
{
  "date": "YYYY-MM-DD",
  "total": N,
  "papers": [{ "title": "...", "paper_url": "...", "areas": [...], "labs": [...], "venue": "...", "relevance": { "quadruped": 0.9, "navigation": 0.3, "rl": 0.8, "vla": 0.2 }, "translated": true }],
  "source": "daily|fallback|hf_monthly",
  "status": "completed"
}
\`\`\`

**5b.** Send WhatsApp summary via \`mcp__paperclaw__send_message\` (*single asterisks*, • bullets). Write the summary in the language matching \`$OUTPUT_LANGUAGE\` (run \`echo \$OUTPUT_LANGUAGE\` to read it).

Korean (\`ko\`) template:
\`\`\`
📚 *Daily Research Update* (DATE)

Added N new papers:

*🔬 분야별 정리*
• *RL* (N): Title1, Title2...
• *VLA* (N): Title1...

*⭐ 오늘의 추천 논문*
• *Title* — 1줄 요약
  Link: URL

*📊 연구실별*
• ETH RSL (N papers)
• MIT (N papers)
\`\`\`
If 0 papers: "오늘은 새로운 논문이 없습니다."
If source is "hf_monthly", add: *🏆 이번 달 HuggingFace 인기 논문*

English (\`en\`) template:
\`\`\`
📚 *Daily Research Update* (DATE)

Added N new papers:

*🔬 By field*
• *RL* (N): Title1, Title2...
• *VLA* (N): Title1...

*⭐ Today's picks*
• *Title* — one-line summary
  Link: URL

*📊 By lab*
• ETH RSL (N papers)
• MIT (N papers)
\`\`\`
If 0 papers: "No new papers today."
If source is "hf_monthly", add: *🏆 This month's top HuggingFace papers*

Other languages: same structure, translate labels (📚 header, 🔬 By field, ⭐ Today's picks, 📊 By lab) and the empty-result / hf_monthly strings into \`$OUTPUT_LANGUAGE\`.
`.trim();

const nightlyInterval = CronExpressionParser.parse(NIGHTLY_CRON, {
  tz: TIMEZONE,
});
const nightlyNextRun = nightlyInterval.next().toISOString();

createTask({
  id: 'research-papers-nightly',
  group_folder: GROUP_FOLDER,
  chat_jid: CHAT_JID,
  prompt: nightlyPrompt,
  schedule_type: 'cron',
  schedule_value: NIGHTLY_CRON,
  context_mode: 'isolated',
  next_run: nightlyNextRun,
  status: 'active',
  created_at: new Date().toISOString(),
});

console.log('\nCreated research-papers-nightly task');
console.log('   Next run:', nightlyNextRun);
console.log('   Schedule: daily at 11:30 PM KST');
