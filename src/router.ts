import { Channel, NewMessage } from './types.js';

export function escapeXml(s: string): string {
  if (!s) return '';
  return s
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;');
}

export function formatMessages(messages: NewMessage[]): string {
  const lines = messages.map(
    (m) =>
      `<message sender="${escapeXml(m.sender_name)}" time="${m.timestamp}">${escapeXml(m.content)}</message>`,
  );
  return `<messages>\n${lines.join('\n')}\n</messages>`;
}

export function stripInternalTags(text: string): string {
  return text.replace(/<internal>[\s\S]*?<\/internal>/g, '').trim();
}

// A trailing offer to file the answer in Notion. Saving a paper Q&A is step 4 of
// the workflow, not something to ask about — and `auto_save_qa.py` files any
// paper-context pair on the healer regardless, so the offer is also WRONG: it
// implies the answer was not saved when it is about to be. Written as a rule in
// CLAUDE.md first; the agent asked twice more within two days, which is this
// repo's recurring lesson that prose is not load-bearing.
//
// Deliberately narrow, because a genuine clarifying question must survive: the
// line has to END the message, be short, ask (…까요), name the action
// (추가/저장/정리), AND point at THIS answer (이 설명/내용/답변, 자세한 설명, …) or
// name Notion. "어떤 문서를 정리해드릴까요?" and "이 논문을 새로 추가해서 정리해드릴까요?"
// are real questions and are left alone.
// The object must be a WORD ("이 설명"), not a stray syllable — a bare 이 matched
// inside 없이 and swallowed "…생략없이 정리해드릴까요?", a real question. And nothing
// between the object and the verb may cross a sentence end, so "…Notion DB에
// 있나요? 아니면 이 논문을 새로 추가해서 정리해드릴까요?" keeps its question too.
const SAVE_OFFER =
  /(?:^|\n)(?:[^\n]{0,60}?[\s(])?(?:(?:이|위|해당|자세한)\s*(?:설명|내용|답변)|Notion|노션)[^\n?.!]{0,40}?(?:추가|저장|정리)(?:해\s*드릴까요|해드릴까요|할까요|드릴까요)\s*[?？]?\s*$/;

export function stripSaveOffer(text: string): string {
  const stripped = text.replace(SAVE_OFFER, '');
  return stripped.trim() ? stripped.trim() : text.trim();
}

export function formatOutbound(rawText: string): string {
  const text = stripSaveOffer(stripInternalTags(rawText));
  if (!text) return '';
  return text;
}

export function routeOutbound(
  channels: Channel[],
  jid: string,
  text: string,
): Promise<void> {
  const channel = channels.find((c) => c.ownsJid(jid) && c.isConnected());
  if (!channel) throw new Error(`No channel for JID: ${jid}`);
  return channel.sendMessage(jid, text);
}

export function findChannel(
  channels: Channel[],
  jid: string,
): Channel | undefined {
  return channels.find((c) => c.ownsJid(jid));
}
