// EEVEE front end: UI state and navigation.
// Every backend call goes through api.js (sendCode, verifyCode, saveSetup). Nothing else here talks to a server.

const ENVELOPES = [
  { name: "food", label: "Food" },
  { name: "travel", label: "Travel" },
  { name: "shopping", label: "Shopping" },
  { name: "software", label: "Software" },
];

const STORAGE_KEY = "eevee-setup";
const GENERIC_ERROR = "Something went wrong. Try again.";
const SEND_FAILED = "We couldn't send a code to that number. Try again.";

// ---------- State (kept in sessionStorage so a reload doesn't lose progress) ----------

function initialState() {
  return {
    phone: null,            // E.164, set once a code has been sent
    phoneVerified: false,
    budget: null,           // whole dollars, set once step 2 is valid
    envelopes: null,        // { food: { limit: "50", auto: "0" }, ... } raw field values
    envelopesTouched: false,
    connectorLink: null,    // returned by saveSetup
    savedData: null,        // exactly what was sent to saveSetup
  };
}

const state = loadState();

function loadState() {
  try {
    const saved = JSON.parse(sessionStorage.getItem(STORAGE_KEY));
    if (saved) return { ...initialState(), ...saved };
  } catch (error) {
    // Storage blocked or unreadable: start fresh.
  }
  return initialState();
}

// Every state change goes through here, so it is always persisted.
function update(patch) {
  Object.assign(state, patch);
  try {
    sessionStorage.setItem(STORAGE_KEY, JSON.stringify(state));
  } catch (error) {
    // Storage blocked: progress just won't survive a reload.
  }
}

// What saveSetup receives, built from the current state.
function setupData() {
  return {
    phone: state.phone,
    weekly_budget: state.budget,
    envelopes: readEnvelopes(state.envelopes, state.budget).values,
  };
}

// The setup counts as saved only while the last save still matches the current data,
// so any later edit locks step 4 again without anyone having to remember to say so.
function isSaved() {
  return state.savedData !== null && state.envelopes !== null &&
    JSON.stringify(setupData()) === JSON.stringify(state.savedData);
}

// ---------- Helpers ----------

const $ = (selector, root = document) => root.querySelector(selector);
const $$ = (selector, root = document) => [...root.querySelectorAll(selector)];

function showMessage(element, text) {
  element.textContent = text;
  element.hidden = !text;
}

function markInvalid(input, invalid) {
  if (invalid) input.setAttribute("aria-invalid", "true");
  else input.removeAttribute("aria-invalid");
}

function setFieldError(input, element, text) {
  showMessage(element, text);
  markInvalid(input, Boolean(text));
}

// Runs one backend call with the whole form locked and the button in a loading state.
// Returns the call's result, or undefined if the form was already busy or the call failed
// (in which case the generic error is shown in errorElement).
async function runBusy(form, button, busyLabel, errorElement, call) {
  const lock = $(".lock", form);
  if (lock.disabled) return undefined;
  const idleLabel = button.textContent;
  lock.disabled = true;
  button.setAttribute("aria-busy", "true");
  button.textContent = busyLabel;
  try {
    return await call();
  } catch (error) {
    console.error(error);
    showMessage(errorElement, GENERIC_ERROR);
    return undefined;
  } finally {
    lock.disabled = false;
    button.removeAttribute("aria-busy");
    button.textContent = idleLabel;
  }
}

// Accepts "(415) 555-0123", "415.555.0123", "+1 415 555 0123"… Returns "+14155550123" or null.
function normalizePhone(raw) {
  let digits = raw.replace(/\D/g, "");
  if (digits.length === 11 && digits.startsWith("1")) digits = digits.slice(1);
  if (!/^[2-9]\d{9}$/.test(digits)) return null;
  return `+1${digits}`;
}

function formatPhone(e164) {
  const d = e164.slice(2);
  return `(${d.slice(0, 3)}) ${d.slice(3, 6)}-${d.slice(6)}`;
}

function formatMoney(amount) {
  const sign = amount < 0 ? "−" : "";
  return `${sign}$${Math.abs(amount).toLocaleString("en-US")}`;
}

