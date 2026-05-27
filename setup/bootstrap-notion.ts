/**
 * Bootstraps the Notion research-papers database with the schema PaperClaw expects.
 *
 * Usage:
 *   NOTION_TOKEN=secret_... NOTION_PARENT_PAGE_ID=<page-id> \
 *     npx tsx setup/bootstrap-notion.ts
 *
 *   # Or pass parent page as an arg
 *   NOTION_TOKEN=secret_... npx tsx setup/bootstrap-notion.ts <page-id>
 *
 * Prerequisites:
 *   1. Create an internal integration at https://www.notion.so/my-integrations
 *      and copy its token into NOTION_TOKEN.
 *   2. In your Notion workspace, create (or pick) a page that will hold the
 *      database, open it, click "..." → "Connections" → add your integration.
 *      Copy the page ID from its URL (the 32-char hex at the end).
 *   3. Run this script. It prints the new database ID and appends it to .env
 *      as NOTION_RESEARCH_DB if .env exists.
 */
import { readFileSync, writeFileSync, existsSync, appendFileSync } from 'node:fs';
import { resolve } from 'node:path';

const NOTION_API = 'https://api.notion.com/v1/databases';
const NOTION_VERSION = '2022-06-28';

const token = process.env.NOTION_TOKEN;
const parentPageId = process.env.NOTION_PARENT_PAGE_ID || process.argv[2];

if (!token) {
  console.error('NOTION_TOKEN env var is required.');
  console.error('Get one at https://www.notion.so/my-integrations');
  process.exit(1);
}

if (!parentPageId) {
  console.error('Notion parent page ID is required.');
  console.error('Pass via NOTION_PARENT_PAGE_ID env var or as the first argument.');
  console.error('Find the ID in the page URL: notion.so/<workspace>/<page-name>-<THIS-PART>');
  process.exit(1);
}

const normalizedParent = parentPageId.replace(/-/g, '').match(/[a-f0-9]{32}/i)?.[0];
if (!normalizedParent) {
  console.error(`Invalid Notion page ID: ${parentPageId}`);
  console.error('Expected a 32-character hex string (dashes optional).');
  process.exit(1);
}

const dashed = [
  normalizedParent.slice(0, 8),
  normalizedParent.slice(8, 12),
  normalizedParent.slice(12, 16),
  normalizedParent.slice(16, 20),
  normalizedParent.slice(20),
].join('-');

const FIELD_TAGS = [
  'RL', 'World Model', 'Autonomous Navigation', 'VLA', 'Control',
  'Computer Vision', 'SLAM', 'State Estimation', 'Scene Representation',
  'Generative Models',
];
const VENUE_TAGS = [
  'TRO', 'RAL', 'IJRR', 'ICRA', 'IROS', 'CoRL', 'RSS', 'NeurIPS',
  'ICLR', 'ICML', 'CVPR', 'ECCV', 'Science Robotics', 'Nature',
];

// Column names — Korean when OUTPUT_LANGUAGE is ko or unset (matches legacy
// schema), English otherwise. The agent reads the actual DB schema at session
// start, so either set is supported.
const lang = (process.env.OUTPUT_LANGUAGE || 'ko').toLowerCase();
const useKoreanColumns = lang === 'ko';
const FIELD_COL = useKoreanColumns ? '분야' : 'Field';
const LAB_COL = useKoreanColumns ? '연구실, 기관 소속' : 'Lab/Institution';

const schema = {
  parent: { type: 'page_id', page_id: dashed },
  title: [{ type: 'text', text: { content: 'Research Papers' } }],
  properties: {
    'Paper Pages': { title: {} },
    'Paper URL': { url: {} },
    'Authors': { rich_text: {} },
    'Year': { number: { format: 'number' } },
    [FIELD_COL]: {
      multi_select: { options: FIELD_TAGS.map((name) => ({ name })) },
    },
    [LAB_COL]: { multi_select: { options: [] } },
    'Journal, Conference': {
      select: { options: VENUE_TAGS.map((name) => ({ name })) },
    },
  },
};

console.log(`Using column names: ${FIELD_COL} / ${LAB_COL} (OUTPUT_LANGUAGE=${lang})`);

console.log(`Creating database under page ${dashed}...`);

const res = await fetch(NOTION_API, {
  method: 'POST',
  headers: {
    Authorization: `Bearer ${token}`,
    'Notion-Version': NOTION_VERSION,
    'Content-Type': 'application/json',
  },
  body: JSON.stringify(schema),
});

if (!res.ok) {
  const body = await res.text();
  console.error(`Notion API error ${res.status}: ${body}`);
  if (res.status === 404) {
    console.error('Page not found, OR the integration is not connected to this page.');
    console.error('Open the page in Notion → "..." → Connections → add your integration.');
  }
  process.exit(1);
}

const db = (await res.json()) as { id: string; url: string };
const dbId = db.id.replace(/-/g, '');

console.log('');
console.log('✓ Database created');
console.log(`  ID:  ${dbId}`);
console.log(`  URL: ${db.url}`);
console.log('');

const envPath = resolve(process.cwd(), '.env');
if (existsSync(envPath)) {
  const current = readFileSync(envPath, 'utf8');
  if (current.includes('NOTION_RESEARCH_DB=')) {
    const updated = current.replace(/^NOTION_RESEARCH_DB=.*$/m, `NOTION_RESEARCH_DB=${dbId}`);
    writeFileSync(envPath, updated);
    console.log(`Updated NOTION_RESEARCH_DB in ${envPath}`);
  } else {
    appendFileSync(envPath, `\nNOTION_RESEARCH_DB=${dbId}\n`);
    console.log(`Appended NOTION_RESEARCH_DB to ${envPath}`);
  }
} else {
  console.log('No .env found — copy this line into your .env:');
  console.log(`  NOTION_RESEARCH_DB=${dbId}`);
}
