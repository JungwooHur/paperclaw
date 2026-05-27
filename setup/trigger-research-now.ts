/**
 * Triggers a test research paper collection run (one-shot).
 * Run with: npx tsx setup/trigger-research-now.ts
 */
import { initDatabase, createTask, deleteTask, getTasksForGroup } from '../src/db.js';

initDatabase();

const TASK_ID = 'research-papers-test-' + Date.now();
const GROUP_FOLDER = process.env.MAIN_GROUP_FOLDER || 'main';
const CHAT_JID = process.env.CHAT_JID;

if (!CHAT_JID) {
  console.error('CHAT_JID env var is required (your WhatsApp JID, e.g. 821012345678@s.whatsapp.net)');
  process.exit(1);
}

// Clean up old test tasks
const oldTests = getTasksForGroup(GROUP_FOLDER).filter((t) =>
  t.id.startsWith('research-papers-test-'),
);
for (const t of oldTests) {
  deleteTask(t.id);
}

const testPrompt = `
You are running a TEST research paper collection. Process quickly but with full quality.

## Token Budget
Process each paper fully in one pass (classify → Notion → translate) to avoid re-reading.
- After ~30 tool calls: skip translations
- After ~50 tool calls: stop and finalize
- ALWAYS send WhatsApp summary at the end

## Setup
- Config: /workspace/group/research-papers/config.json
- Env vars: $NOTION_TOKEN, $NOTION_RESEARCH_DB

## Step 1: Fetch papers (Marco Hutter only)
\`\`\`bash
cd /workspace/group/research-papers && python3 collect_papers.py --fetch-only --researchers "Marco Hutter" 2>/dev/null
\`\`\`
Write safety-net:
\`\`\`bash
echo '{"date":"'$(date +%Y-%m-%d)'","total":0,"papers":[],"status":"in_progress"}' > /workspace/group/research-papers/today-results.json
\`\`\`

## Step 2: Process each paper (ONE PASS)
For each paper — classify, add to Notion, translate, save. All in one pass per paper.

**Classify** from fetched metadata (no extra fetch):
- 분야: RL, Control, Autonomous Navigation, etc.
- Journal/Conference: abbreviations, skip for arXiv-only
- 연구실: check config.researcherLabMap

**Check Notion for duplicates FIRST** — search by title and URL. Skip if already exists.

**Add to Notion (only if not duplicate):**
\`\`\`
POST https://api.notion.com/v1/pages
Headers: Authorization: Bearer $NOTION_TOKEN, Notion-Version: 2022-06-28
{
  "parent": { "database_id": "$NOTION_RESEARCH_DB" },
  "properties": {
    "Paper Pages": { "title": [{ "text": { "content": "TITLE" } }] },
    "Paper URL": { "url": "URL" },
    "Authors": { "rich_text": [{ "text": { "content": "AUTHORS" } }] },
    "Year": { "number": YEAR },
    "분야": { "multi_select": [{ "name": "AREA" }] },
    "연구실, 기관 소속": { "multi_select": [{ "name": "LAB" }] },
    "Journal, Conference": { "select": { "name": "VENUE" } }
  }
}
\`\`\`

**Process + Figure (max 2 papers):** NEVER extract from PDF. Fetch ar5iv HTML ONCE per paper, extract Abstract + core section (Method if exists, otherwise most important section).
For figures: find \`<img>\` URLs near Method section (primary). If that fails, use \`agent-browser\` to screenshot the figure and upload: \`agent-browser open URL\` → \`agent-browser screenshot /tmp/fig.png\` → \`curl -F 'file=@/tmp/fig.png' https://0x0.st\`.
Process section text per \`$OUTPUT_LANGUAGE\` (ko=translate to Korean, en=reformat in English, other=translate to that language — see groups/main/CLAUDE.md "Output Language Mode"). Append all to Notion page.

**Save incrementally** after each paper.

## Step 3: Finalize (MANDATORY)
Write final today-results.json. Send WhatsApp summary via \`mcp__paperclaw__send_message\` in the language matching \`$OUTPUT_LANGUAGE\`.

Korean (\`ko\`) template:
📚 *테스트 실행 결과* (DATE)
Added N papers (Marco Hutter's group):
• *Title* — 분야, Lab
  Link
⭐ Top picks:
• *Title* — 1줄 추천 이유
If 0 papers: "테스트 완료 — 새로운 논문이 없습니다."

English (\`en\`) template:
📚 *Test run result* (DATE)
Added N papers (Marco Hutter's group):
• *Title* — Field, Lab
  Link
⭐ Top picks:
• *Title* — one-line reason
If 0 papers: "Test done — no new papers."

Other languages: same structure, translate the labels.
`.trim();

createTask({
  id: TASK_ID,
  group_folder: GROUP_FOLDER,
  chat_jid: CHAT_JID,
  prompt: testPrompt,
  schedule_type: 'once',
  schedule_value: '',
  context_mode: 'isolated',
  next_run: new Date().toISOString(),
  status: 'active',
  created_at: new Date().toISOString(),
});

console.log('Test run queued:', TASK_ID);
console.log('   Check WhatsApp — results will arrive in a few minutes.');
console.log('   Monitor: tail -f logs/paperclaw.log');
