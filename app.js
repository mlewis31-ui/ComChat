const state = {
  user: null,
  communities: [],
  dms: [],
  communityId: null,
  spaceId: null,
  dmId: null,
  messages: [],
  replyTo: null,
  search: "",
  pollTimer: null,
  metadataTimer: null,
  polling: false,
  messageRequestId: 0,
  toastTimer: null,
  memberSearch: "",
  memberSearchOpen: false,
  channelsCollapsed: false
};
const byId = (id) => document.getElementById(id);
let googleScriptPromise;
const make = (tag, className = "", text) => {
  const element = document.createElement(tag);
  if (className) element.className = className;
  if (text !== undefined) element.textContent = text;
  return element;
};
const community = () => state.communities.find((item) => item.id === state.communityId);
const space = () => community()?.spaces.find((item) => item.id === state.spaceId);
const dm = () => state.dms.find((item) => item.id === state.dmId);
const activeConversationId = () => state.dmId ?? state.spaceId;

async function api(path, options = {}) {
  const response = await fetch(path, {
    credentials: "same-origin",
    ...options,
    headers: {
      ...(options.body ? { "Content-Type": "application/json" } : {}),
      ...options.headers
    }
  });
  const data = response.status === 204 ? {} : await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(data.error || `Request failed (${response.status}).`);
  return data;
}

function toast(message) {
  const element = byId("toast");
  element.textContent = message;
  element.classList.add("visible");
  clearTimeout(state.toastTimer);
  state.toastTimer = setTimeout(() => element.classList.remove("visible"), 3200);
}

function setSignedOut() {
  clearTimeout(state.pollTimer);
  clearInterval(state.metadataTimer);
  state.user = null;
  state.communities = [];
  state.dms = [];
  state.communityId = null;
  state.spaceId = null;
  state.dmId = null;
  state.messages = [];
  state.messageRequestId += 1;
  renderAll();
}

async function bootstrap() {
  const { user } = await api("/api/me");
  state.user = user;
  const [communityData, dmData] = await Promise.all([api("/api/communities"), api("/api/dms")]);
  state.communities = communityData.communities.map((item) => ({
    ...item,
    categories: [{ name: "TEXT CHANNELS", spaces: item.spaces }]
  }));
  state.dms = dmData.dms;
  if (!state.communities.some((item) => item.id === state.communityId)) {
    state.communityId = state.communities[0]?.id ?? null;
    state.spaceId = community()?.spaces.find((item) => item.kind === "text")?.id ?? community()?.spaces[0]?.id ?? null;
  }
  if (!state.dms.some((item) => item.id === state.dmId)) state.dmId = null;
  renderAll();
  await loadMessages();
  startPolling();
}

async function refreshSharedLists() {
  if (!state.user) return;
  try {
    const [communityData, dmData] = await Promise.all([api("/api/communities"), api("/api/dms")]);
    state.communities = communityData.communities.map((item) => ({
      ...item,
      categories: [{ name: "TEXT CHANNELS", spaces: item.spaces }]
    }));
    state.dms = dmData.dms;
    renderCommunities();
    renderSpaces();
    renderDMs();
    renderMembers();
  } catch (error) {
    if (error.message.includes("sign in")) setSignedOut();
    else toast(error.message);
  }
}

function renderCommunities() {
  const root = byId("community-list");
  root.replaceChildren();
  for (const item of state.communities) {
    const button = make("button", `community-icon${state.communityId === item.id && !state.dmId ? " active" : ""}`, item.name.slice(0, 2).toUpperCase());
    button.type = "button";
    button.title = item.name;
    button.setAttribute("aria-label", item.name);
    button.dataset.community = item.id;
    root.append(button);
  }
}

