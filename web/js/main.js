
function formatTimeHHMM(d = new Date()) {
  const hh = String(d.getHours()).padStart(2, "0");
  const mm = String(d.getMinutes()).padStart(2, "0");
  return `${hh}:${mm}`;
}
async function tabSwitch(name) {
  console.log("tabSwitch called with name:", name);
  const activePanel = document.querySelector(".panel.active");
  if (activePanel?.id === "panel-strategies" && name !== "strategies" && typeof window.strategyConfirmLeave === "function") {
    const ok = await window.strategyConfirmLeave();
    if (!ok) return false;
  }
  document.querySelectorAll(".panel").forEach((p) => p.classList.remove("active"));
  document.querySelectorAll(".nav-item").forEach((b) => {
    b.classList.remove("active");
    b.removeAttribute("aria-current");
  });
  const panel = el("panel-" + name);
  const btn = document.querySelector(`.nav-menu [data-tab="${name}"]`);
  console.log("tabSwitch found panel:", panel, "btn:", btn);
  if (panel) {
    panel.classList.add("active");
    panel.style.animation = 'none';
    panel.offsetHeight;
    panel.style.animation = '';
  }
  if (btn) {
    btn.classList.add("active");
    btn.setAttribute("aria-current", "page");
    const text = btn.querySelector("span")?.innerText;
    if (text && el("page-title-display")) el("page-title-display").innerText = text;
  }
  const descriptions = {
    auto: "从配置到执行，在一个工作区掌握交易流程。",
    purchases: "查看已支付饰品的购入价格、时间与购入时比例。",
    accounts: "集中管理 Steam 账号、登录验证与账号凭据。",
    "steam-guard": "查看与复制当前账号的动态安全令牌。",
    proxy: "配置出口节点、路由策略与网络连通性检测。",
    strategies: "组合交易模块，模拟策略，并确认启用变更。",
    settings: "配置数据源、交易参数与通知服务，保存后生效。",
    debug: "从饰品报告追溯筛选结果与关联网络请求。",
  };
  if (el("page-description")) el("page-description").textContent = descriptions[name] || "";
  if (name === "debug") refreshLog();
  if (name === "purchases") refreshTransactions();
  if (name === "accounts") refreshAccounts();
  if (name === "steam-guard") initSteamGuardPanel();
  if (name !== "steam-guard") stopSteamGuardTimer();
  if (name === "proxy") {
    loadProxyConfig();
  }
  if (name === "strategies" && typeof loadStrategies === "function") {
    loadStrategies();
  }
  return true;
}

let lastStatus = "idle";
let _lastLyricText = "";
// 是否已配置账号的缓存标志（避免 popup 在未配置时误弹出）
let _hasAnyAccount = false;