// Raw text of a number field. Browsers report text they can't parse (like "1e") as "",
// so keep it as something that still reads as "not a number".
function rawValue(input) {
  return input.validity.badInput ? "NaN" : input.value;
}

// Reads a whole-dollar value. Empty counts as 0.
function parseDollars(raw) {
  const text = raw.trim();
  if (text === "") return { ok: true, value: 0, empty: true };
  const value = Number(text);
  if (!Number.isFinite(value)) return { ok: false, message: "enter a number." };
  if (value < 0) return { ok: false, message: "amounts can't be negative." };
  if (!Number.isInteger(value)) return { ok: false, message: "use whole dollars." };
  return { ok: true, value, empty: false };
}

// Validates the raw envelope fields against the budget. Returns the parsed values
// (what gets saved), the money left to split, and the first problem to fix, if any.
function readEnvelopes(raw, budget) {
  let total = 0;
  let problem = null;
  const values = ENVELOPES.map(({ name, label }) => {
    const limit = parseDollars(raw[name].limit);
    const auto = parseDollars(raw[name].auto);
    if (limit.ok) total += limit.value;
    if (!problem) {
      if (!limit.ok) problem = { name, field: "limit", message: `${label} limit: ${limit.message}` };
      else if (!auto.ok) problem = { name, field: "auto", message: `${label} auto-approve: ${auto.message}` };
      else if (auto.value > limit.value) {
        problem = { name, field: "auto", message: `${label}: auto-approve can't be more than its ${formatMoney(limit.value)} limit.` };
      }
    }
    return { name, weekly_limit: limit.value, auto_approve_under: auto.value };
  });
  const left = budget - total;
  if (!problem && left < 0) {
    problem = { message: `Your limits add up to ${formatMoney(total)}. That's ${formatMoney(-left)} more than your weekly budget.` };
  }
  return { values, left, problem };
}

// ---------- Approved moment: check mark, vibration ----------

function celebrate() {
  if (navigator.vibrate) navigator.vibrate(30);
}

function markSvg(kind, isStatic = false) {
  const path = kind === "approved" ? "M16 27l7 7 14-15" : "M20 20l12 12M32 20L20 32";
  return `<svg class="mark mark-${kind}${isStatic ? " is-static" : ""}" viewBox="0 0 52 52" aria-hidden="true">` +
    `<circle cx="26" cy="26" r="23" pathLength="1"/><path d="${path}" pathLength="1"/></svg>`;
}

// ---------- Landing: phone demo (tap Y or N) ----------

const quickReplies = $("#quick-replies");
const smsReply = $("#sms-reply");
const smsReplyText = $("#sms-reply-text");
const smsResult = $("#sms-result");
const phoneOverlay = $("#phone-overlay");
const phoneReplay = $("#phone-replay");
const demoTimers = [];

const DEMO_RESULTS = {
  Y: { kind: "approved", label: "Approved", html: '<strong class="approved">Approved.</strong> Claude can proceed.' },
  N: { kind: "declined", label: "Declined", html: '<strong class="declined">Declined.</strong> Claude won\'t buy it.' },
};

function later(ms, fn) {
  demoTimers.push(setTimeout(fn, ms));
}

function replyInDemo(answer) {
  const result = DEMO_RESULTS[answer];
  quickReplies.hidden = true;
  smsReplyText.textContent = answer;
  smsReply.classList.remove("is-pending");
  smsResult.innerHTML = `<span class="visually-hidden">EEVEE: </span>${result.html}`;

  later(450, () => {
    smsResult.classList.remove("is-pending");
    phoneOverlay.className = `phone-overlay is-${result.kind}`;
    phoneOverlay.innerHTML = `${markSvg(result.kind)}<p class="overlay-label">${result.label}</p>`;
    phoneOverlay.hidden = false;
    if (result.kind === "approved") celebrate();
  });
  later(2000, () => phoneOverlay.classList.add("is-leaving"));
  later(2250, () => {
    phoneOverlay.hidden = true;
    phoneReplay.classList.remove("is-pending");
  });
}