function renderSpaces() {
  const active = community();
  const isAdmin = Boolean(state.user?.is_admin);
  byId("community-name").textContent = active?.name ?? "Your communities";
  byId("community-tagline").textContent = active
    ? `${active.members.length} ${active.members.length === 1 ? "member" : "members"}`
    : state.user?.is_admin ? "Server administrator" : state.user ? "Join a community to get started." : "Sign in to get started.";
  byId("community-menu").hidden = !active || !isAdmin;
  byId("add-space").hidden = !active || !isAdmin;
  byId("invite-button").hidden = Boolean(active && !isAdmin);
  byId("invite-button").replaceChildren(
    make("span", "invite-icon", active && isAdmin ? "↗" : "+"),
    document.createTextNode(active ? "Invite friends" : !state.user ? "Sign in to get started" : isAdmin ? "Create a community" : "Join a community")
  );
  byId("add-community").title = isAdmin ? "Create or join a community" : "Join a community";
  byId("empty-create").textContent = isAdmin ? "Create your first community →" : "Join a community →";
  const root = byId("spaces-list");
  root.replaceChildren();
  for (const category of active?.categories ?? []) {
    if (!category.spaces.length) continue;
    const heading = make("button", "category-label");
    heading.type = "button";
    heading.setAttribute("aria-expanded", String(!state.channelsCollapsed));
    heading.append(make("span", "", category.name), make("span", "category-chevron", state.channelsCollapsed ? "›" : "⌄"));
    heading.addEventListener("click", () => {
      state.channelsCollapsed = !state.channelsCollapsed;
      renderSpaces();
    });
    root.append(heading);
    if (state.channelsCollapsed) continue;
    for (const item of category.spaces) {
      const button = make("button", `space-link${state.spaceId === item.id && !state.dmId ? " selected" : ""}`);
      button.type = "button";
      button.dataset.space = item.id;
      button.append(make("span", "space-icon", "#"));
      button.append(make("span", "space-name", item.name));
      root.append(button);
    }
  }
  if (!active && state.user) root.append(make("p", "sidebar-empty", "Create a community or join with an invite code."));
}

function renderDMs() {
  const root = byId("dm-list");
  root.replaceChildren();
  for (const item of state.dms) {
    const button = make("button", `space-link dm-link${state.dmId === item.id ? " selected" : ""}`);
    button.type = "button";
    button.dataset.dm = item.id;
    button.append(make("span", "dm-avatar", item.display_name.slice(0, 1).toUpperCase()), make("span", "space-name", item.display_name));
    root.append(button);
  }
  if (!state.dms.length) root.append(make("p", "sidebar-empty dm-empty", state.user ? "No direct messages yet." : "Sign in to message friends."));
}

function renderMembers() {
  const root = byId("member-groups");
  root.replaceChildren();
  const memberPanel = byId("members-panel");
  const isDM = Boolean(state.dmId);
  memberPanel.hidden = isDM;
  const allMembers = community()?.members ?? [];
  const search = state.memberSearch.trim().toLocaleLowerCase();
  const members = allMembers.filter((member) => !search
    || member.display_name.toLocaleLowerCase().includes(search)
    || member.username.toLocaleLowerCase().includes(search));
  byId("member-search-toggle").hidden = allMembers.length < 2;
  byId("member-search-field").hidden = !allMembers.length || allMembers.length < 2 || !state.memberSearchOpen;
  byId("member-filter").value = state.memberSearch;
  byId("empty-members").hidden = members.length > 0;
  byId("empty-members").textContent = state.user
    ? search ? "No members match that search." : "No members to show."
    : "Sign in to see your communities.";
  if (!members.length) return;
  root.append(make("h2", "member-group-title", `MEMBERS · ${members.length}`));
  for (const member of members) {
    const row = make("div", "member-row");
    row.dataset.member = `${member.display_name} ${member.username}`.toLocaleLowerCase();
    const avatar = make("span", "avatar member-avatar avatar-lavender", member.display_name.slice(0, 1).toUpperCase());
    const info = make("div", "member-info");
    info.append(make("div", "member-name-line", member.display_name), make("span", "member-status", `@${member.username}`));
    row.append(avatar, info);
    root.append(row);
  }
}