function pushLyricLine(text, subText) {
  if (text === _lastLyricText) return;
  _lastLyricText = text;
  const track = el("step-scroll-track");
  if (!track) return;
  const LINE_H = 24;
  const MAX_LINES = 10;
  const oldSub = track.querySelector(".lyric-sub");
  if (oldSub) oldSub.remove();
  const oldLines = track.querySelectorAll(".lyric-line");
  oldLines.forEach((l, i) => {
    const dist = oldLines.length - i;
    let op;
    if (dist === 1) op = 0.35;
    else if (dist === 2) op = 0.15;
    else op = 0.05;
    l.style.transition = "opacity 0.6s ease";
    l.style.opacity = String(op);
  });
  const line = document.createElement("div");
  line.className = "lyric-line";
  line.textContent = text;
  line.style.opacity = "0";
  line.style.transition = "none";
  track.appendChild(line);
  if (subText) {
    const sub = document.createElement("div");
    sub.className = "lyric-line lyric-sub";
    sub.textContent = subText;
    sub.style.opacity = "0";
    sub.style.transition = "none";
    track.appendChild(sub);
  }
  track.style.transition = "none";
  track.style.transform = `translateY(${LINE_H}px)`;
  void track.offsetHeight;
  track.style.transition = "";
  track.style.transform = "translateY(0)";
  requestAnimationFrame(() => {
    line.style.transition = "opacity 0.45s cubic-bezier(0.22, 1, 0.36, 1) 0.12s";
    line.style.opacity = "1";
    const newSub = track.querySelector(".lyric-sub");
    if (newSub) {
      newSub.style.transition = "opacity 0.45s cubic-bezier(0.22, 1, 0.36, 1) 0.2s";
      newSub.style.opacity = "0.3";
    }
  });
  while (track.querySelectorAll(".lyric-line:not(.lyric-sub)").length > MAX_LINES) {
    track.removeChild(track.firstChild);
  }
  const mainCount = track.querySelectorAll(".lyric-line:not(.lyric-sub)").length;
  if (mainCount > 1) {
    const vp = el("step-scroll-viewport");
    if (vp) vp.style.height = subText ? "72px" : "48px";
  }
}
async function refreshStatus() {
  try {
    const d = await fetchJson(API + "/status");
    if (d.buff_verification_required && _hasAnyAccount) {
      showReloginModal("buff", { reason: "verification_required", error: d.buff_verification_reason });
    } else if (d.buff_auth_expired && _hasAnyAccount) {
      showReloginModal("buff");
    }
    const raw = d.status || "idle";
    const statusText = raw === "running" ? "运行中" : raw === "error" ? "错误" : raw === "stopped" ? "已停止" : "空闲中";
    const top = el("status-text");
    if (top) top.textContent = statusText;
    const inline = el("status-text-inline");
    if (inline) inline.textContent = statusText;
    const stepDesc = d.step || "";
    const item = d.progress_item || "";
    const newText = stepDesc && item ? `${stepDesc}：${item}` : (stepDesc || item || "—");
    const nextItem = d.next_progress_item || "";
    const subText = stepDesc && nextItem ? `${stepDesc}：${nextItem}` : nextItem;
    pushLyricLine(newText, subText);
    const pill = el("status-pill");
    if (pill) {
      pill.classList.remove("status-idle", "status-running", "status-stopped", "status-error");
      pill.classList.add(raw === "running" ? "status-running" : raw === "error" ? "status-error" : raw === "stopped" ? "status-stopped" : "status-idle");
    }
    const controlBar = document.querySelector(".control-bar");
    if (controlBar) {
      if (raw === "running") controlBar.classList.add("running");
      else controlBar.classList.remove("running");
    }
    const lu = el("last-updated");
    if (lu) lu.textContent = formatTimeHHMM();
    if (raw !== lastStatus) {
      if (raw === "running") toast("已开始运行");
      if (raw === "stopped") toast("已停止");
      if (raw === "error") toast("发生错误", d.step || "请看调试日志");
      lastStatus = raw;
    }
  } catch {
  }
  try {
    const d = await fetchJson(API + "/pending_payment");
    const p = d.pending;
    const box = el("pending-payment");
    if (!box) return;
    if (p && p.pay_url) {
      box.classList.remove("hidden");
      const link = el("pay-link");
      if (link) {
        link.href = p.pay_url;
        link.textContent = p.pay_type === "wechat" ? "微信支付链接（可复制到浏览器）" : "打开支付链接";
      }
      const t = el("pay-type");
      if (t) t.textContent = p.name ? "订单: " + p.name : "";
      box.dataset.payUrl = p.pay_url;
      const qrWrap = el("pay-qrcode-wrap");
      const qrBox = el("pay-qrcode");
      if (p.pay_type === "wechat" && qrWrap && qrBox && typeof QRCode !== "undefined") {
        qrWrap.classList.remove("hidden");
        qrBox.innerHTML = "";
        try {
          new QRCode(qrBox, { text: p.pay_url, width: 200, height: 200 });
        } catch (e) {
          qrWrap.classList.add("hidden");
        }
      } else {
        if (qrWrap) qrWrap.classList.add("hidden");
        if (qrBox) qrBox.innerHTML = "";
      }
    } else {
      box.classList.add("hidden");
      box.dataset.payUrl = "";
      const qrWrap = el("pay-qrcode-wrap");
      const qrBox = el("pay-qrcode");
      if (qrWrap) qrWrap.classList.add("hidden");
      if (qrBox) qrBox.innerHTML = "";
    }
  } catch {
  }

}
let reloginType = "steam";
function showReloginModal(type, opts = {}) {
  reloginType = type || "steam";
  const overlay = el("relogin-overlay");
  if (overlay) overlay.classList.remove("hidden");
  const title = el("relogin-title");
  const msg = el("relogin-message");
  if (reloginType === "buff") {
    if (opts.reason === "verification_required") {
      if (title) title.textContent = "Buff 需要验证";
      if (msg) msg.textContent = opts.error
        ? `Buff 返回：${compactErrorText(opts.error, 180)}。请打开内置浏览器，进入 Buff 市场或任意商品页，完成刷新/人机验证后点击完成。`
        : "Buff 需要刷新页面状态或完成人机验证。请打开内置浏览器，进入 Buff 市场或任意商品页，完成验证后点击完成。";
    } else {
      if (title) title.textContent = "Buff 登录已过期";
      if (msg) msg.textContent = "登录已过期，请按当前运行环境打开浏览器登录 Buff，或手动填写 Cookie 后继续。";
    }
  } else {
    if (title) title.textContent = "Steam 登录已过期";
    if (opts.reason === "need_2fa") {
      if (msg) msg.textContent = "需要二次验证（验证码），请点击下方按钮打开浏览器并完成 Steam 登录。";
    } else if (opts.error) {
      if (msg) msg.textContent = compactErrorText(opts.error, 220);
    } else {
      if (msg) msg.textContent = "登录已过期，请按当前运行环境打开浏览器登录 Steam，或手动填写 Cookie 后继续。";
    }
  }
  const btnOk = el("relogin-btn-ok");
  if (btnOk) btnOk.disabled = false;
  if (typeof runtimeCanLaunchBrowser === "function" && !runtimeCanLaunchBrowser()) {
    if (msg && typeof runtimeManualLoginMessage === "function") msg.textContent = runtimeManualLoginMessage(reloginType);
    if (btnOk) btnOk.disabled = true;
  }
  if (typeof applyRuntimeUiHints === "function") applyRuntimeUiHints();
}
function hideReloginModal() {
  const overlay = el("relogin-overlay");
  if (overlay) overlay.classList.add("hidden");
}
async function copyPayLink() {
  const box = el("pending-payment");
  const url = box?.dataset.payUrl;
  if (!url) return;
  try {
    await navigator.clipboard.writeText(url);
    toast("已复制链接");
  } catch {
    const ta = document.createElement("textarea");
    ta.value = url;
    document.body.appendChild(ta);
    ta.select();
    document.execCommand("copy");
    ta.remove();
    toast("已复制链接");
  }
}
function bindEvents() {
  document.querySelectorAll(".nav-item").forEach((btn) => {
    btn.title = btn.querySelector("span")?.textContent || "";
    btn.addEventListener("click", () => tabSwitch(btn.dataset.tab));
  });
  document.querySelectorAll("[data-open-panel]").forEach((btn) => {
    btn.addEventListener("click", async () => {
      if (await tabSwitch(btn.dataset.openPanel)) {
        const heading = el("page-title-display");
        heading?.setAttribute("tabindex", "-1");
        heading?.focus({ preventScroll: true });
      }
    });
  });
  document.querySelectorAll(".proxy-strategy-card, #steam-token-circle").forEach((control) => {
    control.setAttribute("role", "button");
    control.tabIndex = 0;
    control.addEventListener("keydown", (event) => {
      if (event.key === "Enter" || event.key === " ") {
        event.preventDefault();
        control.click();
      }
    });
  });
  el("btn-quit")?.addEventListener("click", async () => {
    if (await appConfirm("确定要退出程序吗？退出后后台所有的交易、保活和查询任务都将停止。", { title: "退出程序", danger: true, confirmText: "退出" })) {
      fetchJson(API + "/system/shutdown", { method: "POST" })
        .then(() => {
          toast("程序正在退出", "您可以安全地直接关闭此窗口", 100000);
          setTimeout(() => window.close(), 1000);
        })
        .catch(() => {
          toast("程序已退出", "您可以安全地直接关闭此窗口", 100000);
        });
    }
  });
  el("btn-start")?.addEventListener("click", () => startPipeline());
  el("btn-observe")?.addEventListener("click", () => startPipeline(true));
  el("btn-stop")?.addEventListener("click", stopPipeline);
  el("btn-paid")?.addEventListener("click", () => confirmPayment(true));
  el("btn-fail")?.addEventListener("click", () => confirmPayment(false));
  el("btn-copy-pay")?.addEventListener("click", copyPayLink);
  el("btn-add-account")?.addEventListener("click", () => openAccountForm());
  el("account-form-cancel")?.addEventListener("click", closeAccountForm);
  el("account-form-save")?.addEventListener("click", saveAccountForm);
  el("accounts-search")?.addEventListener("input", (e) => {
    accountsSearchTerm = e.target.value || "";
    renderAccountsUI(accountsCache, accountsCurrentId);
  });
  el("btn-save-config")?.addEventListener("click", () =>
    saveConfigFromForm()
      .then(() => toast("设置已保存"))
      .catch((e) => toast("保存失败", e.message || "请稍后再试"))
  );
  el("btn-refresh-paid")?.addEventListener("click", refreshTransactions);
  el("btn-clear-log")?.addEventListener("click", clearLog);
  el("btn-toggle-pause")?.addEventListener("click", togglePause);
  el("btn-toggle-scroll")?.addEventListener("click", toggleAutoScroll);
  el("btn-download-log")?.addEventListener("click", downloadLog);
  el("btn-export-log")?.addEventListener("click", exportLog);
  el("log-search")?.addEventListener("input", () => renderLogFull());
  el("log-level")?.addEventListener("change", () => renderLogFull());
  el("log-layer")?.addEventListener("change", () => renderLogFull());
  el("log-outcome")?.addEventListener("change", () => renderLogFull());
  el("log-output")?.addEventListener("click", (event) => {
    const button = event.target.closest("[data-report-network]");
    if (button) showReportNetwork(button.dataset.reportNetwork);
  });
  el("cfg-verbose-debug")?.addEventListener("change", async () => {
    try {
      const result = await fetchJson(API + "/config", {
        method: "POST",
        body: JSON.stringify({
          config: {
            pipeline: {
              verbose_debug: el("cfg-verbose-debug").checked,
            },
          },
        }),
      });
      if (!result.ok) throw new Error(result.error || "配置写入失败");
      toast("已保存", "详细调试 " + (el("cfg-verbose-debug").checked ? "已开启，下次运行生效" : "已关闭"));
    } catch (e) {
      toast("保存失败", e.message || "");
    }
  });
  el("cfg-steam-listings-debug")?.addEventListener("change", async () => {
    try {
      const result = await fetchJson(API + "/config", {
        method: "POST",
        body: JSON.stringify({
          config: {
            pipeline: {
              steam_listings_debug: el("cfg-steam-listings-debug").checked,
            },
          },
        }),
      });
      if (!result.ok) throw new Error(result.error || "配置写入失败");
      toast("已保存", "Steam 在售/历史调试 " + (el("cfg-steam-listings-debug").checked ? "已开启" : "已关闭"));
    } catch (e) {
      toast("保存失败", e.message || "");
    }
  });
  el("btn-export-config")?.addEventListener("click", exportConfig);
  el("btn-data-init")?.addEventListener("click", async () => {
    if (!(await appConfirm("警告：此操作将清空所有数据（包括交易记录、自动挂刀配置、绑定的 Steam 账号与凭据）！\n\n您确定要进行“恢复出厂设置”吗？", { title: "恢复出厂设置", danger: true, confirmText: "继续" }))) {
      return;
    }
    if (!(await appConfirm("再次确认：数据一旦清空将无法恢复。是否继续？", { title: "最终确认", danger: true, confirmText: "清空数据" }))) {
      return;
    }
    try {
      await fetchJson(API + "/data/init", { method: "POST" });
      await refreshStatus();
      toast("数据已清空，即将刷新页面");
      setTimeout(() => location.reload(), 1500);
    } catch (e) {
      toast("初始化失败", e.message || "");
    }
  });
  el("cfg-import-file")?.addEventListener("change", (e) => importConfigFromFile(e.target.files?.[0]));
  el("relogin-btn-open")?.addEventListener("click", openBrowserAndLogin);
  el("relogin-btn-ok")?.addEventListener("click", () => finishRelogin(true));
  el("relogin-btn-fail")?.addEventListener("click", () => finishRelogin(false));
  el("btn-theme")?.addEventListener("click", () => Theme.cycle());
  el("steam-token-circle")?.addEventListener("click", handleSteamGuardClick);
}
function setupScrollToTop() {
  const btn = el("scroll-top-btn");
  if (!btn) return;
  const panels = document.querySelectorAll(".panel");
  const check = () => {
    const active = document.querySelector(".panel.active");
    if (active && active.scrollTop > 200) btn.classList.add("visible");
    else btn.classList.remove("visible");
  };
  panels.forEach(p => p.addEventListener("scroll", check, { passive: true }));
  btn.addEventListener("click", () => {
    const active = document.querySelector(".panel.active");
    if (active) active.scrollTo({ top: 0, behavior: "smooth" });
  });
}
function setupKeyboardShortcuts() {
  const tabMap = { "1": "auto", "2": "purchases", "7": "accounts", "8": "steam-guard", "9": "settings", "0": "debug" };
  document.addEventListener("keydown", async (e) => {
    if (e.target.tagName === "INPUT" || e.target.tagName === "TEXTAREA" || e.target.tagName === "SELECT" || e.target.isContentEditable) return;
    if (e.altKey && tabMap[e.key]) {
      e.preventDefault();
      await tabSwitch(tabMap[e.key]);
    }
  });
}
async function init() {
  Theme.apply(Theme.get());
  if (window.matchMedia) {
    const mm = window.matchMedia("(prefers-color-scheme: dark)");
    mm.addEventListener?.("change", () => {
      if (Theme.get() === "system") Theme.apply("system");
    });
  }
  if (typeof _updateScrollBtn === "function") _updateScrollBtn();
  bindEvents();
  setupScrollToTop();
  setupKeyboardShortcuts();
  setupButtonInteractions();
  bindUXEvents();
  if (typeof loadRuntimeProfile === "function") {
    await loadRuntimeProfile();
  }

  let wizardShown = false;
  try {
    // 提前执行新手引导检查，避免被后续可能超时的库存请求阻塞
    wizardShown = await checkAndShowOnboardingWizard();
  } catch (e) {
    console.warn("Failed to check onboarding wizard:", e);
  }

  try {
    await loadConfig();
    await loadProxyConfig();
  } catch (e) {
    toast("加载配置失败", e.message || "请检查后端是否可用");
  }

  await refreshTransactions();
  await refreshStatus();
  setInterval(refreshStatus, 2000);
  setInterval(() => { if (document.querySelector("#panel-purchases.active")) refreshTransactions(); }, 5000);
  setInterval(() => {
    if (document.querySelector("#panel-debug.active")) refreshLog();
  }, 1500);
}
function setupButtonInteractions() {
  document.addEventListener('mousemove', (e) => {
    const btn = e.target.closest('.btn');
    if (btn) {
      const rect = btn.getBoundingClientRect();
      const x = ((e.clientX - rect.left) / rect.width * 100).toFixed(0);
      const y = ((e.clientY - rect.top) / rect.height * 100).toFixed(0);
      btn.style.setProperty('--ripple-x', x + '%');
      btn.style.setProperty('--ripple-y', y + '%');
    }
  });
}
init();
