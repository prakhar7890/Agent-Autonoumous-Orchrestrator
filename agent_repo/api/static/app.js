/* Agent Repository UI — vanilla JavaScript.
 *
 * Security rules honoured here:
 *  - Only textContent and createElement build page content.
 *  - No browser storage of queries or outputs; queries are never logged.
 *  - Same-origin requests only (all fetch URLs are relative).
 *  - Client-side upload size check mirrors the server's 1 MB limit.
 */

"use strict";

const SERVER_LIMIT_BYTES = 1024 * 1024; // matches server upload limit in agent_repo/api/app.py

const TAB_NAMES = ["ask", "agents", "versions", "executions", "proposals"];

const bannerTarget = document.getElementById("banner-target");
const askStatus = document.getElementById("ask-status");
const askForm = document.getElementById("ask-form");
const askQuery = document.getElementById("ask-query");
const askAttachment = document.getElementById("ask-attachment");
const askUploadLimit = document.getElementById("ask-upload-limit");
const askSubmit = document.getElementById("ask-submit");
const askBreakpoint = document.getElementById("ask-breakpoint");
const askOutput = document.getElementById("ask-output");
const askExecId = document.getElementById("ask-exec-id");

const agentsTableEl = document.getElementById("agents-table");
const agentDetailEl = document.getElementById("agent-detail");
const versionsAgentSelect = document.getElementById("versions-agent");
const versionsOutputEl = document.getElementById("versions-output");
const executionsTableEl = document.getElementById("executions-table");
const executionDetailEl = document.getElementById("execution-detail");
const proposalsTableEl = document.getElementById("proposals-table");

function escapeText(text) {
  return String(text == null ? "" : text);
}

function setText(element, text) {
  if (element) element.textContent = escapeText(text);
}

function createElement(tag, attributes) {
  const element = document.createElement(tag);
  if (attributes) {
    Object.keys(attributes).forEach(function (key) {
      if (key.indexOf("on") === 0) {
        throw new Error("inline event handlers are not allowed: " + key);
      }
      element.setAttribute(key, String(attributes[key]));
    });
  }
  return element;
}

function append(parent, child) {
  if (parent && child) parent.appendChild(child);
  return child;
}

function clearChildren(element) {
  if (!element) return;
  while (element.firstChild) element.removeChild(element.firstChild);
}

function show(element) {
  if (element) element.removeAttribute("hidden");
}

function hide(element) {
  if (element) element.setAttribute("hidden", "");
}

function switchTab(tabName) {
  TAB_NAMES.forEach(function (name) {
    const button = document.querySelector('.tab[data-tab="' + name + '"]');
    const panel = document.getElementById("tab-" + name);
    if (name === tabName) {
      if (button) button.setAttribute("aria-selected", "true");
      if (panel) panel.setAttribute("aria-hidden", "false");
    } else {
      if (button) button.setAttribute("aria-selected", "false");
      if (panel) panel.setAttribute("aria-hidden", "true");
    }
  });
}

function setupTabs() {
  document.querySelectorAll(".tab").forEach(function (button) {
    button.addEventListener("click", function () {
      const tab = button.getAttribute("data-tab");
      switchTab(tab);
      if (tab === "versions" && !versionsOutputEl.querySelector("table")) loadVersions();
      else if (tab === "executions") loadExecutions();
      else if (tab === "proposals") loadProposals();
    });
  });
}

async function fetchJson(path) {
  const response = await fetch(path, { credentials: "same-origin" });
  if (!response.ok) {
    throw new Error("HTTP " + response.status + " for " + path);
  }
  return response.json();
}

function formatNotRecorded(value) {
  return value === null || value === undefined ? "not recorded" : String(value);
}

function renderPlanSteps(steps, container) {
  clearChildren(container);
  if (!steps || steps.length === 0) return;
  steps.forEach(function (step) {
    const block = createElement("div", { class: "step" });
    const title = createElement("div", { class: "step-title" });
    setText(title, "Step " + step.order + " — " + escapeText(step.role) + " · " + escapeText(step.agent) + "@" + escapeText(step.version));
    append(block, title);

    const field = function (label, value) {
      const row = createElement("div", { class: "step-field" });
      const lab = createElement("span", { class: "step-label" });
      setText(lab, label + ":");
      const val = createElement("span", { class: "step-value" });
      setText(val, formatNotRecorded(value));
      append(row, lab);
      append(row, document.createTextNode(" "));
      append(row, val);
      append(block, row);
    };

    field("What it does", step.what_it_does);
    field("Why chosen", step.why_chosen);
    field("Matched terms", step.matched_terms ? step.matched_terms.join(", ") : "not recorded");
    field("Score", step.score);
    field("Tools", step.tools);
    field("Model", step.model);
    append(container, block);
  });
}