function renderProfile() {
  const name = state.user?.display_name ?? "Guest";
  byId("profile-name").replaceChildren(
    make("strong", "", name),
    make("span", "", state.user ? `@${state.user.username}${state.user.is_admin ? " · Server admin" : " · Account"}` : "Sign in or create account")
  );
  byId("profile-button").textContent = state.user ? name.slice(0, 1).toUpperCase() : "?";
  byId("profile-button").setAttribute("aria-label", state.user ? "Account settings" : "Sign in or create account");
  byId("local-status").textContent = state.user ? "●" : "";
  byId("local-status").title = state.user ? "Connected to this ComChat server" : "";
}

function renderMessage(message) {
  const article = make("article", `message${message.mine ? " own-message" : ""}`);
  article.dataset.messageId = String(message.id);
  const initial = message.author.slice(0, 1).toUpperCase();
  article.append(make("div", "avatar message-avatar avatar-lavender", initial));
  const content = make("div", "message-content");
  const heading = make("div", "message-heading");
  heading.append(make("strong", "", message.author), make("time", "", new Date(message.created_at * 1000).toLocaleTimeString([], { hour: "numeric", minute: "2-digit" })));
  content.append(heading);
  if (message.reply_to) {
    const reply = state.messages.find((item) => item.id === message.reply_to);
    content.append(make("div", "quoted-reply", `↳ In reply to ${reply?.author ?? "a previous message"}`));
  }
  content.append(make("p", "message-text", message.text));
  if (message.reactions?.length || message.mine) {
    const reactions = make("div", "reactions");
    for (const reaction of message.reactions ?? []) {
      const button = make("button", `reaction${reaction.reacted ? " reacted" : ""}`);
      button.type = "button";
      button.dataset.action = "react";
      button.dataset.emoji = reaction.emoji;
      button.dataset.messageId = String(message.id);
      button.append(make("span", "", reaction.emoji), make("span", "", String(reaction.count)));
      reactions.append(button);
    }
    if (message.mine) {
      const add = make("button", "reaction-add", "+");
      add.type = "button";
      add.dataset.action = "quick-react";
      add.dataset.messageId = String(message.id);
      reactions.append(add);
    }
    content.append(reactions);
  }
  const actions = make("div", "message-actions");
  for (const [action, label, title] of [["reply", "↩", "Reply"], ["quick-react", "☺", "React"], ["report", "···", "Report message"]]) {
    const button = make("button", "", label);
    button.type = "button";
    button.title = title;
    button.dataset.action = action;
    button.dataset.messageId = String(message.id);
    actions.append(button);
  }
  article.append(content, actions);
  return article;
}

function renderMessages() {
  const root = byId("messages");
  root.replaceChildren();
  const activeSpace = space();
  const activeDM = state.dms.find((item) => item.id === state.dmId);
  const selected = activeSpace || activeDM;
  const search = state.search.trim().toLocaleLowerCase();
  const visible = state.messages.filter((message) => !search
    || message.text.toLocaleLowerCase().includes(search)
    || message.author.toLocaleLowerCase().includes(search));
  byId("message-date").hidden = visible.length === 0;
  if (visible.length) byId("message-date").replaceChildren(make("span", "", "MESSAGES"));
  for (const message of visible) root.append(renderMessage(message));
  if (!visible.length && selected) {
    root.append(make("p", "empty-search", search
      ? "No messages match that search."
      : activeDM
        ? `This is the beginning of your conversation with ${activeDM.display_name}.`
        : "No messages yet. Start the conversation."));
  }
}

function renderAll() {
  renderCommunities();
  renderSpaces();
  renderDMs();
  renderProfile();
  renderMembers();
  updateConversationUI();
}

