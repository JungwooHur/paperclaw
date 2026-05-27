---
name: notebooklm
description: Use NotebookLM to analyze research papers — create notebooks, add paper sources, ask questions for translation and Q&A. Activates for paper translation, paper analysis, and research paper tasks.
allowed-tools: Bash(notebooklm:*)
---

# NotebookLM Paper Analysis

Use `notebooklm` CLI to offload paper reading to Google NotebookLM. This saves Claude tokens — NotebookLM handles document understanding, you handle orchestration and Notion saves.

## Authentication

Auth state is mounted read-only from the host at `~/.notebooklm/`. If commands fail with auth errors, tell the user to run `notebooklm login` on the host machine.

**IMPORTANT:** The auth directory is read-only, so `notebooklm use <id>` will fail. Always pass `--notebook <id>` explicitly to every command.

## Core Commands

```bash
# Create a notebook for a paper
notebooklm create "Paper: ARXIV_ID" --json
# Returns: {"id": "notebook-uuid", "title": "Paper: ARXIV_ID", ...}

# Add paper as source (wait for processing)
notebooklm source add "https://ar5iv.labs.arxiv.org/html/ARXIV_ID" --notebook <id> --wait

# Check source status
notebooklm source list --notebook <id> --json

# Ask questions about the paper
notebooklm ask "your question" --notebook <id>

# List all notebooks (find existing ones)
notebooklm list --json
```

## Notebook ID Management

Store notebook IDs in `/workspace/group/research-papers/notebooks.json`:
```json
{
  "2401.12345": "notebook-uuid-here",
  "2403.67890": "another-uuid"
}
```

Before creating a new notebook, check if one already exists for the paper.

## Translation Prompt Template

When asking NotebookLM to translate a section, use this exact prompt structure:

```
논문의 {SECTION_NAME} 섹션 전체를 한국어로 번역해.

규칙:
1. 한 글자도 빼먹지 말고 전문(full text) 번역해
2. 전문용어(예: motion matching, policy, reward, reinforcement learning 등)는 영어 그대로 유지
3. 일반적인 단어는 문맥이 자연스럽도록 한국어로 번역
4. 수식 참조(예: 식 (1), Eq. (3))와 Figure 참조(Fig. 2)는 원문 그대로 유지
5. subsection 제목도 "영어 원문 (한국어 번역)" 형식으로 포함
6. 번역 텍스트만 출력하고, 메타 코멘트("번역 완료" 등)는 절대 금지
```

## Figure List Prompt

```
이 논문의 모든 Figure와 Table을 나열해.
각 항목에 대해: (1) Figure/Table 번호 (2) 캡션 요약 (3) 어느 섹션에서 참조되는지 알려줘.
```

## Q&A Prompt

For paper-specific questions, pass the user's question directly:
```bash
notebooklm ask "USER_QUESTION_HERE" --notebook <id>
```

For questions that go beyond the paper (background knowledge, comparisons with other papers, general concepts), answer with your own knowledge instead of using NotebookLM.

## Error Handling

- **Auth expired**: Tell user to run `notebooklm login` on host
- **Source not ready**: Wait and retry `notebooklm source list --notebook <id> --json`
- **Rate limited**: Wait 30 seconds and retry
- **NotebookLM down**: Fall back to reading ar5iv HTML directly (old approach)
