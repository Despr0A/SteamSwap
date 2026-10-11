let paidPurchasesLoading = false;

async function refreshTransactions() {
  const body = document.querySelector("#paid-purchases-table tbody");
  if (!body || paidPurchasesLoading) return;
  paidPurchasesLoading = true;
  try {
    const data = await fetchJson(API + "/purchases");
    const number = (value, digits) => value == null ? "--" : Number(value).toFixed(digits);
    body.innerHTML = (data.purchases || []).map((purchase) => {
      const date = new Date(Number(purchase.at) * 1000).toLocaleString();
      return `<tr><td>${escapeHtml(purchase.name || "")}</td><td>${number(purchase.price, 2)}</td><td>${number(purchase.market_price, 2)}</td><td>${escapeHtml(date)}</td><td>${number(purchase.purchase_ratio, 4)}</td></tr>`;
    }).join("") || '<tr><td colspan="5" class="text-muted">暂无已支付饰品</td></tr>';
  } catch (error) {
    toast("加载已支付饰品失败", error.message || "");
  } finally {
    paidPurchasesLoading = false;
  }
}