function updateConversationUI() {
  const activeSpace = space();
  const activeDM = dm();
  const selected = activeSpace || activeDM;
  byId("space-title").textContent = activeDM?.display_name ?? activeSpace?.name ?? "No space selected";
  byId("space-description").textContent = activeDM ? `Direct message · @${activeDM.username}` : activeSpace ? `#${community()?.name} · Text channel` : "Create a community or start a DM.";
  byId("conversation-icon").textContent = activeDM ? "@" : "#";
  byId("empty-state").hidden = Boolean(selected);
  byId("welcome-title").textContent = selected ? (activeDM ? `Message ${activeDM.display_name}` : `# ${activeSpace.name}`) : "Start with a blank slate";
  byId("welcome-description").textContent = selected
    ? "Your conversation is ready."
    : !state.user ? "No demo communities, chats, or members are loaded. Sign in to get started."
      : state.user.is_admin ? "No communities yet. Create one for your friends."
        : "You haven’t joined a community yet. Join with an invite code.";
  byId("empty-create").hidden = Boolean(selected);
  byId("empty-dm").hidden = Boolean(selected);
  const canChat = Boolean(state.user && selected);
  byId("message-form").hidden = !canChat;
  byId("composer-hint").hidden = !canChat;
  byId("message-search").disabled = !canChat;
  byId("space-info").disabled = !selected;
  byId("message-input").disabled = !canChat;
  byId("message-input").placeholder = activeDM ? `Message @${activeDM.username}` : activeSpace ? `Message #${activeSpace.name}` : "Sign in and select a conversation";
  renderMessages();
}

async function loadMessages(after = 0, silent = false) {
  const conversationId = activeConversationId();
  if (!state.user || !conversationId) {
    state.messages = [];
    renderMessages();
    return;
  }
  const requestId = ++state.messageRequestId;
  try {
    const result = await api(`/api/conversations/${conversationId}/messages?after=${after}`);
    if (conversationId !== activeConversationId() || requestId !== state.messageRequestId) return;
    const scroll = byId("conversation-scroll");
    const wasNearBottom = scroll.scrollHeight - scroll.scrollTop - scroll.clientHeight < 80;
    const existing = new Set(state.messages.map((item) => item.id));
    const added = result.messages.filter((item) => !existing.has(item.id));
    if (!after) state.messages = result.messages;
    else state.messages.push(...added);
    renderMessages();
    if (added.length && wasNearBottom) scroll.scrollTop = scroll.scrollHeight;
  } catch (error) {
    if (error.message.includes("sign in")) setSignedOut();
    else if (!silent) toast(error.message);
  }
}

async function pollMessages() {
  if (!state.user || state.polling) return;
  state.polling = true;
  try {
    if (document.visibilityState === "visible" && activeConversationId()) {
      await loadMessages(state.messages.at(-1)?.id ?? 0, true);
    }
  } finally {
    state.polling = false;
    clearTimeout(state.pollTimer);
    state.pollTimer = setTimeout(pollMessages, document.visibilityState === "visible" ? 1500 : 5000);
  }
}

function startPolling() {
  clearTimeout(state.pollTimer);
  clearInterval(state.metadataTimer);
  pollMessages();
  state.metadataTimer = setInterval(() => {
    if (state.user) refreshSharedLists();
  }, 5000);
}

function openDialog(title, body, buttons) {
  const dialog = byId("action-dialog");
  const content = byId("dialog-content");
  content.replaceChildren(make("div", "dialog-kicker", "COMCHAT"), make("h2", "", title), make("p", "dialog-copy", body));
  const actions = make("div", "dialog-actions");
  for (const item of buttons) {
    const button = make("button", `dialog-action${item.secondary ? " secondary" : ""}`, item.label);
    button.type = "button";
    button.addEventListener("click", async () => {
      dialog.close();
      await new Promise((resolve) => setTimeout(resolve, 0));
      await item.action?.();
    });
    actions.append(button);
  }
  content.append(actions);
  dialog.showModal();
}