function renderOutput(result, container) {
  const output = result.output || {};
  const kind = output.kind || "text";
  const text = output.text || "";

  // Hide the output box entirely when there is no output (e.g. broken runs such as no_match).
  if (!text && kind !== "image") {
    hide(container);
    return;
  }
  show(container);

  if (kind === "text") {
    const pre = createElement("pre");
    setText(pre, text);
    append(container, pre);
  } else if (kind === "json") {
    const pre = createElement("pre");
    try {
      setText(pre, JSON.stringify(JSON.parse(output.text || "{}"), null, 2));
    } catch (_err) {
      setText(pre, output.text || "");
    }
    append(container, pre);
  } else if (kind === "image") {
    const mime = output.mime || "";
    const allowed = ["image/png", "image/jpeg", "image/webp", "image/svg+xml"];
    if (allowed.indexOf(mime) !== -1 && output.data_base64) {
      const img = createElement("img", { src: "data:" + mime + ";base64," + output.data_base64, alt: "image output" });
      append(container, img);
      const label = createElement("p", { class: "stub-label" });
      setText(label, "Generated image output");
      append(container, label);
    } else {
      const p = createElement("p");
      setText(p, "Image output (mime " + escapeText(mime) + ") is not displayed in this page.");
      append(container, p);
    }
  } else {
    const p = createElement("p");
    setText(p, "No output to display.");
    append(container, p);
  }
}

function renderAskResult(result) {
  setText(askStatus, "Status: " + escapeText(result.status));
  if (result.break_reason) {
    clearChildren(askBreakpoint);
    const callout = createElement("div", { class: "callout" });
    const detailText = result.user_message ? " - " + escapeText(result.user_message) : "";
    setText(callout, "Breakpoint: " + escapeText(result.break_reason) + detailText);
    append(askBreakpoint, callout);
  } else {
    clearChildren(askBreakpoint);
  }
  if (result.clarifying_question) {
    const p = createElement("p");
    setText(p, "Clarifying question: " + escapeText(result.clarifying_question));
    append(askBreakpoint, p);
  }
  renderPlanSteps(result.steps || [], askOutput);
  renderOutput(result, askOutput);
  // Show the sanitized original filename for uploads as a display-only field,
  // after renderOutput so it stays visible even when the output box is hidden.
  if (result.upload_filename) {
    const p = createElement("p", { class: "stub-label" });
    setText(p, "Upload: " + escapeText(result.upload_filename));
    append(askOutput, p);
    show(askOutput);
  }
  setText(askExecId, "Execution id: " + escapeText(result.execution_id));
}

function resetAskUi() {
  setText(askStatus, "");
  clearChildren(askBreakpoint);
  clearChildren(askOutput);
  setText(askExecId, "");
  askSubmit.disabled = false;
}

async function handleAskSubmit(event) {
  event.preventDefault();
  resetAskUi();

  const file = askAttachment.files && askAttachment.files[0];
  if (file && file.size > SERVER_LIMIT_BYTES) {
    setText(askUploadLimit, "File too large: " + file.size + " bytes. Limit is " + SERVER_LIMIT_BYTES + " bytes (1 MB).");
    askSubmit.disabled = false;
    return;
  }
  setText(askUploadLimit, "");

  askSubmit.disabled = true;
  setText(askStatus, "Sending query…");

  const formData = new FormData();
  formData.append("query", askQuery.value);
  if (file) formData.append("attachment", file);

  try {
    const response = await fetch("/ask", { method: "POST", body: formData, credentials: "same-origin" });
    if (!response.ok) {
      let detail = "Request failed (HTTP " + response.status + ").";
      try {
        const body = await response.json();
        if (body && body.detail) {
          if (Array.isArray(body.detail)) {
            detail = body.detail.map(function (d) { return (d && d.message) ? d.message : String(d); }).join("; ");
          } else {
            detail = String(body.detail);
          }
        }
      } catch (_err) {
        // keep generic message
      }
      setText(askStatus, detail);
      askSubmit.disabled = false;
      return;
    }
    const result = await response.json();
    renderAskResult(result);
    askAttachment.value = "";
    loadExecutions();
    askSubmit.disabled = false;
  } catch (err) {
    setText(askStatus, "Request failed: " + escapeText(err.message));
  } finally {
    askSubmit.disabled = false;
  }
}

