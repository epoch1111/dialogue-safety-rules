// Audit Web v4.2.1 — visual dashboard
// Pure DOM + fetch, no frameworks.
// IMPORTANT: every UI value comes from the REAL audit response, never
// from a scenario's name, expected_decision, or any other hardcoded
// hint.

(function () {
  "use strict";

  const $ = (id) => document.getElementById(id);
  const escapeHtml = (s) => String(s ?? "").replace(/[<>&]/g, (c) => ({
    "<": "&lt;", ">": "&gt;", "&": "&amp;",
  }[c]));
  const pretty = (v, indent = 2) => JSON.stringify(v, null, indent);

  // ----- module state -----
  let scenarios = [];
  let currentScenario = null;
  let meta = {
    project_version: "—",
    ruleset_version: "—",
    input_schema_version: "1.0",
    backend_ok: false,
  };
  let lastAudit = null;
  let uiTrace = null;
  let ruleTypeCatalog = [];

  // ----- DOM refs -----
  const els = {
    brandSub:        $("brand-sub"),
    badgeVersion:    $("badge-version"),
    badgeRuleset:    $("badge-ruleset"),
    badgeSchema:     $("badge-schema"),
    badgeStrict:     $("badge-strict"),
    badgeCompat:     $("badge-compat"),
    badgeBackend:    $("badge-backend"),
    scenarioSelect:  $("scenario-select"),
    scenarioSummary: $("scenario-summary"),
    clinicalCasePanel: $("clinical-case-panel"),
    caseProfileCards: $("case-profile-cards"),
    caseEvidenceCards: $("case-evidence-cards"),
    strictToggle:    $("strict-toggle"),
    compatToggle:    $("compat-toggle"),
    simulateToggle:  $("simulate-toggle"),
    patientCards:    $("patient-cards"),
    dialogueCards:   $("dialogue-cards"),
    patientTa:       $("patient-state"),
    dialogueTa:      $("dialogue-output"),
    runBtn:          $("run-btn"),
    resetBtn:        $("reset-btn"),
    stepRow1:        $("step-row-1"),
    stepRow2:        $("step-row-2"),
    stepLoading:     $("step-loading"),
    expandAll:       $("expand-all"),
    collapseAll:     $("collapse-all"),
    decisionText:    $("decision-text"),
    decisionRule:    $("decision-rule"),
    decisionCard:    $("decision-card"),
    decisionBasis:   $("decision-basis"),
    visibleResponse: $("visible-response"),
    originalSent:    $("original-sent"),
    findingsCounts:  $("findings-counts"),
    timingTable:     $("timing-table").querySelector("tbody"),
    bottomRuleExample: $("bottom-rule-example"),
    bottomDrugContext: $("bottom-drug-context"),
    bottomRuleTypes:   $("bottom-rule-types"),
    rawInput:        $("raw-input"),
    normalized:      $("normalized"),
    rawAudit:        $("raw-audit"),
    footerMeta:      $("footer-meta"),
  };

  // ==============================================================
  // helpers
  // ==============================================================

  function clearChildren(el) {
    while (el.firstChild) el.removeChild(el.firstChild);
  }

  function setText(el, text) {
    el.textContent = text;
  }

  function el(tag, attrs, ...children) {
    const node = document.createElement(tag);
    if (attrs) {
      for (const k in attrs) {
        if (k === "class") node.className = attrs[k];
        else if (k === "text") node.textContent = attrs[k];
        else if (k.startsWith("on") && typeof attrs[k] === "function") {
          node.addEventListener(k.slice(2).toLowerCase(), attrs[k]);
        } else if (k === "data") {
          for (const dk in attrs.data) node.dataset[dk] = attrs.data[dk];
        } else {
          node.setAttribute(k, attrs[k]);
        }
      }
    }
    for (const c of children.flat()) {
      if (c == null) continue;
      node.appendChild(typeof c === "string" ? document.createTextNode(c) : c);
    }
    return node;
  }

  function dedent(s) {
    return s.replace(/\n[ \t]+/g, "\n").replace(/^\n/, "").replace(/\n$/, "");
  }

  // ==============================================================
  // boot — health + scenarios + rule-type catalog
  // ==============================================================

  async function checkHealth() {
    try {
      const res = await fetch("/api/health");
      if (!res.ok) throw new Error("health not ok");
      const data = await res.json();
      meta.backend_ok = true;
      meta.project_version = data.project_version || "—";
      meta.ruleset_version = data.ruleset || "—";
      meta.input_schema_version = data.input_schema_version || "1.0";
    } catch (e) {
      meta.backend_ok = false;
    }
    updateBadges();
  }

  async function loadScenarios() {
    try {
      const res = await fetch("/api/scenarios");
      const data = await res.json();
      scenarios = data.scenarios || [];
      renderScenarioSelect();
    } catch (e) {
      setText(els.scenarioSummary, "无法加载场景: " + e.message);
    }
  }

  async function loadRuleTypeCatalog() {
    try {
      const res = await fetch("/api/rule-types");
      const data = await res.json();
      ruleTypeCatalog = data.rule_types || [];
      renderRuleTypes(ruleTypeCatalog, new Set());
    } catch (e) {
      // best-effort; ignore
    }
  }

  function updateBadges() {
    setText(els.brandSub,
      `${meta.project_version} · 严格输入契约 + 必要上下文 + 确定性规则`);
    setText(els.badgeVersion, `v${meta.project_version}`);
    setText(els.badgeRuleset, `ruleset: ${meta.ruleset_version}`);
    setText(els.badgeSchema, `schema: ${meta.input_schema_version}`);

    const strictOn = els.strictToggle.checked;
    const compatOn = els.compatToggle.checked;
    els.badgeStrict.textContent = strictOn ? "strict mode" : "strict mode (off)";
    els.badgeStrict.className = "badge " + (strictOn ? "badge-info" : "badge-neutral");
    els.badgeCompat.textContent = compatOn ? "compat: on" : "compat: off";
    els.badgeCompat.className = "badge " + (compatOn ? "badge-warn" : "badge-neutral");

    if (meta.backend_ok) {
      els.badgeBackend.textContent = "后端 OK";
      els.badgeBackend.className = "badge badge-pass";
    } else {
      els.badgeBackend.textContent = "后端不可用";
      els.badgeBackend.className = "badge badge-block";
    }

    setText(els.footerMeta,
      `Dialogue Agent Safety Rule Engine · v${meta.project_version} · ` +
      `ruleset=${meta.ruleset_version} · schema=${meta.input_schema_version} · ` +
      `strict=${strictOn} · compat=${compatOn}`);
  }

  // ==============================================================
  // scenario picker
  // ==============================================================

  function renderScenarioSelect() {
    clearChildren(els.scenarioSelect);
    const placeholder = el("option", { value: "" }, "(选择场景…)");
    els.scenarioSelect.appendChild(placeholder);
    scenarios.forEach((group) => {
      const og = el("optgroup", { label: group.group });
      (group.items || []).forEach((item) => {
        og.appendChild(el("option", { value: item.id }, item.title));
      });
      els.scenarioSelect.appendChild(og);
    });
  }

  function findScenario(id) {
    for (const group of scenarios) {
      for (const item of (group.items || [])) {
        if (item.id === id) return item;
      }
    }
    return null;
  }

  els.scenarioSelect.addEventListener("change", () => {
    const s = findScenario(els.scenarioSelect.value);
    if (!s) return;
    currentScenario = s;
    els.patientTa.value = pretty(s.patient_state || {});
    els.dialogueTa.value = pretty(s.dialogue_output || {});
    setText(els.scenarioSummary, s.summary || s.title || "");
    renderClinicalCase(s);
    renderPatientState(s.patient_state || {});
    renderDialogueOutput(s.dialogue_output || {});
  });

  function renderClinicalCase(scenario) {
    const profile = scenario.case_profile;
    const evidence = scenario.retrieved_evidence;
    const isClinicalCase = Boolean(profile || evidence);
    els.clinicalCasePanel.hidden = !isClinicalCase;
    clearChildren(els.caseProfileCards);
    clearChildren(els.caseEvidenceCards);
    if (!isClinicalCase) return;

    const labels = [
      ["年龄", profile?.age], ["性别", profile?.sex],
      ["就诊类型", profile?.visit_type], ["主诉", profile?.chief_complaint],
      ["病史摘要", profile?.history_summary], ["当前情况", profile?.current_condition],
      ["已知疾病", (profile?.known_conditions || []).join("、") || "—"],
      ["案例备注", (profile?.case_notes || []).join("；") || "—"],
    ];
    labels.forEach(([label, value]) => {
      els.caseProfileCards.appendChild(el("div", { class: "card" },
        el("div", { class: "card-label" }, label),
        el("div", { class: "meta-line" }, String(value ?? "—")),
      ));
    });
    (evidence || []).forEach((item) => {
      els.caseEvidenceCards.appendChild(el("article", { class: "evidence-card" },
        el("div", { class: "card-row" },
          el("span", { class: "card-label" }, item.evidence_id || "—"),
          el("span", { class: "badge badge-info" }, `score ${item.retrieval_score ?? "—"}`),
        ),
        el("div", { class: "evidence-title" }, item.source_title || "—"),
        el("div", { class: "meta-line" }, item.section || "—"),
        el("div", { class: "evidence-excerpt" }, item.excerpt || "—"),
        el("div", { class: "meta-line" },
          item.is_demo_evidence ? "演示证据" : "非演示证据"),
      ));
    });
  }

  els.strictToggle.addEventListener("change", updateBadges);
  els.compatToggle.addEventListener("change", updateBadges);

  // ==============================================================
  // patient_state + dialogue_output visual cards
  // ==============================================================

  function renderPatientState(ps) {
    clearChildren(els.patientCards);
    const root = document.createDocumentFragment();

    root.appendChild(cardSection("基本信息", [
      ["患者 ID", ps.patient_id || "—"],
    ]));

    const meds = ps.current_medications || [];
    const medNodes = meds.length === 0
      ? [el("div", { class: "empty" }, "(无当前用药)")]
      : meds.map(renderMedItem);
    root.appendChild(cardSection("当前用药 current_medications", medNodes));

    const codes = ps.disease_codes || [];
    const codeVal = codes.length === 0
      ? "(无)"
      : codes.join("、");
    root.appendChild(cardSection("疾病 disease_codes", [
      ["诊断列表", codeVal],
    ]));

    const meas = ps.measurements || {};
    const measNodes = Object.keys(meas).length === 0
      ? [el("div", { class: "empty" }, "(无 measurement 记录)")]
      : Object.keys(meas).map((k) => renderMeasurementItem(k, meas[k]));
    root.appendChild(cardSection("检查指标 measurements", measNodes));

    const flags = ps.clinical_flags || {};
    const flagNodes = Object.keys(flags).length === 0
      ? [el("div", { class: "empty" }, "(无 clinical_flags)")]
      : Object.entries(flags).map(([k, v]) =>
          el("div", { class: "card" },
            el("dl", { class: "kv-grid" },
              el("dt", null, k),
              el("dd", null, v === true ? "true" : v === false ? "false" : String(v ?? "—"))
            )
          )
        );
    root.appendChild(cardSection("临床标记 clinical_flags", flagNodes));

    const allergies = ps.allergies || [];
    root.appendChild(cardSection("过敏 allergies",
      allergies.length === 0 ? ["(无)"] : allergies));

    els.patientCards.appendChild(root);
  }

  function cardSection(label, body) {
    const wrap = el("div", { class: "card" },
      el("div", { class: "card-label" }, label),
    );
    if (Array.isArray(body)) {
      if (body.length === 0 || (typeof body[0] === "string" && body[0] === "(无)")) {
        wrap.appendChild(el("div", { class: "empty" },
          typeof body[0] === "string" ? body[0] : "(无)"));
      } else if (typeof body[0] === "string") {
        // list of strings
        const list = el("div", { class: "kv-grid" });
        body.forEach((s, i) => {
          list.appendChild(el("dt", null, `${i + 1}.`));
          list.appendChild(el("dd", null, s));
        });
        wrap.appendChild(list);
      } else if (typeof body[0] === "object" && body[0] !== null && "tagName" in body[0]) {
        // list of element nodes
        body.forEach((n) => wrap.appendChild(n));
      } else {
        // list of [label, value] tuples
        const list = el("dl", { class: "kv-grid" });
        body.forEach(([k, v]) => {
          list.appendChild(el("dt", null, k));
          list.appendChild(el("dd", null, String(v)));
        });
        wrap.appendChild(list);
      }
    } else if (typeof body === "string") {
      wrap.appendChild(el("div", null, body));
    } else {
      wrap.appendChild(body);
    }
    return wrap;
  }

  function renderMedItem(med) {
    const act = med.status || "active";
    const cls = `medication-item ${act}`;
    const root = el("div", { class: cls },
      el("div", { class: "action-row" },
        el("span", { class: "drug-name" }, med.drug_name || med.drug_id || "—"),
        el("span", { class: "drug-id" }, med.drug_id ? `(${med.drug_id})` : ""),
        el("span", { class: "action-tag" }, act),
      ),
    );
    const parts = [];
    if (med.dose_value != null) {
      parts.push(["剂量", `${med.dose_value} ${med.dose_unit || ""}`.trim()]);
    }
    if (med.frequency_per_day != null) {
      parts.push(["每日次数", String(med.frequency_per_day)]);
    }
    if (med.route) parts.push(["途径", med.route]);
    if (med.replace_drug_id || med.replace_drug_name) {
      parts.push(["替换自", `${med.replace_drug_name || med.replace_drug_id}`]);
    }
    if (parts.length > 0) {
      const list = el("dl", { class: "kv-grid" });
      parts.forEach(([k, v]) => {
        list.appendChild(el("dt", null, k));
        list.appendChild(el("dd", null, v));
      });
      root.appendChild(list);
    }
    return root;
  }

  function renderMeasurementItem(key, m) {
    const v = m.value ?? "—";
    const u = m.unit ?? "";
    const observed = m.observed_at ?? "—";
    const source = m.source ?? "—";
    const confirmed = m.confirmed;
    let confirmBadge = "badge-pass";
    let confirmLabel = "已确认";
    if (confirmed === false) {
      confirmBadge = "badge-warn";
      confirmLabel = "未确认";
    } else if (confirmed !== true) {
      confirmBadge = "badge-neutral";
      confirmLabel = "未提供";
    }
    return el("div", { class: "card" },
      el("div", { class: "card-row" },
        el("span", { class: "card-label" }, key),
        el("span", { class: `badge ${confirmBadge}` }, confirmLabel),
      ),
      el("dl", { class: "kv-grid" },
        el("dt", null, "数值"), el("dd", null, `${v} ${u}`.trim()),
        el("dt", null, "检测时间"), el("dd", null, observed),
        el("dt", null, "来源"), el("dd", null, source),
      )
    );
  }

  function renderDialogueOutput(do_) {
    clearChildren(els.dialogueCards);
    const root = document.createDocumentFragment();

    const reply = do_.reply_text || "";
    root.appendChild(el("div", { class: "card" },
      el("div", { class: "card-label" }, "正文 reply_text"),
      el("div", null, reply || "(空)"),
    ));

    if (do_.requires_review || (do_.uncertainty_reasons || []).length > 0) {
      const banner = el("div", { class: "warning-banner" },
        do_.requires_review
          ? "LLM 自报 requires_review = true"
          : `LLM 自报 uncertainty_reasons = ${(do_.uncertainty_reasons || []).join("、")}`,
      );
      root.appendChild(banner);
    }

    const meds = do_.medication_actions || [];
    const medNodes = meds.length === 0
      ? [el("div", { class: "empty" }, "(无 medication_actions)")]
      : meds.map(renderDialogueMedItem);
    root.appendChild(cardSection("药物建议 medication_actions", medNodes));

    const foods = do_.food_advice || [];
    const foodNodes = foods.length === 0
      ? [el("div", { class: "empty" }, "(无 food_advice)")]
      : foods.map(renderDialogueFoodItem);
    root.appendChild(cardSection("饮食建议 food_advice", foodNodes));

    const exes = do_.exercise_advice || [];
    const exNodes = exes.length === 0
      ? [el("div", { class: "empty" }, "(无 exercise_advice)")]
      : exes.map(renderDialogueExerciseItem);
    root.appendChild(cardSection("运动建议 exercise_advice", exNodes));

    const cares = do_.care_actions || [];
    const careNodes = cares.length === 0
      ? [el("div", { class: "empty" }, "(无 care_actions)")]
      : cares.map(renderDialogueCareItem);
    root.appendChild(cardSection("就医建议 care_actions", careNodes));

    els.dialogueCards.appendChild(root);
  }

  function renderDialogueMedItem(ma) {
    const act = ma.action || "";
    const cls = `medication-item ${act}`;
    const root = el("div", { class: cls },
      el("div", { class: "action-row" },
        el("span", { class: "drug-name" },
          ma.drug_name || ma.drug_id || "—"),
        el("span", { class: "drug-id" }, ma.drug_id ? `(${ma.drug_id})` : ""),
        el("span", { class: "action-tag" }, act || "—"),
      ),
    );
    const list = el("dl", { class: "kv-grid" });
    if (ma.dose_value != null)
      list.appendChild(el("dt", null, "剂量"),
        el("dd", null, `${ma.dose_value} ${ma.dose_unit || ""}`.trim()));
    if (ma.frequency_per_day != null)
      list.appendChild(el("dt", null, "每日次数"),
        el("dd", null, String(ma.frequency_per_day)));
    if (ma.route) list.appendChild(el("dt", null, "途径"),
      el("dd", null, ma.route));
    if (ma.use_current_regimen) list.appendChild(el("dt", null, "继续当前方案"),
      el("dd", null, "✓ 是"));
    if (ma.replace_drug_id || ma.replace_drug_name)
      list.appendChild(el("dt", null, "替换自"),
        el("dd", null, `${ma.replace_drug_name || ma.replace_drug_id}`));
    root.appendChild(list);
    return root;
  }

  function renderDialogueFoodItem(fa) {
    const root = el("div", { class: "food-item" },
      el("div", { class: "action-row" },
        el("span", { class: "drug-name" }, fa.food_name || "—"),
        el("span", { class: "drug-id" }, fa.food_concept_id ? `(${fa.food_concept_id})` : ""),
        el("span", { class: "action-tag" }, fa.action || "—"),
      ),
    );
    const list = el("dl", { class: "kv-grid" });
    if (fa.amount != null)
      list.appendChild(el("dt", null, "量"), el("dd", null, String(fa.amount)));
    if (fa.frequency)
      list.appendChild(el("dt", null, "频次"), el("dd", null, fa.frequency));
    if (fa.instruction)
      list.appendChild(el("dt", null, "说明"), el("dd", null, fa.instruction));
    root.appendChild(list);
    return root;
  }

  function renderDialogueExerciseItem(ea) {
    const root = el("div", { class: "exercise-item" },
      el("div", { class: "action-row" },
        el("span", { class: "drug-name" }, ea.activity_name || "—"),
        el("span", { class: "drug-id" }, ea.activity_concept_id ? `(${ea.activity_concept_id})` : ""),
        el("span", { class: "action-tag" }, ea.action || "—"),
      ),
    );
    const list = el("dl", { class: "kv-grid" });
    if (ea.intensity)
      list.appendChild(el("dt", null, "强度"), el("dd", null, ea.intensity));
    if (ea.duration_min != null)
      list.appendChild(el("dt", null, "时长"), el("dd", null, `${ea.duration_min} min`));
    if (ea.frequency_per_week != null)
      list.appendChild(el("dt", null, "周频次"),
        el("dd", null, `${ea.frequency_per_week}/week`));
    if (ea.instruction)
      list.appendChild(el("dt", null, "说明"), el("dd", null, ea.instruction));
    root.appendChild(list);
    return root;
  }

  function renderDialogueCareItem(ca) {
    const root = el("div", { class: "card" },
      el("div", { class: "action-row" },
        el("span", { class: "drug-name" }, ca.type || "—"),
        el("span", { class: "action-tag" }, ca.action || "—"),
      ),
    );
    if (ca.target || ca.urgency) {
      const list = el("dl", { class: "kv-grid" });
      if (ca.target) list.appendChild(el("dt", null, "对象"), el("dd", null, ca.target));
      if (ca.urgency) list.appendChild(el("dt", null, "紧急度"), el("dd", null, ca.urgency));
      root.appendChild(list);
    }
    return root;
  }

  // ==============================================================
  // 9-step flow rendering
  // ==============================================================

  function renderAuditSteps(payload) {
    uiTrace = payload.ui_trace;
    if (!uiTrace || !Array.isArray(uiTrace.steps) || uiTrace.steps.length === 0) {
      // Fallback: derive status from payload directly
      uiTrace = deriveUiTraceFallback(payload);
    }
    const steps = uiTrace.steps.slice(0, 6);
    const steps2 = uiTrace.steps.slice(6, 9);
    clearChildren(els.stepRow1);
    clearChildren(els.stepRow2);
    steps.forEach((s) => els.stepRow1.appendChild(renderStepCard(s)));
    steps2.forEach((s) => els.stepRow2.appendChild(renderStepCard(s)));
  }

  function deriveUiTraceFallback(payload) {
    const r = payload.audit_report || {};
    const ive = r.input_validation_errors || [];
    const cons = r.consistency_violations || [];
    const meds = r.medical_violations || [];
    const missing = r.missing_context_fields || [];
    const decision = r.decision || "PASS";
    function status(block, review, passed) {
      if (block) return "blocked";
      if (review) return "warning";
      return "passed";
    }
    function anyBlock(items) {
      return items.some((i) => (i.severity || "") === "BLOCK");
    }
    function anyReview(items) {
      return items.some((i) => ["REVIEW", "BLOCK"].includes(i.severity || ""));
    }
    return {
      schema_version: "1.0",
      steps: [
        { step: 1, key: "input_validation", name: "输入校验",
          status: status(anyBlock(ive), anyReview(ive), ive.length === 0),
          summary: "Schema、类型、枚举、单位、drug_id↔drug_name 校验",
          details: ive },
        { step: 2, key: "normalize", name: "标准化", status: "passed",
          summary: "药物 / 食物 / 运动名称标准化；单位换算",
          details: {} },
        { step: 3, key: "drug_context", name: "构建 DrugContext",
          status: "passed", summary: "current → recommended → resulting",
          details: r.developer_diagnostics?.drug_context || {} },
        { step: 4, key: "text_parsing", name: "文本解析（辅助）",
          status: "passed", summary: "仅用于一致性检查与遗漏检测",
          details: {} },
        { step: 5, key: "required_context", name: "必要信息检查",
          status: status(false, missing.length > 0, missing.length === 0),
          summary: "通过精确 per-channel 索引查询所需字段",
          details: { missing_context_fields: missing } },
        { step: 6, key: "consistency", name: "一致性检查",
          status: status(anyBlock(cons), anyReview(cons), cons.length === 0),
          summary: "SYS001..SYS008 + 文本与结构化冲突",
          details: cons },
        { step: 7, key: "candidate_recall", name: "候选规则召回",
          status: "passed", summary: "8 个精确 per-channel 索引",
          details: {
            channels: r.retrieval_channels || [],
            candidate_rule_ids: r.candidate_rule_ids || [],
          } },
        { step: 8, key: "evaluation", name: "规则执行",
          status: status(anyBlock(meds), anyReview(meds), meds.length === 0),
          summary: "deterministic per-rule evaluation",
          details: { violations: meds } },
        { step: 9, key: "aggregate", name: "结果汇总",
          status: { BLOCK: "blocked", REVIEW: "warning" }[decision] || "passed",
          summary: "BLOCK > REVIEW > PASS",
          details: {
            decision,
            decision_basis: r.decision_basis || [],
            counts: { ive: ive.length, missing: missing.length,
                      cons: cons.length, meds: meds.length },
          } },
      ],
    };
  }

  function renderStepCard(step) {
    const status = step.status || "pending";
    const summary = step.summary || "";
    const details = step.details;
    const card = el("div", {
      class: "step-card",
      data: { status, open: "false", step: step.step },
      onclick: () => toggleStep(card),
    },
      el("div", { class: "step-head" },
        el("span", { class: "step-num" }, String(step.step || "")),
        el("span", { class: "step-name" }, step.name || step.key || "—"),
        el("span", {
          class: "step-status-pill",
          data: { status },
        }, statusLabel(status)),
      ),
      el("div", { class: "step-summary" }, summary),
      el("div", { class: "step-body" }),
    );
    const body = card.querySelector(".step-body");
    body.appendChild(renderStepBody(step.key || "", details));
    return card;
  }

  function toggleStep(card) {
    const open = card.dataset.open === "true";
    card.dataset.open = open ? "false" : "true";
  }

  function statusLabel(s) {
    switch (s) {
      case "passed":   return "PASS";
      case "warning":  return "REVIEW";
      case "blocked":  return "BLOCK";
      case "error":    return "ERROR";
      case "running":  return "运行中…";
      default:         return "待执行";
    }
  }

  function renderStepBody(key, details) {
    const root = document.createDocumentFragment();
    if (key === "input_validation") {
      if (!details || details.length === 0) {
        root.appendChild(el("div", { class: "empty" }, "所有 Schema、类型、枚举、单位、ID 校验通过"));
        return root;
      }
      details.forEach((iv) => {
        const card = el("div", { class: "card" },
          el("div", { class: "card-row" },
            el("span", { class: "card-label" }, iv.code || "—"),
            el("span", { class: `badge badge-${(iv.severity||"review").toLowerCase()}` },
              iv.severity || "REVIEW"),
          ),
          el("div", null, iv.message || ""),
          el("div", { class: "kv-grid" },
            el("dt", null, "字段路径"), el("dd", null, iv.field_path || "—"),
          ),
        );
        root.appendChild(card);
      });
      return root;
    }
    if (key === "normalize") {
      if (!details) {
        root.appendChild(el("div", { class: "empty" }, "(无标准化细节)"));
        return root;
      }
      const medActions = details.medication_actions || [];
      const foods = details.food_advice || [];
      const exes = details.exercise_advice || [];
      const cares = details.care_actions || [];
      const summary = el("div", { class: "card" },
        el("div", { class: "kv-grid" },
          el("dt", null, "药物动作"), el("dd", null, `${medActions.length} 条`),
          el("dt", null, "饮食建议"), el("dd", null, `${foods.length} 条`),
          el("dt", null, "运动建议"), el("dd", null, `${exes.length} 条`),
          el("dt", null, "就医建议"), el("dd", null, `${cares.length} 条`),
        ),
      );
      root.appendChild(summary);
      return root;
    }
    if (key === "drug_context") {
      const dc = details || {};
      const grid = el("div", { class: "dc-grid" },
        dcCol("当前用药 current_drugs", dc.current_drugs),
        dcCol("建议操作 recommended", dc.recommended_drugs),
        dcCol("执行后 resulting_drugs", dc.resulting_drugs),
      );
      root.appendChild(grid);
      if ((dc.text_mentioned_drugs || []).length > 0
          || (dc.text_dose_drugs || []).length > 0) {
        root.appendChild(el("div", { class: "meta-line" },
          "文本识别药物：",
          (dc.text_mentioned_drugs || []).join("、") || "—",
          " · 文本带剂量药物：",
          (dc.text_dose_drugs || []).join("、") || "—"));
      }
      return root;
    }
    if (key === "text_parsing") {
      const ex = (details && details.text_extractions) || [];
      root.appendChild(el("div", { class: "meta-line" },
        "识别到文本剂量 ", `${ex.length} 条；`,
        "仅用于一致性检查，不作规则判断依据"));
      ex.slice(0, 6).forEach((t) => {
        root.appendChild(el("div", { class: "card" },
          el("dl", { class: "kv-grid" },
            el("dt", null, "药物"), el("dd", null, t.drug || "—"),
            el("dt", null, "剂量"), el("dd", null,
              `${t.dose_value ?? "—"} ${t.dose_unit ?? ""}`.trim()),
            el("dt", null, "置信度"), el("dd", null, t.confidence || "—"),
          ),
        ));
      });
      return root;
    }
    if (key === "required_context") {
      const miss = details?.missing_context_fields || [];
      if (miss.length === 0) {
        root.appendChild(el("div", { class: "empty" },
          "本次所需患者字段全部存在且已确认"));
      } else {
        miss.forEach((m) => {
          root.appendChild(el("div", { class: "card" },
            el("div", { class: "card-row" },
              el("span", { class: "card-label" }, m.field_path || "—"),
              el("span", { class: "badge badge-warn" }, m.severity || "REVIEW"),
            ),
            el("div", { class: "meta-line" }, m.reason || ""),
            el("div", { class: "meta-line" },
              `关联规则：${(m.related_rule_ids || []).join("、") || "—"}`),
          ));
        });
      }
      const trace = details?.retrieval_trace || [];
      if (trace.length > 0) {
        root.appendChild(el("div", { class: "meta-line" },
          `required-context 精确召回 ${trace.length} 条通道 · ` +
          `规则总数 ${details?.total_rules_in_repo ?? "?"} · ` +
          `实际查询 ${details?.total_rules_consulted ?? "?"} 条`,
        ));
      }
      return root;
    }
    if (key === "consistency") {
      if (!details || details.length === 0) {
        root.appendChild(el("div", { class: "empty" },
          "正文与结构化动作一致"));
        return root;
      }
      details.forEach((cv) => {
        root.appendChild(el("div", { class: "card" },
          el("div", { class: "card-row" },
            el("span", { class: "card-label" }, cv.code || "—"),
            el("span", { class: `badge badge-${(cv.severity||"review").toLowerCase()}` },
              cv.severity || "REVIEW"),
          ),
          el("div", null, cv.message || ""),
        ));
      });
      return root;
    }
    if (key === "candidate_recall") {
      const channels = details?.channels || [];
      const rids = details?.candidate_rule_ids || [];
      const trace = details?.trace || [];
      const chanRow = el("div", { class: "channel-row" });
      channels.forEach((c) => chanRow.appendChild(
        el("span", { class: "channel-pill" }, c)));
      root.appendChild(el("div", { class: "meta-line" }, "召回通道："));
      root.appendChild(chanRow);
      root.appendChild(el("div", { class: "meta-line" },
        `共召回候选规则 ${rids.length} 条：`));
      const cand = el("div", { class: "candidate-rules" });
      rids.forEach((rid) => {
        cand.appendChild(el("div", { class: "candidate-rule" },
          el("span", { class: "rid" }, rid),
          el("span", null, ""),
        ));
      });
      root.appendChild(cand);
      if (trace.length > 0) {
        root.appendChild(el("div", { class: "meta-line" }, "通道 trace："));
        trace.slice(0, 8).forEach((t) => {
          root.appendChild(el("div", { class: "card" },
            el("dl", { class: "kv-grid" },
              el("dt", null, "channel"), el("dd", null, t.channel || "—"),
              el("dt", null, "key"), el("dd", null, (t.key || []).join(", ") || "—"),
              el("dt", null, "规则数"), el("dd", null, String((t.rule_ids || []).length)),
            ),
          ));
        });
      }
      return root;
    }
    if (key === "evaluation") {
      const evals = details?.evaluations || [];
      const matched = evals.filter((e) => e.matched);
      root.appendChild(el("div", { class: "meta-line" },
        `共评估 ${evals.length} 条规则 · 命中 ${matched.length} 条`));
      evals.forEach((e) => {
        const card = renderEvalCard(e);
        root.appendChild(card);
      });
      return root;
    }
    if (key === "aggregate") {
      const c = details?.counts || {};
      const list = el("dl", { class: "kv-grid" });
      list.appendChild(el("dt", null, "decision"),
        el("dd", null, details?.decision || "—"));
      list.appendChild(el("dt", null, "decision_basis"),
        el("dd", null, (details?.decision_basis || []).join("、") || "—"));
      list.appendChild(el("dt", null, "input_validation_errors"),
        el("dd", null, String(c.input_validation_errors || 0)));
      list.appendChild(el("dt", null, "missing_context_fields"),
        el("dd", null, String(c.missing_context_fields || 0)));
      list.appendChild(el("dt", null, "consistency_violations"),
        el("dd", null, String(c.consistency_violations || 0)));
      list.appendChild(el("dt", null, "medical_violations"),
        el("dd", null, String(c.medical_violations || 0)));
      root.appendChild(el("div", { class: "card" }, list));
      if (details?.patient_visible_response) {
        root.appendChild(el("div", { class: "meta-line" },
          "患者可见内容已生成（详见右侧）"));
      }
      return root;
    }
    root.appendChild(el("div", { class: "empty" }, "(无数据)"));
    return root;
  }

  function dcCol(title, items) {
    return el("div", { class: "dc-col" },
      el("h5", null, title),
      items && items.length
        ? el("ul", { class: "dc-list" },
            ...items.map((d) => el("li", null, d)))
        : el("div", { class: "dc-empty" }, "(无)"),
    );
  }

  function renderEvalCard(e) {
    const matched = e.matched;
    const sev = e.severity || (matched ? "REVIEW" : "—");
    const card = el("div", {
      class: "eval-card" + (matched ? " matched" : ""),
      data: { sev, open: matched ? "true" : "false" },
      onclick: () => {
        const open = card.dataset.open === "true";
        card.dataset.open = open ? "false" : "true";
        body.style.display = open ? "none" : "block";
      },
    },
      el("div", { class: "eval-head" },
        el("span", { class: "rid" }, e.rule_id || "—"),
        el("span", { class: "sev " + (sev || "REVIEW") }, sev),
      ),
    );
    const body = el("div", { class: "eval-conditions" });
    if (matched && e.conditions) {
      e.conditions.forEach((c) => {
        const cls = c.passed ? "cond-pass" : "cond-fail";
        body.appendChild(el("div", { class: "cond-row " + cls },
          el("span", null, c.passed ? "✓" : "✗"),
          el("span", null, c.description || ""),
        ));
      });
    }
    body.style.display = matched ? "block" : "none";
    card.appendChild(body);
    return card;
  }

  // ==============================================================
  // output panel
  // ==============================================================

  function renderDecisionPanel(payload) {
    const decision = payload.decision || "—";
    const sent = payload.original_llm_reply_was_sent === true;
    const r = payload.audit_report || {};

    setText(els.decisionText, decision);
    let cls = "decision-unknown";
    let rule = "等待审计结果…";
    if (decision === "BLOCK") { cls = "decision-block"; rule = "原始 LLM 回复已被拦截。"; }
    else if (decision === "REVIEW") { cls = "decision-review"; rule = "需要医生或药师人工复核。"; }
    else if (decision === "PASS") { cls = "decision-pass"; rule = "原始 LLM 回复已发出。"; }
    els.decisionCard.className = "decision-card " + cls;
    setText(els.decisionRule, rule);

    setText(els.visibleResponse, payload.patient_visible_response || "—");

    if (sent) {
      els.originalSent.textContent = "✅ 原始回复已发送（PASS）";
      els.originalSent.className = "big-flag flag-yes";
    } else {
      els.originalSent.textContent = "⛔ 原始回复已被拦截（" +
        (decision || "REVIEW") + "）";
      els.originalSent.className = "big-flag flag-no";
    }

    // decision_basis pills
    clearChildren(els.decisionBasis);
    (r.decision_basis || []).forEach((b) => {
      let c = "basis-pill";
      if (b.includes("BLOCK") || b === "MEDICAL_RULE") c += " block";
      else if (b === "MISSING_CONTEXT" || b === "TEXT_STRUCTURE_CONSISTENCY"
               || b === "INPUT_VALIDATION" || b === "LLM_DECLARED_UNCERTAINTY") c += " warn";
      else c += " primary";
      els.decisionBasis.appendChild(el("span", { class: c }, b));
    });
    if ((r.decision_basis || []).length === 0) {
      els.decisionBasis.appendChild(el("span", { class: "basis-pill" }, "(无)"));
    }

    // findings counts
    const counts = {
      input_validation: (r.input_validation_errors || []).length,
      missing_context: (r.missing_context_fields || []).length,
      consistency: (r.consistency_violations || []).length,
      medical: (r.medical_violations || []).length,
    };
    function fcItem(label, value, sev) {
      const card = el("div", {
        class: "fc-card" + (value > 0 ? " has" : " zero"),
        data: { sev: sev || (value > 0 ? "REVIEW" : "") },
      },
        el("div", { class: "fc-label" }, label),
        el("div", { class: "fc-value" }, String(value)),
      );
      return card;
    }
    clearChildren(els.findingsCounts);
    els.findingsCounts.appendChild(fcItem("输入校验", counts.input_validation));
    els.findingsCounts.appendChild(fcItem("缺失上下文", counts.missing_context));
    els.findingsCounts.appendChild(fcItem("一致性违规", counts.consistency));
    els.findingsCounts.appendChild(fcItem("医学命中",
      (r.medical_violations || []).filter((v) => v.severity === "BLOCK").length, "BLOCK"));
    els.findingsCounts.appendChild(fcItem("医学 REVIEW",
      (r.medical_violations || []).filter((v) => v.severity === "REVIEW").length));
    els.findingsCounts.appendChild(fcItem("系统异常",
      (r.system_findings || []).length));

    // timing
    renderTiming(r.timing_ms || {});
  }

  function renderTiming(t) {
    clearChildren(els.timingTable);
    const phases = [
      "input_validation", "normalization", "matching", "text_parsing",
      "risk_detection", "required_context", "consistency",
      "candidate_selection", "evaluation", "logging", "total",
    ];
    phases.forEach((p) => {
      const v = (t[p] ?? t[p + "_ms"] ?? 0);
      const tr = el("tr", null,
        el("td", { class: "k" }, p),
        el("td", { class: "v" }, `${Number(v).toFixed(3)} ms`),
      );
      els.timingTable.appendChild(tr);
    });
  }

  // ==============================================================
  // bottom panels
  // ==============================================================

  function renderRuleExample(payload) {
    clearChildren(els.bottomRuleExample);
    const r = payload.audit_report || {};
    const matched = (r.medical_violations || []).concat([]);
    let ruleJson = "(本次未命中医学规则)";
    let ruleLogic = "没有触发的医学规则，所以 PASS 通过。";
    if (matched.length > 0) {
      const top = matched[0];
      ruleJson = pretty(top, 2);
      ruleLogic = `命中规则 ${top.rule_id} · 等级 ${top.severity} · ${top.message}`;
    }
    const wrap = el("div", { class: "rule-example" },
      el("pre", { class: "rule-json" }, ruleJson),
      el("div", { class: "rule-logic" },
        ruleLogic,
        el("br"),
        el("br"),
        "triggers：决定规则是否被召回",
        el("br"),
        "conditions / parameters：决定规则是否命中",
      ),
    );
    els.bottomRuleExample.appendChild(wrap);
  }

  function renderDrugContextFlow(payload) {
    clearChildren(els.bottomDrugContext);
    const dc = (payload.audit_report?.developer_diagnostics?.drug_context)
      || uiTrace?.steps?.[2]?.details || {};
    const cur = dc.current_drugs || [];
    const rec = dc.recommended_drugs || [];
    const res = dc.resulting_drugs || [];
    const normText = (arr) => arr.length === 0 ? "(空)" : arr.join("、");
    const flow = el("div", { class: "dc-flow" },
      el("div", { class: "dc-step cur" },
        "current_drugs\n", el("br"), normText(cur)),
      el("span", { class: "dc-arrow" }, "→"),
      el("div", { class: "dc-step act" },
        "recommended\n", el("br"), normText(rec)),
      el("span", { class: "dc-arrow" }, "→"),
      el("div", { class: "dc-step res" },
        "resulting_drugs\n", el("br"), normText(res)),
    );
    els.bottomDrugContext.appendChild(flow);
    els.bottomDrugContext.appendChild(el("p", { class: "hint" },
      "current_drugs：当前用药。recommended：建议操作。resulting_drugs：" +
      "执行后用药（start/continue/increase/decrease/replace 进入；" +
      "stop/hold 移出；avoid_start 不变）。"));
  }

  function renderRuleTypesBottom(payload) {
    if (!ruleTypeCatalog || ruleTypeCatalog.length === 0) {
      clearChildren(els.bottomRuleTypes);
      els.bottomRuleTypes.appendChild(el("div", { class: "empty" }, "(加载中)"));
      return;
    }
    clearChildren(els.bottomRuleTypes);
    const evaluated = new Set(payload.audit_report?.evaluated_rule_ids || []);
    const matched = new Set((payload.audit_report?.medical_violations || [])
      .map((v) => v.rule_id));
    const ridsByType = (payload.ui_trace?.steps || []).find(
      (s) => s.key === "evaluation")?.details?.evaluations || [];
    const firedTypes = new Set(ridsByType
      .filter((e) => e.matched).map((e) => e.type));
    const wrap = el("div", { class: "rule-types" });
    ruleTypeCatalog.forEach((rt) => {
      const recalled = (payload.audit_report?.retrieval_channels || [])
        .some((c) => c.includes(rt.key));
      const fired = firedTypes.has(rt.key);
      wrap.appendChild(el("div", { class: "rt-row" },
        el("span", null,
          el("span", { class: "rt-name" }, rt.name_zh),
          el("span", { class: "rt-stats" }, ` (${rt.key})`),
        ),
        el("span", null,
          el("span", { class: "rt-stats" }, `${rt.active_count}/${rt.total_count} active`),
          el("span", { class: "rt-recall", data: { active: fired ? "true" : (recalled ? "true" : "false") } },
            fired ? " 命中" : (recalled ? " 召回" : " —")),
        ),
      ));
    });
    els.bottomRuleTypes.appendChild(wrap);
  }

  // ==============================================================
  // dev details (JSON)
  // ==============================================================

  function renderDevDetails(payload, rawInput) {
    const r = payload.audit_report || {};
    const norm = r.developer_diagnostics?.normalized_draft || {};
    setText(els.rawInput, pretty(rawInput, 2));
    setText(els.normalized, pretty(norm, 2));
    setText(els.rawAudit, pretty(r, 2));
  }

  // ==============================================================
  // wire up
  // ==============================================================

  function expandAllSteps(open) {
    document.querySelectorAll(".step-card").forEach((c) => {
      c.dataset.open = open ? "true" : "false";
    });
  }
  els.expandAll.addEventListener("click", () => expandAllSteps(true));
  els.collapseAll.addEventListener("click", () => expandAllSteps(false));

  // JSON dev panel buttons
  document.querySelectorAll("[data-copy]").forEach((btn) => {
    btn.addEventListener("click", () => {
      const id = btn.dataset.copy;
      const text = $(id)?.textContent || "";
      try {
        navigator.clipboard?.writeText(text);
        const orig = btn.textContent;
        btn.textContent = "已复制";
        setTimeout(() => { btn.textContent = orig; }, 1200);
      } catch (e) {
        // ignore
      }
    });
  });
  document.querySelectorAll("[data-format]").forEach((btn) => {
    btn.addEventListener("click", () => {
      const id = btn.dataset.format;
      const el = $(id);
      try {
        const obj = JSON.parse(el.textContent || "null");
        el.textContent = obj === null ? "(空)" : pretty(obj, 2);
      } catch (e) {
        btn.textContent = "非 JSON";
        setTimeout(() => { btn.textContent = "格式化"; }, 1200);
      }
    });
  });

  // ----- run audit -----

  function flashStepsRunning() {
    document.querySelectorAll(".step-card").forEach((c, i) => {
      c.dataset.open = "false";
      c.dataset.status = "running";
      c.querySelector(".step-status-pill").textContent = "运行中…";
      c.querySelector(".step-status-pill").dataset.status = "running";
    });
  }

  function settleStepStatuses() {
    if (!uiTrace || !uiTrace.steps) return;
    const cards = document.querySelectorAll(".step-card");
    uiTrace.steps.forEach((s, i) => {
      const card = cards[i];
      if (!card) return;
      card.dataset.status = s.status || "passed";
      const pill = card.querySelector(".step-status-pill");
      pill.dataset.status = s.status || "passed";
      pill.textContent = statusLabel(s.status || "passed");
      // Open the step if there is an issue.
      const hasIssue = ["blocked", "warning", "error"].includes(s.status);
      card.dataset.open = hasIssue ? "true" : "false";
    });
  }

  async function runAudit() {
    let patient_state, dialogue_output;
    try {
      patient_state = JSON.parse(els.patientTa.value || "{}");
    } catch (e) {
      setText(els.scenarioSummary, "patient_state JSON 解析失败: " + e.message);
      return;
    }
    try {
      dialogue_output = JSON.parse(els.dialogueTa.value || "{}");
    } catch (e) {
      setText(els.scenarioSummary, "dialogue_output JSON 解析失败: " + e.message);
      return;
    }

    setText(els.scenarioSummary, "运行中…");
    flashStepsRunning();

    let resp;
    try {
      resp = await fetch("/api/audit", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          schema_version: "1.0",
          patient_state,
          dialogue_output,
          strict_mode: els.strictToggle.checked,
          compat_mode: els.compatToggle.checked,
          simulate_error: els.simulateToggle.checked,
          debug: true,
        }),
      });
    } catch (e) {
      setText(els.scenarioSummary, "请求失败: " + e.message);
      return;
    }
    if (!resp.ok) {
      setText(els.scenarioSummary, "服务端错误 " + resp.status);
      return;
    }
    let data;
    try {
      data = await resp.json();
    } catch (e) {
      setText(els.scenarioSummary, "服务端返回非 JSON: " + e.message);
      return;
    }
    setText(els.scenarioSummary, currentScenario
      ? (currentScenario.summary || currentScenario.title || "")
      : "自定义输入完成");

    lastAudit = data;
    renderDecisionPanel(data);
    renderAuditSteps(data);
    settleStepStatuses();
    renderRuleExample(data);
    renderDrugContextFlow(data);
    renderRuleTypesBottom(data);
    renderDevDetails(data, {
      schema_version: "1.0",
      patient_state,
      dialogue_output,
    });
  }

  els.runBtn.addEventListener("click", runAudit);
  els.resetBtn.addEventListener("click", () => {
    els.patientTa.value = "";
    els.dialogueTa.value = "";
    setText(els.scenarioSummary, "已清空");
  });

  // ==============================================================
  // boot
  // ==============================================================

  (async () => {
    await checkHealth();
    await loadRuleTypeCatalog();
    await loadScenarios();
    // URL param ?scenario=ID can pre-select and auto-run.
    const params = new URLSearchParams(location.search);
    const wanted = params.get("scenario");
    let chosen = null;
    if (wanted) {
      chosen = findScenario(wanted);
    }
    if (!chosen && scenarios.length > 0 && scenarios[0].items?.length > 0) {
      chosen = scenarios[0].items[0];
    }
    if (chosen) {
      els.scenarioSelect.value = chosen.id;
      els.scenarioSelect.dispatchEvent(new Event("change"));
      // Render placeholder empty steps first.
      renderAuditSteps({ audit_report: {} });
      // If ?scenario=ID was explicit, auto-run.
      if (wanted) {
        // Wait a tick for the form to settle, then audit.
        setTimeout(() => { els.runBtn.click(); }, 80);
      }
    } else {
      renderAuditSteps({ audit_report: {} });
    }
  })();
})();