function openFormDialog({ title, body, fields, submitLabel = "Continue", onSubmit }) {
  const dialog = byId("action-dialog");
  const content = byId("dialog-content");
  content.replaceChildren(make("div", "dialog-kicker", "COMCHAT · ACCOUNT REQUIRED"), make("h2", "", title), make("p", "dialog-copy", body));
  const form = make("form", "create-form");
  const inputs = {};
  for (const field of fields) {
    const label = make("label", "create-label", field.label);
    const input = make("input", "create-input");
    input.name = field.name;
    input.type = field.type ?? "text";
    input.required = field.required !== false;
    input.maxLength = field.maxLength ?? 64;
    input.minLength = field.minLength ?? 1;
    input.autocomplete = field.autocomplete ?? "off";
    input.placeholder = field.placeholder ?? "";
    label.append(input);
    form.append(label);
    inputs[field.name] = input;
  }
  const error = make("p", "form-error");
  error.hidden = true;
  const actions = make("div", "dialog-actions");
  const submit = make("button", "dialog-action", submitLabel);
  submit.type = "submit";
  const cancel = make("button", "dialog-action secondary", "Cancel");
  cancel.type = "button";
  cancel.addEventListener("click", () => dialog.close());
  actions.append(submit, cancel);
  form.append(error, actions);
  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    error.hidden = true;
    submit.disabled = true;
    try {
      await onSubmit(Object.fromEntries(Object.entries(inputs).map(([name, input]) => [name, input.value])));
      dialog.close();
    } catch (failure) {
      error.textContent = failure.message;
      error.hidden = false;
    } finally {
      submit.disabled = false;
    }
  });
  content.append(form);
  dialog.showModal();
  inputs[fields[0].name].focus();
}

function openAuth(mode = "register") {
  const register = mode === "register";
  openFormDialog({
    title: register ? "Create your account" : "Welcome back",
    body: register
      ? "The first account on a fresh server becomes its sole administrator. Other accounts can join communities but cannot create or edit them. Use a unique password."
      : "Accounts and messages are stored on the ComChat server. Use a unique password and do not share it with anyone.",
    fields: [
      { name: "username", label: "Username (3–24 letters, numbers, underscores)", maxLength: 24, minLength: 3, autocomplete: "username" },
      ...(register ? [{ name: "display_name", label: "Display name", maxLength: 32, autocomplete: "nickname" }] : []),
      { name: "password", label: "Password (12–128 characters)", type: "password", maxLength: 128, minLength: 12, autocomplete: register ? "new-password" : "current-password" }
    ],
    submitLabel: register ? "Create account" : "Sign in",
    onSubmit: async (values) => {
      const result = await api(register ? "/api/register" : "/api/login", { method: "POST", body: JSON.stringify(values) });
      state.user = result.user;
      state.communityId = null;
      state.spaceId = null;
      state.dmId = null;
      state.messages = [];
      await bootstrap();
      toast(register ? "Account created." : "Signed in.");
    }
  });
  addGoogleSignIn();
}

function addGoogleSignIn() {
  const content = byId("dialog-content");
  const separator = make("div", "auth-separator", "or");
  const button = make("div", "google-signin");
  content.append(separator, button);
  const clientId = document.querySelector('meta[name="google-client-id"]')?.content;
  if (!clientId) {
    button.append(make("p", "auth-provider-note", "Google sign-in needs a Google OAuth client ID configured by the server owner."));
    return;
  }
  button.append(make("p", "auth-provider-note", "Continue with your Google account."));
  const mountButton = async () => {
    if (!googleScriptPromise) {
      googleScriptPromise = new Promise((resolve, reject) => {
        const script = document.createElement("script");
        script.src = "https://accounts.google.com/gsi/client";
        script.async = true;
        script.onload = resolve;
        script.onerror = () => reject(new Error("Google sign-in could not load."));
        document.head.append(script);
      });
    }
    await googleScriptPromise;
    const identity = window.google?.accounts?.id;
    if (!identity) throw new Error("Google sign-in is unavailable.");
    identity.initialize({
      client_id: clientId,
      callback: async ({ credential }) => {
        try {
          const result = await api("/api/google", { method: "POST", body: JSON.stringify({ credential }) });
          state.user = result.user;
          state.communityId = null;
          state.spaceId = null;
          state.dmId = null;
          state.messages = [];
          byId("action-dialog").close();
          await bootstrap();
          toast("Signed in with Google.");
        } catch (error) {
          button.replaceChildren(make("p", "auth-provider-note auth-provider-error", error.message));
        }
      }
    });
    const target = make("div", "google-button-target");
    button.append(target);
    identity.renderButton(target, { theme: "filled_black", size: "large", shape: "pill", text: "continue_with", width: 280 });
  };
  mountButton().catch((error) => {
    button.replaceChildren(make("p", "auth-provider-note auth-provider-error", error.message));
  });
}