function renderAgentsTable(agents) {
  clearChildren(agentsTableEl);
  clearChildren(agentDetailEl);
  const table = createElement("table");
  const thead = createElement("thead");
  const headerRow = createElement("tr");
  ["Name", "Version", "Status", "Input -> Output", "Risk tools", "Sub-agents", "Example queries"].forEach(function (heading) {
    const th = createElement("th");
    setText(th, heading);
    append(headerRow, th);
  });
  append(thead, headerRow);
  append(table, thead);

  const tbody = createElement("tbody");
  agents.forEach(function (agent) {
    const tr = createElement("tr", { class: "clickable" });
    tr.tabIndex = 0;
    const cells = [agent.name, agent.version, agent.status, agent.input_to_output, agent.risk_tools, agent.sub_agents, agent.example_queries];
    cells.forEach(function (text) {
      const td = createElement("td");
      setText(td, text);
      append(tr, td);
    });
    tr.addEventListener("click", function () {
      loadAgentDetail(agent.name);
    });
    tr.addEventListener("keydown", function (ev) {
      if (ev.key === "Enter" || ev.key === " ") {
        ev.preventDefault();
        loadAgentDetail(agent.name);
      }
    });
    append(tbody, tr);
  });
  append(table, tbody);
  append(agentsTableEl, table);
}

async function loadAgents() {
  try {
    const agents = await fetchJson("/agents");
    renderAgentsTable(agents);
    // populate versions agent selector
    const previousSelection = versionsAgentSelect.value;
    clearChildren(versionsAgentSelect);
    agents.forEach(function (agent) {
      const option = createElement("option", { value: agent.name });
      setText(option, agent.name);
      append(versionsAgentSelect, option);
    });
    if (previousSelection) {
      versionsAgentSelect.value = previousSelection;
    }
    if (versionsAgentSelect.value) {
      loadVersions();
    }
  } catch (err) {
    setText(agentsTableEl, "Failed to load agents: " + escapeText(err.message));
  }
}

async function loadAgentDetail(name) {
  clearChildren(agentDetailEl);
  setText(agentDetailEl, "Loading " + escapeText(name) + "…");
  try {
    const detail = await fetchJson("/agents/" + encodeURIComponent(name));
    const dl = createElement("dl");
    const field = function (label, value) {
      const dt = createElement("dt");
      setText(dt, label);
      const dd = createElement("dd");
      if (Array.isArray(value)) {
        const ul = createElement("ul");
        value.forEach(function (item) {
          const li = createElement("li");
          setText(li, escapeText(item));
          append(ul, li);
        });
        append(dd, ul);
      } else {
        setText(dd, escapeText(value));
      }
      append(dl, dt);
      append(dl, dd);
    };
    field("Name", detail.name);
    field("Version", detail.version);
    field("Status", detail.status);
    field("Purpose", detail.purpose);
    field("Usefulness", detail.usefulness);
    field("Input", detail.input);
    field("Processing", detail.processing);
    field("Output", detail.output);
    field("Model", detail.model);
    field("Chainable", detail.chainable ? "yes" : "no");
    field("Pipeline", detail.pipeline);
    field("Tools", detail.tools);
    field("Sub-agents", detail.sub_agents);
    field("Examples", detail.examples);
    clearChildren(agentDetailEl);
    append(agentDetailEl, dl);
  } catch (err) {
    setText(agentDetailEl, "Failed to load agent detail: " + escapeText(err.message));
  }
}

function renderSimpleTable(container, columns, rows, onRowClick) {
  clearChildren(container);
  const table = createElement("table");
  const thead = createElement("thead");
  const headerRow = createElement("tr");
  columns.forEach(function (heading) {
    const th = createElement("th");
    setText(th, heading);
    append(headerRow, th);
  });
  append(thead, headerRow);
  append(table, thead);

  const tbody = createElement("tbody");
  rows.forEach(function (row) {
    const tr = createElement("tr", { class: onRowClick ? "clickable" : "" });
    if (onRowClick) {
      tr.tabIndex = 0;
      tr.addEventListener("click", function () { onRowClick(row); });
      tr.addEventListener("keydown", function (ev) {
        if (ev.key === "Enter" || ev.key === " ") {
          ev.preventDefault();
          onRowClick(row);
        }
      });
    }
    columns.forEach(function (col) {
      const td = createElement("td");
      setText(td, escapeText(row[col]));
      append(tr, td);
    });
    append(tbody, tr);
  });
  append(table, tbody);
  append(container, table);
}

