// Audit Web v4.2.0 — front-end logic.
// No frameworks. Uses plain DOM + fetch.

(function () {
  "use strict";

  const $ = (id) => document.getElementById(id);

  const scenarioSelect = $("scenario-select");
  const summary = $("scenario-summary");
  const patientTa = $("patient-state");
  const dialogueTa = $("dialogue-output");
  const runBtn = $("run-btn");
  const resetBtn = $("reset-btn");
  const traceDiv = $("trace");
  const decisionText = $("decision-text");
  const decisionCard = $("decision-card");
  const decisionRule = $("decision-rule");
  const decisionBasis = $("decision-basis");
  const visible = $("visible-response");
  const reviewerMessage = $("reviewer-message");
  const sent = $("original-sent");
  const iveList = $("ive-list");
  const missingList = $("missing-list");
  const consList = $("cons-list");
  const medList = $("med-list");
  const normalizedPS = $("normalized-patient-state");
  const drugCtx = $("drug-context");
  const candIds = $("candidate-rule-ids");
  const evaldIds = $("evaluated-rule-ids");
  const channelsPre = $("retrieval-channels");
  const timingTable = $("timing-table").querySelector("tbody");
  const rawJson = $("raw-json");
  const healthInd = $("health-indicator");
  const rulesetInd = $("ruleset-indicator");

  let currentScenarios = [];

  // ------------------------------------------------------------ helpers

  function pretty(value, indent = 2) {
    try {
      return JSON.stringify(value, null, indent);
    } catch (e) {
      return String(value);
    }
  }

  function setText(el, value) {
    el.textContent = value;
  }

  function clearChildren(el) {
    while (el.firstChild) el.removeChild(el.firstChild);
  }

  // ------------------------------------------------------------ health

  async function checkHealth() {
    try {
      const res = await fetch("/api/health");
      if (!res.ok) throw new Error("health not ok");
      const data = await res.json();
      healthInd.textContent = "就绪";
      healthInd.className = "badge badge-pass";
      rulesetInd.textContent = "ruleset: " + (data.ruleset || "—");
    } catch (e) {
      healthInd.textContent = "未就绪";
      healthInd.className = "badge badge-block";
    }
  }

  // ------------------------------------------------------------ scenarios

  async function loadScenarios() {
    try {
      const res = await fetch("/api/scenarios");
      const data = await res.json();
      currentScenarios = (data.scenarios || []);
      scenarioSelect.innerHTML = "";
      const placeholder = document.createElement("option");
      placeholder.value = "";
      placeholder.textContent = "(选择场景…)";
      scenarioSelect.appendChild(placeholder);
      currentScenarios.forEach((group) => {
        const og = document.createElement("optgroup");
        og.label = group.group;
        group.items.forEach((item) => {
          const opt = document.createElement("option");
          opt.value = item.id;
          opt.textContent = `${item.title}`;
          og.appendChild(opt);
        });
        scenarioSelect.appendChild(og);
      });
    } catch (e) {
      summary.textContent = "无法加载场景预设: " + e.message;
    }
  }

  function findScenario(id) {
    for (const group of currentScenarios) {
      for (const item of (group.items || [])) {
        if (item.id === id) return item;
      }
    }
    return null;
  }

  scenarioSelect.addEventListener("change", () => {
    const id = scenarioSelect.value;
    if (!id) return;
    const s = findScenario(id);
    if (!s) return;
    patientTa.value = pretty(s.patient_state || {});
    dialogueTa.value = pretty(s.dialogue_output || {});
    summary.textContent = s.summary || s.title || "";
  });

  // ------------------------------------------------------------ run

  runBtn.addEventListener("click", async () => {
    summary.textContent = "运行中…";
    let patient_state, dialogue_output;
    try {
      patient_state = JSON.parse(patientTa.value || "{}");
    } catch (e) {
      summary.textContent = "patient_state JSON 解析失败: " + e.message;
      return;
    }
    try {
      dialogue_output = JSON.parse(dialogueTa.value || "{}");
    } catch (e) {
      summary.textContent = "dialogue_output JSON 解析失败: " + e.message;
      return;
    }

    let resp;
    try {
      resp = await fetch("/api/audit", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ patient_state, dialogue_output }),
      });
    } catch (e) {
      summary.textContent = "请求失败: " + e.message;
      return;
    }
    if (!resp.ok) {
      summary.textContent = "服务端错误 " + resp.status;
      return;
    }
    const data = await resp.json();
    summary.textContent = "";
    renderResult(data);
  });

  resetBtn.addEventListener("click", () => {
    patientTa.value = "";
    dialogueTa.value = "";
    summary.textContent = "已清空";
  });

  // ------------------------------------------------------------ result

  function renderResult(payload) {
    const decision = payload.decision || "—";
    const r = payload.audit_report || {};

    decisionText.textContent = decision;
    let cls = "decision-unknown";
    let rule = "";
    if (decision === "BLOCK") { cls = "decision-block"; rule = "原始 LLM 回复已被拦截。"; }
    else if (decision === "REVIEW") { cls = "decision-review"; rule = "需要医生 / 药师复核。"; }
    else if (decision === "PASS") { cls = "decision-pass"; rule = "原始 LLM 回复已发出。"; }
    decisionCard.className = "decision-card " + cls;
    decisionRule.textContent = rule;

    decisionBasis.innerHTML =
      `<strong>decision_basis</strong>: ` +
      ((r.decision_basis || []).map(b => `<span class="badge badge-info">${b}</span>`).join(" ")) +
      ` · <strong>ruleset_version</strong>: <code>${r.ruleset_version || "—"}</code>` +
      ` · <strong>input_schema_version</strong>: <code>${r.input_schema_version || "—"}</code>`;

    setText(visible, payload.patient_visible_response || "—");
    setText(reviewerMessage, r.reviewer_message || "—");

    const sentFlag = payload.original_llm_reply_was_sent === true;
    sent.textContent = sentFlag ? "Yes (已发出)" : "No (已拦截)";
    sent.className = "big-flag " + (sentFlag ? "flag-pass" : "flag-block");

    renderFindings(iveList, r.input_validation_errors || []);
    renderFindings(missingList, (r.missing_context_fields || []).map(m => ({
        code: "MISSING_CONTEXT",
        severity: "REVIEW",
        message: `${m.field_path} (rules=${(m.related_rule_ids || []).join(", ") || "—"})`,
        details: m,
    })));
    renderFindings(consList, r.consistency_violations || []);
    renderFindings(medList, r.medical_violations || []);

    setText(normalizedPS,
      pretty({
        patient_id: "(redacted)",
        patient_state_projection: r.developer_diagnostics && r.developer_diagnostics.normalized_patient_state || "(see input panel)",
      }));
    setText(drugCtx, pretty(r.developer_diagnostics && r.developer_diagnostics.drug_context || {}));
    setText(candIds, pretty(r.candidate_rule_ids || []));
    setText(evaldIds, pretty(r.evaluated_rule_ids || []));
    setText(channelsPre, pretty(r.retrieval_channels || []));

    renderTiming(r.timing_ms || {});
    renderTrace(r);
    setText(rawJson, pretty(r, 2));
  }

  function renderFindings(ul, list) {
    clearChildren(ul);
    if (!list.length) {
      const li = document.createElement("li");
      li.className = "finding-empty";
      li.textContent = "(无)";
      ul.appendChild(li);
      return;
    }
    list.forEach((f) => {
      const li = document.createElement("li");
      li.className = "finding finding-" + (f.severity || "REVIEW").toLowerCase();
      li.innerHTML =
        `<span class="finding-code">${f.code || "—"}</span>` +
        `<span class="finding-sev sev-${f.severity || "REVIEW"}">${f.severity || "REVIEW"}</span>` +
        `<div class="finding-msg">${escapeHtml(f.message || "")}</div>` +
        (f.details ? `<pre class="finding-details">${escapeHtml(pretty(f.details))}</pre>` : "");
      ul.appendChild(li);
    });
  }

  function escapeHtml(s) {
    return String(s).replace(/[<>&]/g, (c) => ({
      "<": "&lt;", ">": "&gt;", "&": "&amp;",
    }[c]));
  }

  // ------------------------------------------------------------ trace (v4.2.0)

  function renderTrace(report) {
    clearChildren(traceDiv);

    const steps = [];
    steps.push(stepEl("📥", "输入校验 (input_validation)",
      pretty(report.input_validation_errors || [])));
    steps.push(stepEl("🧪", "标准化 (normalized draft)",
      pretty({
        reply_text_len: (report.developer_diagnostics && report.developer_diagnostics.normalized_draft || {}).reply_text
          ? (report.developer_diagnostics.normalized_draft.reply_text || "").length : 0,
        medication_actions: (report.developer_diagnostics && report.developer_diagnostics.normalized_draft || {}).medication_actions,
      })));
    steps.push(stepEl("💊", "药物上下文 (DrugContext)",
      pretty(report.developer_diagnostics && report.developer_diagnostics.drug_context || {})));
    steps.push(stepEl("⚠️", "必要上下文 (required_context_checker)",
      pretty(report.developer_diagnostics && report.developer_diagnostics.required_context || {})));
    steps.push(stepEl("📡", "召回通道 (retrieval_channels + trace)",
      pretty({
        channels: report.retrieval_channels || [],
        trace: report.retrieval_trace || [],
      })));
    steps.push(stepEl("🎯", "候选规则 (candidate_rule_ids)",
      pretty(report.candidate_rule_ids || [])));
    steps.push(stepEl("🧠", "执行轨迹 (evaluation_trace)",
      pretty(report.evaluation_trace || [])));
    steps.push(stepEl("🧯", "一致性 (consistency_violations)",
      pretty(report.consistency_violations || [])));
    steps.push(stepEl("🚦", "决策 (decision + basis)",
      pretty({
        decision: report.decision,
        decision_basis: report.decision_basis,
        medical_violations: (report.medical_violations || []).length,
      })));

    steps.forEach((s) => traceDiv.appendChild(s));
    if (steps.length) {
      steps[0].dataset.open = "true";
      steps[0].querySelector(".trace-body").style.display = "block";
    }
  }

  function stepEl(icon, title, content) {
    const wrap = document.createElement("div");
    wrap.className = "trace-step";
    wrap.dataset.open = "false";

    const summaryRow = document.createElement("div");
    summaryRow.className = "trace-summary";
    summaryRow.innerHTML =
      `<span class="trace-icon">${icon}</span>` +
      `<span class="trace-title">${title}</span>` +
      `<span class="trace-toggle">▼</span>`;

    const body = document.createElement("div");
    body.className = "trace-body";
    const pre = document.createElement("pre");
    pre.className = "json";
    pre.textContent = content;
    body.appendChild(pre);

    summaryRow.addEventListener("click", () => {
      const isOpen = wrap.dataset.open === "true";
      wrap.dataset.open = isOpen ? "false" : "true";
      body.style.display = isOpen ? "none" : "block";
    });
    wrap.appendChild(summaryRow);
    wrap.appendChild(body);
    body.style.display = "none";
    return wrap;
  }

  function renderTiming(t) {
    clearChildren(timingTable);
    const phases = [
      "input_validation", "normalization", "matching", "text_parsing",
      "risk_detection", "required_context", "consistency",
      "candidate_selection", "evaluation", "logging", "total",
    ];
    phases.forEach((p) => {
      const v = t[p] || 0;
      const tr = document.createElement("tr");
      tr.innerHTML =
        `<td class='k'>${p}</td><td class='v'>${Number(v).toFixed(3)} ms</td>`;
      timingTable.appendChild(tr);
    });
  }

  // ------------------------------------------------------------ boot
  checkHealth();
  loadScenarios();
})();