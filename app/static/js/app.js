/**
 * StarPost Manager Frontend Client Script
 */

document.addEventListener("DOMContentLoaded", () => {
    // Listen for HTMX custom events
    document.body.addEventListener("showMessage", (evt) => {
        const detail = evt.detail;
        if (typeof detail === "string") {
            window.showToast(detail, "info");
        } else if (detail && detail.message) {
            window.showToast(detail.message, detail.type || "info");
        }
    });

    // Handle HTMX response errors
    document.body.addEventListener("htmx:responseError", (evt) => {
        const xhr = evt.detail.xhr;
        let msg = "An error occurred while processing request";
        try {
            const parsed = JSON.parse(xhr.responseText);
            if (parsed.detail) msg = parsed.detail;
        } catch (e) {
            if (xhr.statusText) msg = `${xhr.status}: ${xhr.statusText}`;
        }
        window.showToast(msg, "error");
    });
});

/**
 * Global Toast System
 */
window.showToast = function (message, type = "info") {
    const container = document.getElementById("toast-container");
    if (!container) return;

    const toast = document.createElement("div");
    toast.className = `flex items-center gap-3 px-4 py-3 rounded-xl shadow-2xl backdrop-blur-md border text-sm text-white transform transition-all duration-300 translate-y-2 opacity-0 z-50 max-w-md ${
        type === "success"
            ? "bg-emerald-950/90 border-emerald-500/50 text-emerald-200"
            : type === "error"
            ? "bg-rose-950/90 border-rose-500/50 text-rose-200"
            : type === "warning"
            ? "bg-amber-950/90 border-amber-500/50 text-amber-200"
            : "bg-slate-900/90 border-blue-500/50 text-blue-200"
    }`;

    const icon = type === "success" ? "✓" : type === "error" ? "✕" : type === "warning" ? "!" : "ℹ";
    toast.innerHTML = `
        <span class="font-bold text-base">${icon}</span>
        <span class="flex-1">${message}</span>
        <button class="opacity-60 hover:opacity-100 font-bold ml-2" onclick="this.parentElement.remove()">×</button>
    `;

    container.appendChild(toast);

    // Animate in
    setTimeout(() => {
        toast.classList.remove("translate-y-2", "opacity-0");
    }, 10);

    // Auto remove after 4.5s
    setTimeout(() => {
        toast.classList.add("opacity-0", "translate-y-2");
        setTimeout(() => toast.remove(), 300);
    }, 4500);
};

/**
 * Format HTML string for Telegram Preview simulation
 */
window.formatTelegramHtml = function (rawHtml) {
    if (!rawHtml) return "<span class='text-dim italic'>No caption provided</span>";
    // Replace tg-spoiler or simulate spoiler clicks
    let formatted = rawHtml
        .replace(/<b>(.*?)<\/b>/gi, "<strong>$1</strong>")
        .replace(/<i>(.*?)<\/i>/gi, "<em>$1</em>")
        .replace(/<s>(.*?)<\/s>/gi, "<del>$1</del>")
        .replace(/<u>(.*?)<\/u>/gi, "<u>$1</u>")
        .replace(/<code>(.*?)<\/code>/gi, "<code class='bg-black/40 px-1 py-0.5 rounded text-pink-400 font-mono text-xs'>$1</code>")
        .replace(/<tg-spoiler>(.*?)<\/tg-spoiler>/gi, "<span class='tg-spoiler' onclick='this.classList.toggle(\"revealed\")'>$1</span>")
        .replace(/\n/g, "<br>");
    return formatted;
};

/**
 * Insert tags into textarea for Telegram HTML formatting toolbar
 */
window.insertTag = function (textareaId, openTag, closeTag) {
    const el = document.getElementById(textareaId);
    if (!el) return;

    const start = el.selectionStart;
    const end = el.selectionEnd;
    const text = el.value;
    const selected = text.substring(start, end);

    const replacement = `${openTag}${selected}${closeTag}`;
    el.value = text.substring(0, start) + replacement + text.substring(end);
    el.focus();
    el.selectionStart = start + openTag.length;
    el.selectionEnd = end + openTag.length;

    // Trigger input event so Alpine / Live preview updates
    el.dispatchEvent(new Event("input", { bubbles: true }));
};