function openAccount() {
  if (!state.user) {
    openFormDialog({
      title: "Sign in or create an account",
      body: "The first account on a fresh server becomes its sole administrator. Other accounts can join communities but cannot create or edit them.",
      fields: [
        { name: "username", label: "Username", maxLength: 24, minLength: 3, autocomplete: "username" },
        { name: "password", label: "Password", type: "password", maxLength: 128, minLength: 12, autocomplete: "current-password" }
      ],
      submitLabel: "Sign in",
      onSubmit: async (values) => {
        const result = await api("/api/login", { method: "POST", body: JSON.stringify(values) });
        state.user = result.user;
        state.communityId = null;
        state.spaceId = null;
        state.dmId = null;
        await bootstrap();
        toast("Signed in.");
      }
    });
    addGoogleSignIn();
    const link = make("button", "dialog-link", "New here? Create an account");
    link.type = "button";
    link.addEventListener("click", () => {
      byId("action-dialog").close();
      openAuth("register");
    });
    byId("dialog-content").append(link);
    return;
  }
  openDialog(`Signed in as ${state.user.display_name}`, `Username: @${state.user.username}`, [
    { label: "Change display name", action: async () => {
      openFormDialog({
        title: "Change display name",
        body: "This name is visible to people in your communities and DMs.",
        fields: [{ name: "display_name", label: "Display name", maxLength: 32, autocomplete: "nickname" }],
        submitLabel: "Save name",
        onSubmit: async ({ display_name }) => {
          const result = await api("/api/profile", { method: "PATCH", body: JSON.stringify({ display_name }) });
          state.user = result.user;
          renderProfile();
        }
      });
    } },
    { label: "Sign out", secondary: true, action: async () => {
      await api("/api/logout", { method: "POST", body: "{}" });
      setSignedOut();
      toast("Signed out.");
    } }
  ]);
}

function openCommunityActions() {
  if (!state.user) return openAccount();
  const actions = [];
  if (state.user.is_admin) actions.push({ label: "Create community", action: () => openFormDialog({
      title: "Create a community",
      body: "Your friends can join from another device using the invite code.",
      fields: [{ name: "name", label: "Community name", maxLength: 48, placeholder: "e.g. Weekend crew" }],
      submitLabel: "Create",
      onSubmit: async ({ name }) => {
        const result = await api("/api/communities", { method: "POST", body: JSON.stringify({ name }) });
        await bootstrap();
        state.communityId = result.community.id;
        state.spaceId = result.community.spaces[0].id;
        state.dmId = null;
        renderAll();
        await loadMessages();
      }
    }) });
  actions.push({ label: "Join with invite code", secondary: true, action: () => openFormDialog({
      title: "Join a community",
      body: "Ask a trusted community member for the invite code.",
      fields: [{ name: "invite_code", label: "Invite code", maxLength: 64 }],
      submitLabel: "Join community",
      onSubmit: async ({ invite_code }) => {
        const result = await api("/api/communities/join", { method: "POST", body: JSON.stringify({ invite_code }) });
        await bootstrap();
        state.communityId = result.community.id;
        state.spaceId = community()?.spaces.find((item) => item.kind === "text")?.id ?? null;
        state.dmId = null;
        renderAll();
        await loadMessages();
      }
    }) });
  openDialog("Communities", state.user.is_admin
    ? "Create a community or join one with an invite code."
    : "Only the server administrator can create communities. You can join with an invite code.",
  actions);
}

function openCreateSpace() {
  if (!community()) return;
  openFormDialog({
    title: "Create a text channel",
    body: "Create a shared text conversation for this community.",
    fields: [{ name: "name", label: "Channel name", maxLength: 48, placeholder: "game-chat" }],
    submitLabel: "Create channel",
    onSubmit: async ({ name }) => {
      const result = await api(`/api/communities/${community().id}/spaces`, {
        method: "POST",
        body: JSON.stringify({ name, kind: "text" })
      });
      await bootstrap();
      state.communityId = result.space.community_id ?? state.communityId;
      state.spaceId = result.space.id;
      state.dmId = null;
      renderAll();
      await loadMessages();
    }
  });
}

