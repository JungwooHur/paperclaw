# Paper Q&A Auto-Healer

`auto_fix_qa.py` runs every 5 minutes via a systemd user timer, **on the host** from `paperclaw-qa-heal.service`, NOT in the container. It scans every page in `$NOTION_RESEARCH_DB` and repairs Q&A callouts that are either:

- nested under a paragraph/heading instead of top-level (placement bug), OR
- in the legacy format (default color, question-in-callout rich_text) instead of the gray-background `💡 callout → toggle(question) → answer` layout.

The script is **structural prevention**, not a style rule — the agent has repeatedly ignored written instructions to use `save_qa_callout.py`, so this self-heals the state out of band.

The service also runs `auto_save_qa.py`, `collect_papers.py --dedupe`, and
**`heal_paper_pages.py`** (back-matter / source-URL / math / furniture / figure /
table healing) — see the `ExecStart` lines in the unit. `heal_figures`/`heal_tables`
are HTML-based (arxiv `arxiv.org/html/<id>`): a **PDF-only paper (HTML 404)** can't be
auto-healed for figures/tables.

## Install

**SYMLINK the units — do NOT `cp`.** A copied unit goes stale the moment the repo
unit gains a step: `cp` was used originally, then `heal_paper_pages.py` was added
to the repo unit but the installed copy was never refreshed, so the
healer's ExecStart never ran and *none* of the structural fixes reached new papers
(the journal showed only the old 3 steps). A symlink can't drift — a `git pull`
updates the target and a `daemon-reload` picks it up.

```bash
cd ~/paperclaw
ln -sf "$PWD/groups/main/research-papers/systemd/paperclaw-qa-heal.service" ~/.config/systemd/user/
ln -sf "$PWD/groups/main/research-papers/systemd/paperclaw-qa-heal.timer"   ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user enable --now paperclaw-qa-heal.timer
```

The installed units live at `~/.config/systemd/user/paperclaw-qa-heal.{service,timer}`;
the source of truth is this directory, and `/update` re-links them and runs
`daemon-reload`. After ANY change to the unit files (including a `git pull` that
touches them), run `systemctl --user daemon-reload`.

If healers "aren't applying", first check the timer is firing
(`docs/DEBUG_CHECKLIST.md`, "Healer or Watchdog Not Running"), then that the
installed unit actually contains every `ExecStart` from the repo unit: a step the
installed copy lacks silently never runs, and its journal
(`journalctl --user -u paperclaw-qa-heal.service`) has no `healed N/M` output.
Drift check:

```bash
[ ~/.config/systemd/user/paperclaw-qa-heal.service -ef "$PWD/groups/main/research-papers/systemd/paperclaw-qa-heal.service" ] \
  && echo "in sync" || echo "STALE — re-link + daemon-reload"
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
