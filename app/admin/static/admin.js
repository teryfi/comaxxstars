(() => {
  const copyButtons = document.querySelectorAll("[data-copy-order]");
  copyButtons.forEach((button) => {
    button.addEventListener("click", async () => {
      const value = button.dataset.copyOrder || "";
      try {
        await navigator.clipboard.writeText(value);
        const original = button.textContent;
        button.textContent = "Скопировано";
        window.setTimeout(() => { button.textContent = original; }, 1400);
      } catch {
        button.textContent = "Выделите номер вручную";
      }
    });
  });

  const starPriceCard = document.querySelector("[data-star-price-card]");
  if (!starPriceCard) {
    return;
  }
  const main = starPriceCard.querySelector("[data-star-price-main]");
  const meta = starPriceCard.querySelector("[data-star-price-meta]");
  const commission = starPriceCard.querySelector("[data-star-price-commission]");
  const updated = starPriceCard.querySelector("[data-star-price-updated]");
  const mode = starPriceCard.querySelector(".star-price-mode");

  const formatTime = (value) => {
    if (!value) {
      return "обновляется автоматически";
    }
    const date = new Date(value);
    if (Number.isNaN(date.getTime())) {
      return String(value);
    }
    return `обновлено ${date.toLocaleTimeString("ru-RU", {
      hour: "2-digit",
      minute: "2-digit",
      second: "2-digit",
    })}`;
  };

  const setStarPrice = (payload) => {
    starPriceCard.classList.toggle("ok", Boolean(payload.ok));
    starPriceCard.classList.toggle("error", !payload.ok);
    if (!payload.ok) {
      if (main) main.textContent = "Нет данных";
      if (meta) meta.textContent = payload.error || "Не удалось получить цену Fragment";
      if (commission) commission.textContent = "";
      if (updated) updated.textContent = formatTime(payload.checked_at);
      if (mode) mode.textContent = "KYC";
      return;
    }
    if (main) main.textContent = `${Number(payload.rub_per_star).toFixed(4)} ₽`;
    if (meta) {
      meta.innerHTML = "";
      [`${payload.amount} ${payload.currency}`, payload.payment_method].forEach((value) => {
        const item = document.createElement("span");
        item.textContent = value;
        meta.appendChild(item);
      });
    }
    if (commission) {
      const commissionValue = Number(payload.commission_percent);
      commission.textContent = commissionValue === 0
        ? "KYC: комиссия API 0%"
        : `Комиссия API не включена: ${payload.commission_percent}%`;
    }
    if (updated) updated.textContent = formatTime(payload.checked_at);
    if (mode) mode.textContent = String(payload.api_mode || "kyc").toUpperCase();
  };

  const refreshStarPrice = async () => {
    try {
      const response = await fetch("/admin/api/star-price", {
        headers: { "Accept": "application/json" },
        cache: "no-store",
      });
      if (!response.ok) {
        throw new Error(`HTTP ${response.status}`);
      }
      setStarPrice(await response.json());
    } catch {
      setStarPrice({
        ok: false,
        error: "Не удалось обновить цену Fragment",
        checked_at: new Date().toISOString(),
      });
    }
  };

  window.setInterval(refreshStarPrice, 30000);
  window.setTimeout(refreshStarPrice, 1500);
})();
