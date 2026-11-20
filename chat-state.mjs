export function createMessage({ id, author, initials, color, role, time, text, reactions = [], replyTo = null, mine = false }) {
  if (!id || !author || !text?.trim()) throw new TypeError("A message needs an id, author, and non-empty text.");
  return { id, author, initials, color, role, time, text: text.trim(), reactions: reactions.map((reaction) => ({ ...reaction, members: [...reaction.members] })), replyTo, mine };
}

export function toggleReaction(message, emoji, member = "Morgan") {
  const reactions = message.reactions.map((reaction) => ({ ...reaction, members: [...reaction.members] }));
  const existing = reactions.find((reaction) => reaction.emoji === emoji);
  if (!existing) return [...reactions, { emoji, count: 1, members: [member] }];
  const reacted = existing.members.includes(member);
  existing.members = reacted ? existing.members.filter((name) => name !== member) : [...existing.members, member];
  existing.count = existing.members.length;
  return existing.count ? reactions : reactions.filter((reaction) => reaction.emoji !== emoji);
}

export function filterMembers(groups, query) {
  const normalized = query.trim().toLocaleLowerCase();
  if (!normalized) return groups;
  return groups.map((group) => ({ ...group, members: group.members.filter((member) => member.name.toLocaleLowerCase().includes(normalized)) })).filter((group) => group.members.length);
}