function resetDemo() {
  while (demoTimers.length) clearTimeout(demoTimers.pop());
  phoneOverlay.hidden = true;
  smsReply.classList.add("is-pending");
  smsResult.classList.add("is-pending");
  phoneReplay.classList.add("is-pending");
  quickReplies.hidden = false;
}

for (const button of $$("[data-reply]")) {
  button.addEventListener("click", () => replyInDemo(button.dataset.reply));
}

phoneReplay.addEventListener("click", () => {
  resetDemo();
  $("[data-reply]", quickReplies).focus();
});

// ---------- Navigation (hash routes, so the browser back button works) ----------

const views = $$("[data-view]");
const RENDER_STEP = [null, renderPhoneStep, renderBudgetStep, renderEnvelopesStep, renderConnectStep];

function stepFromHash() {
  const match = /^#\/setup\/([1-4])$/.exec(location.hash);
  return match ? Number(match[1]) : 0;
}

function furthestStep() {
  if (!state.phoneVerified) return 1;
  if (state.budget === null) return 2;
  return isSaved() ? 4 : 3;
}

function go(step) {
  location.hash = step ? `#/setup/${step}` : "#/";
}

function render(moveFocus) {
  const step = stepFromHash();

  if (step === 0 && location.hash && location.hash !== "#/") {
    location.replace("#/");
    return;
  }
  if (step > furthestStep()) {
    location.replace(`#/setup/${furthestStep()}`);
    return;
  }

  const name = step ? `step-${step}` : "landing";
  let active = null;
  for (const view of views) {
    view.hidden = view.dataset.view !== name;
    if (!view.hidden) active = view;
  }
  RENDER_STEP[step]?.();

  document.title = active.dataset.title;
  window.scrollTo(0, 0);
  if (moveFocus) $("h1", active).focus({ preventScroll: true });
}

// ---------- Step 1: Verify your phone ----------

const phoneForm = $("#phone-form");
const phoneInput = $("#phone");
const phoneError = $("#phone-error");
const sendButton = $("#send-button");
const codeForm = $("#code-form");
const codeInput = $("#code");
const codeError = $("#code-error");
const codeStatus = $("#code-status");
const verifyButton = $("#verify-button");
const resendButton = $("#resend-button");
const verifiedPanel = $("#verified-panel");

function renderPhoneStep() {
  const stage = state.phoneVerified ? "verified" : state.phone ? "code" : "phone";
  phoneForm.hidden = stage !== "phone";
  codeForm.hidden = stage !== "code";
  verifiedPanel.hidden = stage !== "verified";
  if (state.phone) {
    for (const element of $$("[data-phone-display]")) element.textContent = formatPhone(state.phone);
  }
}

function resetCodeForm() {
  codeInput.value = "";
  setFieldError(codeInput, codeError, "");
  showMessage(codeStatus, "");
}

phoneForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  const phone = normalizePhone(phoneInput.value);
  if (!phone) {
    setFieldError(phoneInput, phoneError, "Enter a 10-digit US mobile number.");
    phoneInput.focus();
    return;
  }
  setFieldError(phoneInput, phoneError, "");

  const sent = await runBusy(phoneForm, sendButton, "Sending…", phoneError, () => sendCode(phone));
  if (sent === undefined) return;
  if (!sent) {
    showMessage(phoneError, SEND_FAILED);
    return;
  }
  update({ phone, phoneVerified: false });
  resetCodeForm();
  renderPhoneStep();
  codeInput.focus();
});

phoneInput.addEventListener("input", () => setFieldError(phoneInput, phoneError, ""));

codeForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  const code = codeInput.value.replace(/\D/g, "");
  showMessage(codeStatus, "");
  if (code.length !== 6) {
    setFieldError(codeInput, codeError, "Enter the 6-digit code from the text.");
    codeInput.focus();
    return;
  }
  setFieldError(codeInput, codeError, "");

  const verified = await runBusy(codeForm, verifyButton, "Checking…", codeError, () => verifyCode(state.phone, code));
  if (verified === undefined) return;
  if (!verified) {
    setFieldError(codeInput, codeError, "That code doesn't match. Check the text and try again.");
    codeInput.focus();
    codeInput.select();
    return;
  }
  update({ phoneVerified: true });
  codeInput.value = "";
  go(2);
});

