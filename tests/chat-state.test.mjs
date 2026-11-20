import test from "node:test";
import assert from "node:assert/strict";
import { createMessage, filterMembers, toggleReaction } from "../chat-state.mjs";

test("creates messages with trimmed text and independent reaction data", () => {
  const message = createMessage({ id: "m1", author: "Ari", text: "  hello  ", reactions: [{ emoji: "🌿", count: 1, members: ["Ari"] }] });
  assert.equal(message.text, "hello");
  assert.deepEqual(toggleReaction(message, "🌿"), []);
  assert.equal(message.reactions[0].count, 1);
});

test("rejects messages without usable content", () => {
  assert.throws(() => createMessage({ id: "m1", author: "Ari", text: " " }), TypeError);
});

test("adds and removes a member's reaction without changing other members", () => {
  const message = createMessage({ id: "m1", author: "Ari", text: "hello", reactions: [{ emoji: "🌿", count: 1, members: ["Ari"] }] });
  const reacted = toggleReaction(message, "🌿");
  assert.deepEqual(reacted, [{ emoji: "🌿", count: 2, members: ["Ari", "Morgan"] }]);
  assert.deepEqual(toggleReaction({ ...message, reactions: reacted }, "🌿"), [{ emoji: "🌿", count: 1, members: ["Ari"] }]);
  assert.deepEqual(toggleReaction(message, "✨"), [{ emoji: "🌿", count: 1, members: ["Ari"] }, { emoji: "✨", count: 1, members: ["Morgan"] }]);
});

test("filters member groups by name and removes empty groups", () => {
  const groups = [{ role: "Hosts", members: [{ name: "Juniper" }] }, { role: "Around", members: [{ name: "Ari" }, { name: "Morgan" }] }];
  assert.deepEqual(filterMembers(groups, "  mor "), [{ role: "Around", members: [{ name: "Morgan" }] }]);
  assert.equal(filterMembers(groups, "").length, 2);
});
