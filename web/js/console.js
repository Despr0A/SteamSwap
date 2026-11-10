
let logLines = [];
let debugPaused = false;
let autoScroll = (localStorage.getItem('autoScroll') || '1') === '1';
const reportLabels = {
  item: "饰品", goods_id: "BUFF 商品编号", market_hash_name: "Steam 市场名",
  report_id: "本次检查编号", request_id: "请求编号", flow_id: "流水线编号",
  pass: "通过", reject: "拒绝", wait: "暂缓", observe: "仅观测", success: "读取成功",
  failed: "读取失败", ready: "已就绪", fresh: "有效", stale: "已过期或时间无效", error: "异常",
  history_data: "历史数据", reference_freshness: "卖单快照时效", reference_price: "参考价计算",
  max_discount: "成本率检查", reference_stability: "参考价稳定性", sell_pressure: "卖压检查",
  stability_analysis: "历史稳定性分析", buff_realtime_price: "BUFF 实时价格复核",
  custom_buy_modules: "自定义策略", candidate: "候选结果", observation: "观测结果",
  history_unavailable: "历史数据不可用", reference_price_unstable: "参考价尚未稳定",
  steam_sell_orders_unavailable: "Steam 卖单不可用", stability_analysis_invalid: "历史分析无效",
  all_checks_passed: "全部候选检查通过", observation_only: "已记录参考价",
  source: "数据来源", memory_cache: "内存缓存", database_cache: "本地数据库", refresh: "刷新数据库",
  history_adjusted: "卖单参考价经有效历史数据校正", unknown: "未提供", local: "本地检查",
  network_path: "网络查询流程（可能回退缓存）", fresh_local: "本地参考价",
  fetched_at: "原始采集时间", age_seconds: "数据年龄（秒）", ttl_seconds: "有效期（秒）",
  history_points: "可用历史点数", plan_price: "BUFF 候选价（元）",
  original_reference_price: "校正前参考价（元）", reference_price_value: "使用的参考价（元）",
  estimated_ratio: "成本率", limit: "阈值", fee_factor: "手续费系数", comparison: "通过条件",
  purpose: "读取用途", candidate_checks: "参考价及稳定性检查", network_attempted: "已调用网络适配器",
  attempts: "尝试次数", http_status: "HTTP 状态", elapsed_ms: "耗时（毫秒）", endpoint: "接口",
  method: "方法", proxy: "网络出口", account: "账号", reference_fresh: "卖单快照有效",
  sell_orders_count: "卖单档数", operation_invoked: "已调用读取服务", message: "原因",
  analysis_status: "分析状态", "steam.price_history": "Steam 历史成交", "steam.sell_orders": "Steam 卖单",
  "buff.sell_orders": "BUFF 卖单", reason: "判定原因", decision: "结果", operation: "读取操作",
  action_orderbook: "Steam 订单簿", ssr_orderbook: "Steam 市场页面", histogram: "Steam 直方图（可含缓存）",
  diagnostic: "请求诊断", level: "级别", summary: "摘要",
};
function _reportLabel(value) { return reportLabels[value] || value; }
function _logEscape(value) {
  return String(value ?? "").replace(/&/g, "&amp;").replace(/</g, "&lt;")
    .replace(/>/g, "&gt;").replace(/"/g, "&quot;").replace(/'/g, "&#39;");
}
function _levelClass(level) {
  return ({ error: "log-line-error", warn: "log-line-warn", debug: "log-line-debug" })[level] || "log-line-info";
}
function _fmtTime(t) {
  return t == null ? "" : new Date(t * 1000).toTimeString().slice(0, 8);
}
function _reportLayer(x) {
  if (x.report?.layer) return x.report.layer;
  if (x.category === "item_report") return "item";
  if (x.category === "network_report" || x.diagnostic?.endpoint ||
      ["network", "proxy", "steamdt", "exchange_rate"].includes(x.category)) return "network";
  return "system";
}
function _filteredLogLines() {
  const q = (el("log-search")?.value || "").trim().toLowerCase();
  const lv = el("log-level")?.value || "all";
  const layer = el("log-layer")?.value || "item";
  const outcome = el("log-outcome")?.value || "all";
  return logLines.filter((x) => {
    if (layer !== "all" && _reportLayer(x) !== layer) return false;
    if (lv !== "all" && (x.level || "info") !== lv) return false;
    if (outcome !== "all" && (x.report?.decision || x.report?.status) !== outcome) return false;
    return !q || JSON.stringify(x).toLowerCase().includes(q);
  });
}
function _reportFields(data) {
  return Object.entries(data).filter(([key]) => !["check", "status"].includes(key)).map(([key, value]) => {
    let text;
    if (value == null) text = "未提供";
    else if (key === "fetched_at") text = new Date(value * 1000).toLocaleString();
    else if (typeof value === "boolean") text = value ? "是" : "否";
    else if (typeof value === "number") text = Number.isInteger(value) ? String(value) : value.toFixed(4);
    else if (typeof value === "object") text = JSON.stringify(value, null, 2);
    else text = _reportLabel(value);
    const label = key === "reference_price" ? reportLabels.reference_price_value : _reportLabel(key);
    return `<dt>${_logEscape(label)}</dt><dd>${_logEscape(text)}</dd>`;
  }).join("");
}
function _lineToHtml(x) {
  const cls = _levelClass(x.level || "info");
  const report = x.report;
  const text = `${_fmtTime(x.t)} [${x.level || "info"}] ${x.msg || ""}`;
  if (!report && !x.diagnostic) return `<div class="${cls}">${_logEscape(text)}</div>`;
  const title = report ? `${_fmtTime(x.t)} · ${report.item || report.operation || "报告"} · ${_reportLabel(report.decision || report.status)}` : text;
  const checks = (report?.checks || []).map(check =>
    `<section><strong>${_logEscape(_reportLabel(check.check))} · ${_logEscape(_reportLabel(check.status))}</strong><dl>${_reportFields(check)}</dl></section>`).join("");
  const formula = (report?.checks || []).find(check => check.check === "reference_price");
  const formulaText = formula ? `<p>成本率 = BUFF 价格 ÷ Steam 参考价 × ${_logEscape(formula.fee_factor)}；通过条件：成本率 &lt; ${_logEscape(formula.limit ?? "未启用")}</p>` : "";
  const fields = Object.fromEntries(Object.entries(report || {}).filter(([key]) => !["checks", "details", "layer"].includes(key)));
  const notes = (report?.details || []).map(note => `<div>${_logEscape(note.message)}</div>`).join("");
  const networkButton = report?.layer === "item" && report.report_id ?
    `<button type="button" class="btn btn-sm btn-secondary" data-report-network="${_logEscape(report.report_id)}">查看本次网络报告</button>` : "";
  const diagnostic = x.diagnostic ? `<pre>${_logEscape(JSON.stringify(x.diagnostic, null, 2))}</pre>` : "";
  return `<details class="log-report ${cls}" data-log-id="${_logEscape(x.id)}"><summary>${_logEscape(title)}</summary><div class="log-report-content"><dl>${_reportFields(fields)}</dl>${formulaText}${checks}${notes}${diagnostic}${networkButton}</div></details>`;
}
function renderLogFull() {
  const out = el("log-output");
  if (!out) return;
  const expanded = new Set([...out.querySelectorAll("details[open]")].map(node => node.dataset.logId));
  const scrollTop = out.scrollTop;
  const filtered = _filteredLogLines();
  out.innerHTML = filtered.map(_lineToHtml).join("");
  out.querySelectorAll("details").forEach(node => { node.open = expanded.has(node.dataset.logId); });
  out.scrollTop = autoScroll ? out.scrollHeight : scrollTop;
  _updateBadge();
}
function showReportNetwork(reportId) {
  el("log-layer").value = "network";
  el("log-outcome").value = "all";
  el("log-level").value = "all";
  el("log-search").value = reportId;
  renderLogFull();
}
async function refreshLog() {
  if (debugPaused) return;
  const out = el("log-output");
  if (!out) return;
  const since = Number(out.dataset.lastIndex || 0);
  try {
    const d = await fetchJson(API + "/log?since=" + since);
    const lines = d.lines || [];
    if (!lines.length) return;
    logLines.push(...lines);
    out.dataset.lastIndex = String(Math.max(...lines.map(l => l.id || 0)));
    renderLogFull();
  } catch {
  }
}
async function clearLog() {
  try {
    await fetch(API + "/log/clear", { method: "POST" });
    logLines = [];
    const out = el("log-output");
    if (out) {
      out.innerHTML = "";
      out.dataset.lastIndex = "0";
    }
    _updateBadge();
    toast("日志已清空");
  } catch (e) {
    toast("清空失败", e.message || "请稍后再试");
  }
}
function _updatePauseBtn() {
  const b = el("btn-toggle-pause");
  if (!b) return;
  const svgHtml = '<svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5"><rect x="6" y="4" width="4" height="16"/><rect x="14" y="4" width="4" height="16"/></svg>';
  const playHtml = '<svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5"><polygon points="5 3 19 12 5 21 5 3"/></svg>';
  b.innerHTML = `${debugPaused ? playHtml : svgHtml} ${debugPaused ? '继续' : '暂停'}`;
}
function _updateScrollBtn() {
  const b = el("btn-toggle-scroll");
  if (!b) return;
  const svgHtml = '<svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5"><polyline points="17 11 12 6 7 11"/><polyline points="17 18 12 13 7 18"/></svg>';
  b.innerHTML = `${svgHtml} 自动滚动：${autoScroll ? '开' : '关'}`;
}
function _updateBadge() {
  const badge = el("log-count-badge");
  if (badge) badge.textContent = `${_filteredLogLines().length} / ${logLines.length} 条`;
}
function togglePause() {
  debugPaused = !debugPaused;
  _updatePauseBtn();
  toast(debugPaused ? "已暂停刷新" : "已继续刷新");
}
function toggleAutoScroll() {
  autoScroll = !autoScroll;
  localStorage.setItem("autoScroll", autoScroll ? "1" : "0");
  _updateScrollBtn();
  toast("自动滚动", autoScroll ? "开启" : "关闭");
}
function downloadLog() {
  function fmtTime(t) {
    if (t == null) return "";
    const d = new Date(t * 1000);
    return d.toTimeString().slice(0, 8);
  }
  const blob = new Blob([
    logLines.map((x) => `${fmtTime(x.t)} [${x.level || "info"}] ${x.msg || ""}` +
      (x.category ? ` [category=${x.category}, flow_id=${x.flow_id || ""}]` : "") +
      (x.report ? `\n  report=${JSON.stringify(x.report)}` : "") +
      (x.diagnostic ? `\n  diagnostic=${JSON.stringify(x.diagnostic)}` : ""))
      .join("\n") + "\n",
  ], { type: "text/plain;charset=utf-8" });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = `log_${new Date().toISOString().replace(/[:.]/g, "-").slice(0, 19)}.txt`;
  document.body.appendChild(a);
  a.click();
  a.remove();
  URL.revokeObjectURL(url);
  toast("已下载日志");
}
async function exportLog() {
  try {
    const r = await fetch(API + "/log/export", { method: "POST" });
    const d = await r.json();
    if (d.ok) {
      toast("日志已导出", `已保存 ${d.lines} 行 → ${d.path}`);
    } else {
      toast("导出失败", d.error || "请稍后再试");
    }
  } catch (e) {
    toast("导出失败", e.message || "请稍后再试");
  }
}
