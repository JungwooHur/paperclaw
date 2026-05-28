# Paper Q&A Auto-Healer

`auto_fix_qa.py` runs every 5 minutes via a systemd user timer. It scans every page in `$NOTION_RESEARCH_DB` and repairs Q&A callouts that are either:

- nested under a paragraph/heading instead of top-level (placement bug), OR
- in the pre-Parkour format (default color, question-in-callout rich_text) instead of the gray-background `💡 callout → toggle(question) → answer` layout.

The script is **structural prevention**, not a style rule — the agent has repeatedly ignored written instructions to use `save_qa_callout.py`, so this self-heals the state out of band.

## Install

```bash
cp groups/main/research-papers/systemd/paperclaw-qa-heal.service ~/.config/systemd/user/
cp groups/main/research-papers/systemd/paperclaw-qa-heal.timer   ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user enable --now paperclaw-qa-heal.timer
```

Verify:
```bash
systemctl --user status paperclaw-qa-heal.timer
journalctl --user -u paperclaw-qa-heal.service -n 100
```

The service reads `NOTION_TOKEN` and `NOTION_RESEARCH_DB` from `~/paperclaw/.env` (resolved via systemd's `%h` specifier). If your PaperClaw checkout lives elsewhere, edit the `WorkingDirectory`/`EnvironmentFile`/`ExecStart` paths in `paperclaw-qa-heal.service` accordingly.

## Manual run

```bash
set -a && source .env && set +a
python3 groups/main/research-papers/auto_fix_qa.py --dry-run            # scan all pages, no writes
python3 groups/main/research-papers/auto_fix_qa.py --page PAGE_ID       # heal one page
```