function openDM() {
  if (!state.user) return openAccount();
  openFormDialog({
    title: "Start a direct message",
    body: "Enter your friend’s username. They must already have an account on this server.",
    fields: [{ name: "username", label: "Friend’s username", maxLength: 24, minLength: 3, autocomplete: "off" }],
    submitLabel: "Open DM",
    onSubmit: async ({ username }) => {
      const result = await api("/api/dms", { method: "POST", body: JSON.stringify({ username }) });
      await bootstrap();
      state.communityId = null;
      state.spaceId = null;
      state.dmId = result.dm.id;
      renderAll();
      await loadMessages();
    }
  });
}

function bindEvents() {
  document.addEventListener("visibilitychange", () => {
    if (document.visibilityState !== "visible" || !state.user) return;
    clearTimeout(state.pollTimer);
    refreshSharedLists();
    pollMessages();
  });
  window.addEventListener("focus", () => {
    if (!state.user) return;
    clearTimeout(state.pollTimer);
    pollMessages();
  });
  byId("community-list").addEventListener("click", async (event) => {
    const button = event.target.closest("[data-community]");
    if (!button) return;
    state.communityId = button.dataset.community;
    state.spaceId = community()?.spaces.find((item) => item.kind === "text")?.id ?? community()?.spaces[0]?.id ?? null;
    state.dmId = null;
    state.messages = [];
    renderAll();
    await loadMessages();
  });
  byId("spaces-list").addEventListener("click", async (event) => {
    const button = event.target.closest("[data-space]");
    if (!button) return;
    state.spaceId = button.dataset.space;
    state.dmId = null;
    state.messages = [];
    state.search = "";
    byId("message-search").value = "";
    renderAll();
    await loadMessages();
  });
  byId("dm-list").addEventListener("click", async (event) => {
    const button = event.target.closest("[data-dm]");
    if (!button) return;
    state.communityId = null;
    state.spaceId = null;
    state.dmId = button.dataset.dm;
    state.messages = [];
    state.search = "";
    byId("message-search").value = "";
    renderAll();
    await loadMessages();
  });
  byId("messages").addEventListener("click", async (event) => {
    const button = event.target.closest("[data-action]");
    if (!button) return;
    const message = state.messages.find((item) => String(item.id) === button.dataset.messageId);
    if (!message) return;
    if (button.dataset.action === "reply") {
      state.replyTo = message.id;
      byId("reply-label").textContent = `Replying to ${message.author}`;
      byId("reply-context").hidden = false;
      byId("message-input").focus();
    } else if (button.dataset.action === "react" || button.dataset.action === "quick-react") {
      const emoji = button.dataset.emoji ?? "💛";
      try {
        await api(`/api/messages/${message.id}/reactions`, { method: "POST", body: JSON.stringify({ emoji }) });
        await loadMessages();
      } catch (error) { toast(error.message); }
    } else if (button.dataset.action === "report") {
      openDialog("Report message", "Reports are not connected to a moderation service on this server yet.", [{ label: "Close", secondary: true }]);
    }
  });
  byId("message-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    const input = byId("message-input");
    const text = input.value.trim();
    const conversationId = activeConversationId();
    if (!text || !conversationId) return;
    try {
      await api(`/api/conversations/${conversationId}/messages`, {
        method: "POST",
        body: JSON.stringify({ text, reply_to: state.replyTo })
      });
      input.value = "";
      input.style.height = "auto";
      state.replyTo = null;
      byId("reply-context").hidden = true;
      await loadMessages();
      byId("conversation-scroll").scrollTop = byId("conversation-scroll").scrollHeight;
    } catch (error) { toast(error.message); }
  });
  byId("message-input").addEventListener("keydown", (event) => {
    if (event.key === "Enter" && !event.shiftKey) {
      event.preventDefault();
      byId("message-form").requestSubmit();
    }
  });
  byId("message-input").addEventListener("input", (event) => {
    event.target.style.height = "auto";
    event.target.style.height = `${Math.min(event.target.scrollHeight, 140)}px`;
  });
  byId("cancel-reply").addEventListener("click", () => {
    state.replyTo = null;
    byId("reply-context").hidden = true;
  });
  byId("message-search").addEventListener("input", (event) => {
    state.search = event.target.value;
    renderMessages();
  });
  byId("member-search-toggle").addEventListener("click", () => {
    state.memberSearchOpen = !state.memberSearchOpen;
    if (!state.memberSearchOpen) state.memberSearch = "";
    renderMembers();
    if (state.memberSearchOpen) byId("member-filter").focus();
  });
  byId("member-filter").addEventListener("input", (event) => {
    state.memberSearch = event.target.value;
    renderMembers();
  });
  byId("emoji-button").addEventListener("click", () => {
    byId("emoji-picker").hidden = !byId("emoji-picker").hidden;
  });
  byId("emoji-picker").addEventListener("click", (event) => {
    const button = event.target.closest("[data-emoji]");
    if (!button) return;
    const input = byId("message-input");
    const start = input.selectionStart;
    const end = input.selectionEnd;
    input.setRangeText(button.dataset.emoji, start, end, "end");
    input.focus();
    input.dispatchEvent(new Event("input", { bubbles: true }));
    byId("emoji-picker").hidden = true;
  });
  byId("add-community").addEventListener("click", openCommunityActions);
  byId("empty-create").addEventListener("click", openCommunityActions);
  byId("invite-button").addEventListener("click", async () => {
    const active = community();
    if (!active) return openCommunityActions();
    if (!state.user?.is_admin) return toast("Only the server administrator can manage invites.");
    try {
      const { invite_code } = await api(`/api/communities/${active.id}/invite`);
      openDialog("Invite friends", `Share this code with friends you trust: ${invite_code}`, [
        { label: "Copy invite code", action: async () => {
          try { await navigator.clipboard.writeText(invite_code); toast("Invite code copied."); }
          catch { toast(`Invite code: ${invite_code}`); }
        } },
        { label: "Close", secondary: true }
      ]);
    } catch (error) { toast(error.message); }
  });
  byId("add-space").addEventListener("click", () => openCreateSpace());
  byId("add-dm").addEventListener("click", openDM);
  byId("empty-dm").addEventListener("click", openDM);
  byId("profile-button").addEventListener("click", openAccount);
  byId("profile-name").addEventListener("click", openAccount);
  byId("safety-info").addEventListener("click", () => openDialog("Privacy & safety", "This server stores account and message data so friends can use it across devices. The app does not encrypt messages end-to-end. Use a trusted HTTPS-hosted server, choose a unique password, and do not share sensitive information.", [{ label: "Got it", secondary: true }]));
  byId("community-menu").addEventListener("click", () => {
    const active = community();
    if (active && state.user?.is_admin) openDialog(active.name, "Manage this community on the ComChat server.", [
      { label: "Edit community name", action: () => openFormDialog({
        title: "Edit community",
        body: "Change the name shown to all community members.",
        fields: [{ name: "name", label: "Community name", maxLength: 48 }],
        submitLabel: "Save changes",
        onSubmit: async ({ name }) => {
          await api(`/api/communities/${active.id}`, { method: "PATCH", body: JSON.stringify({ name }) });
          await refreshSharedLists();
          toast("Community updated.");
        }
      }) },
      { label: "Close", secondary: true }
    ]);
  });
  byId("space-info").addEventListener("click", () => {
    if (space()) openDialog(`#${space().name}`, "Messages in this channel are shared with community members.", [{ label: "Close", secondary: true }]);
    else if (dm()) openDialog(dm().display_name, "Direct messages are visible only to the two accounts in this conversation.", [{ label: "Close", secondary: true }]);
  });
  byId("mobile-menu").addEventListener("click", () => document.querySelector(".app-shell").classList.toggle("menu-open"));
  byId("action-dialog").addEventListener("click", (event) => {
    if (event.target === byId("action-dialog")) byId("action-dialog").close();
  });
}

async function start() {
  bindEvents();
  renderAll();
  try {
    await bootstrap();
  } catch (error) {
    setSignedOut();
    if (error.message !== "Please sign in to continue.") toast("Sign in to connect to this ComChat server.");
  }
}

start();