async function loadVersions() {
  clearChildren(versionsOutputEl);
  const name = versionsAgentSelect.value;
  if (!name) return;
  setText(versionsOutputEl, "Loading versions for " + escapeText(name) + "…");
  try {
    const data = await fetchJson("/agents/" + encodeURIComponent(name) + "/versions");
    const rows = (data.versions || []).map(function (v) {
      return {
        Version: v.version,
        Status: v.status,
        Author: v.author,
        "Created at": v.created_at,
        Default: v.default,
        "Change note": v.change_note,
      };
    });
    clearChildren(versionsOutputEl);
    const h = createElement("h3");
    setText(h, "Lineage for " + escapeText(name));
    append(versionsOutputEl, h);
    const versionsTableWrap = createElement("div");
    append(versionsOutputEl, versionsTableWrap);
    renderSimpleTable(versionsTableWrap, ["Version", "Status", "Author", "Created at", "Default", "Change note"], rows);
    if (data.lineage && data.lineage.length) {
      const h2 = createElement("h3");
      setText(h2, "Lineage (SUPERSEDES chain)");
      append(versionsOutputEl, h2);
      const lineageRows = data.lineage.map(function (l) {
        return { Version: l.version, Status: l.status, Chain: l.chain };
      });
      const lineageTableWrap = createElement("div");
      append(versionsOutputEl, lineageTableWrap);
      renderSimpleTable(lineageTableWrap, ["Version", "Status", "Chain"], lineageRows);
    }
  } catch (err) {
    setText(versionsOutputEl, "Failed to load versions: " + escapeText(err.message));
  }
}

async function loadExecutions() {
  clearChildren(executionsTableEl);
  clearChildren(executionDetailEl);
  setText(executionsTableEl, "Loading executions…");
  try {
    const rows = await fetchJson("/executions?limit=25");
    const normalized = rows.map(function (row) {
      return {
        Execution: row.execution,
        Status: row.status,
        Breakpoint: row.breakpoint,
        Query: row.query,
        "Created at": row.created_at,
      };
    });
    renderSimpleTable(executionsTableEl, ["Execution", "Status", "Breakpoint", "Query", "Created at"], normalized, function (row) {
      loadExecutionDetail(row.Execution);
    });
  } catch (err) {
    setText(executionsTableEl, "Failed to load executions: " + escapeText(err.message));
  }
}

async function loadExecutionDetail(id) {
  clearChildren(executionDetailEl);
  setText(executionDetailEl, "Loading execution " + escapeText(id) + "…");
  try {
    const detail = await fetchJson("/executions/" + encodeURIComponent(id));
    clearChildren(executionDetailEl);
    const h = createElement("h3");
    setText(h, "Execution " + escapeText(detail.execution_id));
    append(executionDetailEl, h);
    const pre = createElement("pre");
    setText(pre, detail.rendered || "");
    append(executionDetailEl, pre);
    const out = detail.output || {};
    if (out.kind === "image" && out.data_base64) {
      renderOutput(detail, executionDetailEl);
    } else if (out.text) {
      const p = createElement("p");
      setText(p, "Stored output: " + escapeText(out.text));
      append(executionDetailEl, p);
    }
  } catch (err) {
    setText(executionDetailEl, "Failed to load execution: " + escapeText(err.message));
  }
}

async function loadProposals() {
  clearChildren(proposalsTableEl);
  setText(proposalsTableEl, "Loading proposals…");
  try {
    const data = await fetchJson("/proposals");
    const rows = (data.proposals || []).map(function (p) {
      return {
        Proposal: p.proposal,
        Status: p.status,
        Agent: p.agent,
        "Base version": p.base_version,
        "Candidate version": p.candidate_version,
        Trigger: p.trigger,
        "Created at": p.created_at,
      };
    });
    clearChildren(proposalsTableEl);
    renderSimpleTable(proposalsTableEl, ["Proposal", "Status", "Agent", "Base version", "Candidate version", "Trigger", "Created at"], rows);
  } catch (err) {
    setText(proposalsTableEl, "Failed to load proposals: " + escapeText(err.message));
  }
}

async function loadBanner() {
  try {
    const health = await fetchJson("/health");
    setText(bannerTarget, "Writing to: " + escapeText(health.target));
  } catch (_err) {
    setText(bannerTarget, "Writing to: unknown");
  }
}

function attachAskUploadCheck() {
  askAttachment.addEventListener("change", function () {
    const file = askAttachment.files && askAttachment.files[0];
    if (!file) {
      setText(askUploadLimit, "");
      return;
    }
    if (file.size > SERVER_LIMIT_BYTES) {
      setText(askUploadLimit, "File too large: " + file.size + " bytes. Limit is " + SERVER_LIMIT_BYTES + " bytes (1 MB). Choose a smaller file.");
      askAttachment.value = "";
    } else {
      setText(askUploadLimit, "");
    }
  });
}

document.addEventListener("DOMContentLoaded", function () {
  setupTabs();
  switchTab("ask");
  attachAskUploadCheck();
  askForm.addEventListener("submit", handleAskSubmit);
  versionsAgentSelect.addEventListener("change", loadVersions);
  loadBanner();
  loadAgents();
  loadExecutions();
  loadProposals();
});