codeInput.addEventListener("input", () => setFieldError(codeInput, codeError, ""));

resendButton.addEventListener("click", async () => {
  setFieldError(codeInput, codeError, "");
  showMessage(codeStatus, "");
  const sent = await runBusy(codeForm, resendButton, "Sending…", codeError, () => sendCode(state.phone));
  if (sent === undefined) return;
  if (sent) showMessage(codeStatus, "New code sent.");
  else showMessage(codeError, SEND_FAILED);
});

for (const button of $$('[data-action="change-number"]')) {
  button.addEventListener("click", () => {
    if (state.phone) phoneInput.value = formatPhone(state.phone);
    update({ phone: null, phoneVerified: false });
    resetCodeForm();
    renderPhoneStep();
    phoneInput.focus();
  });
}

// ---------- Step 2: Weekly allowance ----------

const budgetForm = $("#budget-form");
const budgetInput = $("#budget");
const budgetError = $("#budget-error");

function renderBudgetStep() {
  if (state.budget !== null && budgetInput.value === "") budgetInput.value = String(state.budget);
}

budgetForm.addEventListener("submit", (event) => {
  event.preventDefault();
  const result = parseDollars(rawValue(budgetInput));
  let message = "";
  if (!result.ok) message = `Weekly budget: ${result.message}`;
  else if (result.empty) message = "Enter your weekly budget.";
  else if (result.value <= 0) message = "Your weekly budget needs to be more than $0.";
  if (message) {
    setFieldError(budgetInput, budgetError, message);
    budgetInput.focus();
    return;
  }

  if (state.budget !== result.value) {
    // Re-split only if the person hasn't adjusted the envelopes themselves.
    update({ budget: result.value, envelopes: state.envelopesTouched ? state.envelopes : null });
  }
  go(3);
});

budgetInput.addEventListener("input", () => setFieldError(budgetInput, budgetError, ""));

// ---------- Step 3: Envelopes ----------

const envelopesForm = $("#envelopes-form");
const envelopeGroup = $("#envelope-group");
const splitCounter = $("#split-counter");
const leftAmount = $("#left-amount");
const envelopesProblem = $("#envelopes-problem");
const saveError = $("#save-error");
const saveButton = $("#save-button");

envelopeGroup.innerHTML = ENVELOPES.map(({ name, label }) => `
  <fieldset class="envelope">
    <legend>${label}</legend>
    <div class="envelope-fields">
      <div class="field">
        <label for="${name}-limit">Weekly limit</label>
        <div class="money-input">
          <span aria-hidden="true">$</span>
          <input class="input" id="${name}-limit" data-envelope="${name}" data-field="limit" type="number" min="0" step="1" inputmode="numeric" placeholder="0">
        </div>
      </div>
      <div class="field">
        <label for="${name}-auto">Auto-approve under</label>
        <div class="money-input">
          <span aria-hidden="true">$</span>
          <input class="input" id="${name}-auto" data-envelope="${name}" data-field="auto" type="number" min="0" step="1" inputmode="numeric" placeholder="0">
        </div>
      </div>
    </div>
  </fieldset>`).join("");

const envelopeInput = (name, field) => $(`#${name}-${field}`);

function renderEnvelopesStep() {
  if (!state.envelopes) {
    const even = String(Math.floor(state.budget / ENVELOPES.length));
    update({ envelopes: Object.fromEntries(ENVELOPES.map(({ name }) => [name, { limit: even, auto: "0" }])) });
  }
  for (const { name } of ENVELOPES) {
    envelopeInput(name, "limit").value = state.envelopes[name].limit;
    envelopeInput(name, "auto").value = state.envelopes[name].auto;
  }
  for (const element of $$("[data-budget-display]")) element.textContent = formatMoney(state.budget);
  showMessage(saveError, "");
  updateEnvelopes();
}

function updateEnvelopes() {
  const { left, problem } = readEnvelopes(state.envelopes, state.budget);
  leftAmount.textContent = formatMoney(left);
  splitCounter.classList.toggle("is-over", left < 0);
  for (const input of $$("input", envelopeGroup)) {
    markInvalid(input, Boolean(problem) && input.dataset.envelope === problem.name && input.dataset.field === problem.field);
  }
  showMessage(envelopesProblem, problem ? problem.message : "");
  saveButton.disabled = Boolean(problem);
}

envelopeGroup.addEventListener("input", ({ target }) => {
  const { envelope, field } = target.dataset;
  if (!envelope) return;
  update({
    envelopes: { ...state.envelopes, [envelope]: { ...state.envelopes[envelope], [field]: rawValue(target) } },
    envelopesTouched: true,
  });
  showMessage(saveError, "");
  updateEnvelopes();
});

envelopesForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  if (readEnvelopes(state.envelopes, state.budget).problem) return;
  const data = setupData();
  showMessage(saveError, "");

  const link = await runBusy(envelopesForm, saveButton, "Saving…", saveError, () => saveSetup(data));
  if (link === undefined) return;
  update({ connectorLink: link, savedData: data });
  go(4);
});

// ---------- Step 4: Connect Claude ----------

const copyStatus = $("#copy-status");
let celebratedLink = state.connectorLink; // a reload on step 4 shows the mark without replaying it

function renderConnectStep() {
  const link = state.connectorLink;
  const fresh = link !== celebratedLink; // each save gets a new key, so this means "just saved"
  celebratedLink = link;
  $("#saved-mark").innerHTML = markSvg("approved", !fresh);
  if (fresh) celebrate();

  $("#personal-link").textContent = link;
  $("#cli-command").textContent = `claude mcp add --transport http eevee ${link}`;
  $("#demo-json").textContent = JSON.stringify(state.savedData, null, 2);
  checkServerReachable();
}

// A connector link is only as good as the address inside it, and MCP_BASE_URL is a tunnel
// that moves. Say so here rather than letting Claude fail with "connector not found".
async function checkServerReachable() {
  const element = $("#server-status");
  if (!element) return;

  const show = (state, message) => {
    element.dataset.state = state;
    element.textContent = message;
  };
  show("checking", "Checking that EEVEE is reachable…");

  try {
    const response = await fetch(`${MCP_BASE_URL}/health`, { cache: "no-store" });
    const body = await response.json();
    if (response.ok && body.ok) {
      show("ok", "EEVEE is running and ready for Claude.");
    } else {
      show("warn", `EEVEE answered but is not ready: ${body.database || response.status}.`);
    }
  } catch (error) {
    show(
      "error",
      "EEVEE is not reachable at that address, so this link will not work yet. " +
        "Its address changes when it restarts — ask for the current one.",
    );
  }
}

async function copyText(text) {
  try {
    await navigator.clipboard.writeText(text);
    return true;
  } catch (error) {
    // Clipboard API unavailable (older browser, some file:// setups): fall back to a hidden textarea.
    try {
      const area = document.createElement("textarea");
      area.value = text;
      area.setAttribute("readonly", "");
      area.style.position = "fixed";
      area.style.opacity = "0";
      document.body.append(area);
      area.select();
      const copied = document.execCommand("copy");
      area.remove();
      return copied;
    } catch (fallbackError) {
      return false;
    }
  }
}

for (const button of $$("[data-copy]")) {
  const idleLabel = button.textContent;
  let resetTimer = null;
  button.addEventListener("click", async () => {
    const text = document.getElementById(button.dataset.copy).textContent;
    const copied = await copyText(text);
    button.textContent = copied ? "Copied" : "Couldn't copy";
    copyStatus.textContent = copied ? "Copied to clipboard." : "Couldn't copy. Select the text and copy it yourself.";
    clearTimeout(resetTimer);
    resetTimer = setTimeout(() => { button.textContent = idleLabel; }, 2000);
  });
}

// ---------- Start ----------

window.addEventListener("hashchange", () => render(true));
render(false);
