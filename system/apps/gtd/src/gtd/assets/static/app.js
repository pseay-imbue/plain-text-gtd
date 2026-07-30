// Toast helper + clipboard copy for the GTD UI.

(function () {
    // Robust focus for the /all Search input. The HTML `autofocus`
    // attribute alone isn't enough: the desktop client loads gtd via the
    // system_interface service-worker bootstrap, which triggers a
    // location.reload() after SW install. Some webviews suppress
    // autofocus on programmatic reloads (and on bfcache restores).
    // Explicit focus on pageshow handles both cases.
    function _focusSearchIfPresent() {
        const el = document.querySelector('input.filter-bar__search');
        if (el && document.activeElement !== el) {
            el.focus();
        }
    }
    window.addEventListener('pageshow', _focusSearchIfPresent);

    let toastEl = null;
    let toastTimer = null;

    function ensureToast() {
        if (toastEl) return toastEl;
        toastEl = document.createElement("div");
        toastEl.className = "toast";
        toastEl.setAttribute("role", "status");
        toastEl.setAttribute("aria-live", "polite");
        document.body.appendChild(toastEl);
        return toastEl;
    }

    function hideToast() {
        if (!toastEl) return;
        toastEl.classList.remove("is-show", "toast--action");
    }

    // showToast(message) shows a plain transient toast. Pass an options object
    // to add an action button (e.g. Undo): { action: {label, onAction},
    // duration, onExpire }. The action button is clickable (the plain toast is
    // pointer-events:none); clicking it runs onAction and dismisses the toast.
    function showToast(message, options) {
        const el = ensureToast();
        if (toastTimer) {
            clearTimeout(toastTimer);
            toastTimer = null;
        }
        el.classList.remove("toast--action");
        el.textContent = "";
        const msg = document.createElement("span");
        msg.className = "toast__msg";
        msg.textContent = message;
        el.appendChild(msg);

        const action = options && options.action;
        if (action && action.label) {
            el.classList.add("toast--action");
            const btn = document.createElement("button");
            btn.type = "button";
            btn.className = "toast__action";
            btn.textContent = action.label;
            btn.addEventListener("click", () => {
                if (toastTimer) {
                    clearTimeout(toastTimer);
                    toastTimer = null;
                }
                hideToast();
                try {
                    action.onAction();
                } catch (err) {
                    console.error("toast action threw:", err);
                }
            });
            el.appendChild(btn);
        }

        el.classList.add("is-show");
        const duration = (options && options.duration) || 1800;
        toastTimer = setTimeout(() => {
            toastTimer = null;
            hideToast();
            if (options && options.onExpire) {
                try {
                    options.onExpire();
                } catch (err) {
                    console.error("toast onExpire threw:", err);
                }
            }
        }, duration);
    }

    async function copyText(text) {
        if (navigator.clipboard && window.isSecureContext) {
            try {
                await navigator.clipboard.writeText(text);
                return;
            } catch (err) {
                // Clipboard API can fail in iframed / cross-origin contexts
                // (e.g. served through workspace_server). Fall through to the
                // execCommand fallback, which works without permissions.
            }
        }
        const ta = document.createElement("textarea");
        ta.value = text;
        ta.setAttribute("readonly", "");
        ta.style.position = "fixed";
        ta.style.left = "-9999px";
        ta.style.opacity = "0";
        document.body.appendChild(ta);
        ta.select();
        ta.setSelectionRange(0, text.length);
        const ok = document.execCommand("copy");
        document.body.removeChild(ta);
        if (!ok) {
            throw new Error("Copy fallback failed");
        }
    }

    async function copyFromUrl(url, label) {
        try {
            const res = await fetch(url, { headers: { Accept: "text/plain" } });
            if (!res.ok) throw new Error("Request failed: " + res.status);
            const text = await res.text();
            await copyText(text);
            showToast(label + " copied");
        } catch (err) {
            console.error(err);
            showToast("Copy failed");
        }
    }

    document.addEventListener("click", (event) => {
        const trigger = event.target.closest("[data-copy-url]");
        if (!trigger) return;
        event.preventDefault();
        const url = trigger.getAttribute("data-copy-url");
        const label = trigger.getAttribute("data-copy-label") || "Snippet";
        copyFromUrl(url, label);
    });

    // Inline rename: click a .item__title with data-rename-url to edit.
    document.addEventListener("click", (event) => {
        const el = event.target.closest(".item__title[data-rename-url]");
        if (!el || el.classList.contains("is-editing")) return;
        enterRename(el);
    });

    function enterRename(el) {
        const original = el.textContent.trim();
        el.dataset.original = original;
        el.classList.add("is-editing");
        el.contentEditable = "true";
        el.spellcheck = true;
        el.focus();
        const range = document.createRange();
        range.selectNodeContents(el);
        const sel = window.getSelection();
        sel.removeAllRanges();
        sel.addRange(range);
        el.addEventListener("keydown", onRenameKey);
        el.addEventListener("blur", finishRename, { once: true });
    }

    function onRenameKey(event) {
        if (event.key === "Enter") {
            event.preventDefault();
            event.target.blur();
        } else if (event.key === "Escape") {
            event.preventDefault();
            event.target.textContent = event.target.dataset.original;
            event.target.blur();
        }
    }

    async function finishRename(event) {
        const el = event.target;
        el.removeEventListener("keydown", onRenameKey);
        el.classList.remove("is-editing");
        el.contentEditable = "false";
        const newVal = el.textContent.replace(/\s+/g, " ").trim();
        const original = el.dataset.original;
        if (!newVal || newVal === original) {
            el.textContent = original;
            return;
        }
        el.textContent = newVal;
        try {
            const formData = new FormData();
            formData.append("title", newVal);
            const res = await fetch(el.dataset.renameUrl, { method: "POST", body: formData });
            if (!res.ok) throw new Error("rename failed: " + res.status);
            el.dataset.original = newVal;
            showToast("Renamed");
        } catch (err) {
            console.error(err);
            el.textContent = original;
            showToast("Rename failed");
        }
    }

    // Inline body edit on the project detail page: click the rendered body
    // to swap it for a textarea pre-filled with the raw markdown. Blur saves
    // (matching the title-rename pattern); Escape cancels.
    document.addEventListener("click", (event) => {
        const view = event.target.closest("[data-body-view]");
        if (!view) return;
        if (event.target.closest("a")) return;
        const wrap = view.closest("[data-body-edit]");
        if (!wrap || wrap.classList.contains("is-editing")) return;
        enterBodyEdit(wrap);
    });

    function enterBodyEdit(wrap) {
        const view = wrap.querySelector("[data-body-view]");
        const ta = wrap.querySelector("[data-body-raw]");
        wrap.classList.add("is-editing");
        view.hidden = true;
        ta.hidden = false;
        ta.dataset.original = ta.value;
        autosizeTextarea(ta);
        ta.focus();
        ta.setSelectionRange(ta.value.length, ta.value.length);
        ta.addEventListener("keydown", onBodyKey);
        ta.addEventListener("input", () => autosizeTextarea(ta));
        ta.addEventListener("blur", onBodyBlur, { once: true });
    }

    function autosizeTextarea(ta) {
        ta.style.height = "auto";
        ta.style.height = ta.scrollHeight + 2 + "px";
    }

    function onBodyKey(event) {
        if (event.key === "Escape") {
            event.preventDefault();
            const ta = event.target;
            ta.value = ta.dataset.original;
            ta.removeEventListener("keydown", onBodyKey);
            ta.removeEventListener("blur", onBodyBlur);
            exitBodyEdit(ta.closest("[data-body-edit]"));
        }
    }

    async function onBodyBlur(event) {
        const ta = event.target;
        ta.removeEventListener("keydown", onBodyKey);
        const wrap = ta.closest("[data-body-edit]");
        await saveBody(wrap);
    }

    async function saveBody(wrap) {
        const ta = wrap.querySelector("[data-body-raw]");
        const view = wrap.querySelector("[data-body-view]");
        if (ta.value === ta.dataset.original) {
            exitBodyEdit(wrap);
            return;
        }
        try {
            const formData = new FormData();
            formData.append("body", ta.value);
            const res = await fetch(wrap.dataset.bodyEditUrl, { method: "POST", body: formData });
            if (!res.ok) throw new Error("body save failed: " + res.status);
            const html = await res.text();
            view.innerHTML = html || "<em>Click to add project notes…</em>";
            view.classList.toggle("project-shell__body--empty", !ta.value.trim());
            ta.dataset.original = ta.value;
            showToast("Saved");
            exitBodyEdit(wrap);
        } catch (err) {
            console.error(err);
            showToast("Save failed");
            // Stay in edit mode so the user can retry.
            ta.addEventListener("blur", onBodyBlur, { once: true });
        }
    }

    function exitBodyEdit(wrap) {
        const ta = wrap.querySelector("[data-body-raw]");
        const view = wrap.querySelector("[data-body-view]");
        wrap.classList.remove("is-editing");
        ta.hidden = true;
        view.hidden = false;
    }

    async function postFormAsync(form, extraFields) {
        const formData = new FormData(form);
        if (extraFields) {
            for (const [k, v] of Object.entries(extraFields)) {
                formData.set(k, v);
            }
        }
        const res = await fetch(form.action, {
            method: (form.method || "POST").toUpperCase(),
            body: formData,
            redirect: "follow",
        });
        if (!res.ok && res.status !== 0) {
            throw new Error("save failed: " + res.status);
        }
        return res;
    }

    function inferFieldFromAction(action) {
        try {
            const path = new URL(action, window.location.href).pathname;
            if (path.endsWith("/status")) return "status";
            if (path.endsWith("/priority")) return "priority";
            if (path.endsWith("/parent")) return "project";
            if (path.endsWith("/due-date")) return "due date";
            if (path.endsWith("/start-date")) return "start date";
            if (path.endsWith("/title")) return "title";
            if (path.endsWith("/check")) return null;
        } catch (e) {}
        return null;
    }

    function inferItemTitle(form) {
        const item = form.closest(".item");
        if (!item) return null;
        const titleEl = item.querySelector(".item__title");
        if (!titleEl) return null;
        const raw = (titleEl.textContent || "").trim();
        if (!raw) return null;
        return raw.length > 42 ? raw.slice(0, 40) + "…" : raw;
    }

    // Map view name (matches the `active` context var) to the status value
    // it filters by. Views not in this map don't auto-hide rows.
    const VIEW_STATUS_FILTER = {
        inbox: "inbox",
        next: "next",
        waiting: "waiting",
        tickler: "tickler",
        someday: "someday",
        read: "read-review",
        reference: "reference",
    };

    function fadeOutRow(row, options) {
        row.classList.add("is-leaving");
        requestAnimationFrame(() => {
            requestAnimationFrame(() => row.classList.add("is-collapsed"));
        });
        // Default matches the 1s total duration in style.css (.item.is-leaving).
        // A longer delay keeps the collapsed row in the DOM so an Undo action
        // can bring it back in place before it's removed.
        const delay = (options && options.delay) || 1050;
        if (row._leaveTimer) clearTimeout(row._leaveTimer);
        row._leaveTimer = setTimeout(() => {
            row._leaveTimer = null;
            row.remove();
        }, delay);
    }

    // Reverse a pending fadeOutRow: stop the scheduled removal and restore the
    // row to full height.
    function cancelFadeOut(row) {
        if (row._leaveTimer) {
            clearTimeout(row._leaveTimer);
            row._leaveTimer = null;
        }
        row.classList.remove("is-leaving", "is-collapsed");
    }

    // How long the "marked done" toast (and the offer to Undo it) stays up.
    const DONE_UNDO_DURATION_MS = 6000;

    // Show the "marked done" confirmation toast with an Undo button that
    // restores the item to the bucket it was in before (read from the row's
    // data-status). Recurring items don't actually go "done" — checking them
    // bounces them to their next occurrence server-side — so they get the plain
    // toast with no Undo.
    function offerDoneUndo(li, checkbox, form, title) {
        const message = buildToastMessage({ title, action: "checked-on" });
        const isChild = li && li.classList.contains("item--child");
        const recurs = li && li.dataset.recur === "1";
        if (!li || recurs) {
            showToast(message);
            if (li && !isChild) fadeOutRow(li);
            return;
        }
        // data-status still holds the pre-done bucket (the change handler never
        // rewrites it to "done"), so it's our restore target.
        const prevStatus = li.dataset.status || "inbox";
        // Keep the collapsing row in the DOM for the toast's lifetime so Undo
        // can bring it back in place; it's removed when the toast expires.
        if (!isChild) fadeOutRow(li, { delay: DONE_UNDO_DURATION_MS });
        showToast(message, {
            duration: DONE_UNDO_DURATION_MS,
            action: {
                label: "Undo",
                onAction: () => undoDone(li, checkbox, form, prevStatus, isChild),
            },
        });
    }

    async function undoDone(li, checkbox, form, prevStatus, isChild) {
        if (!isChild) cancelFadeOut(li);
        checkbox.checked = false;
        li.classList.remove("item--done");
        // The checkbox posts to .../check; the status endpoint sits next to it.
        const statusUrl = form.action.replace(/\/check(\?|#|$)/, "/status$1");
        try {
            const formData = new FormData();
            formData.append("status", prevStatus);
            const res = await fetch(statusUrl, { method: "POST", body: formData });
            if (!res.ok && res.status !== 0) throw new Error("undo failed: " + res.status);
            showToast("Restored");
        } catch (err) {
            console.error("undo failed:", err);
            showToast("Undo failed");
        }
    }

    const PRIORITY_ORDER = { high: 0, normal: 1, low: 2 };

    function applyPriorityClass(row, newPriority) {
        row.classList.remove("item--high", "item--low");
        if (newPriority === "high") row.classList.add("item--high");
        else if (newPriority === "low") row.classList.add("item--low");
    }

    function resortByPriority(row) {
        const ul = row.parentElement;
        if (!ul) return;
        // Only sort within the row's own group (e.g. children of the same
        // project, or the loose-tasks section) — never pull a row out of
        // its visual cluster.
        const group = row.dataset.group;
        if (!group) return;
        const members = Array.from(ul.children).filter(
            (el) => el.matches(".item") && el.dataset.group === group
        );
        if (members.length < 2) return;
        const sorted = [...members].sort((a, b) => {
            const pa = PRIORITY_ORDER[a.dataset.priority] ?? 1;
            const pb = PRIORITY_ORDER[b.dataset.priority] ?? 1;
            return pa - pb;
        });
        // Anchor: the element immediately before the first group member.
        // Insert each sorted row right after the previous one, preserving
        // surrounding non-member rows (like project headers).
        let cursor = members[0].previousElementSibling;
        sorted.forEach((s) => {
            if (cursor) {
                ul.insertBefore(s, cursor.nextSibling);
            } else {
                ul.insertBefore(s, ul.firstChild);
            }
            cursor = s;
        });
    }

    function maybeRemoveFromView(form, newStatus) {
        const viewActive = document.body.getAttribute("data-view-active") || "";
        const expected = VIEW_STATUS_FILTER[viewActive];
        if (!expected) return; // not a status-filtered view; leave the row alone
        if (newStatus === expected) return;
        // Don't fade rows shown as project children — they're nested under the
        // project header and we want the user to see the consequence in place.
        const row = form.closest(".item");
        if (!row) return;
        if (row.classList.contains("item--child")) return;
        fadeOutRow(row);
    }

    function buildToastMessage({ title, field, value, action }) {
        // Special-case the checkbox (mark done / restore).
        if (action === "checked-on") {
            return title ? `${title} → done` : "Marked done";
        }
        if (action === "checked-off") {
            return title ? `${title} → back to Inbox` : "Moved back to Inbox";
        }
        if (!field) return title ? `${title} saved` : "Saved";
        const shownValue = value || "—";
        return title ? `${title} → ${field}: ${shownValue}` : `${field}: ${shownValue}`;
    }

    // Async submit on `change` of a select/checkbox/date input inside a form
    // marked data-inline-submit (the row checkbox + date inputs use this).
    document.addEventListener("change", async (event) => {
        const control = event.target;
        const form = control.closest("form[data-inline-submit]");
        if (!form) return;
        if (control.tagName !== "SELECT" && control.tagName !== "INPUT") return;

        control.classList.add("is-saving");
        let saved = false;
        try {
            await postFormAsync(form);
            saved = true;
        } catch (err) {
            console.error("inline save failed:", err);
            showToast("Save failed");
            return;
        } finally {
            control.classList.remove("is-saving");
        }
        if (!saved) return;

        try {
            const title = inferItemTitle(form);
            if (control.type === "checkbox") {
                const li = control.closest(".item");
                if (li) li.classList.toggle("item--done", control.checked);
                if (control.checked) {
                    offerDoneUndo(li, control, form, title);
                } else {
                    showToast(buildToastMessage({ title, action: "checked-off" }));
                }
            } else {
                const field = inferFieldFromAction(form.action);
                const value = control.value || "(cleared)";
                showToast(buildToastMessage({ title, field, value }));
            }
        } catch (err) {
            console.error("post-save UI update threw:", err);
            showToast("Saved");
        }
    });

    // Two-click-to-confirm guard for any form marked data-row-delete. First
    // click flips the button into a "confirm?" state; second click within
    // 2 seconds submits.
    document.addEventListener("click", (event) => {
        const btn = event.target.closest("[data-row-delete] button[type='submit']");
        if (!btn) return;
        const form = btn.closest("form[data-row-delete]");
        if (!form) return;
        if (form.classList.contains("is-confirming")) return;  // allow the submit
        event.preventDefault();
        form.classList.add("is-confirming");
        btn.setAttribute("data-tooltip", "Click again to confirm");
        const timeout = setTimeout(() => {
            form.classList.remove("is-confirming");
            btn.setAttribute(
                "data-tooltip",
                form.dataset.originalTooltip || "Delete (click twice to confirm)"
            );
        }, 2000);
        form.dataset._resetTimeout = String(timeout);
    });

    // Async submit on `submit` of an inline form (custom picker uses button
    // clicks inside a form for each option).
    document.addEventListener("submit", async (event) => {
        const form = event.target.closest("form[data-inline-submit]");
        if (!form) return;
        event.preventDefault();
        const cdrop = form.closest("[data-cdrop]");
        if (cdrop) cdrop.classList.add("is-saving");

        const submitter = event.submitter;
        const extras = submitter && submitter.name
            ? { [submitter.name]: submitter.value }
            : undefined;

        let saved = false;
        try {
            await postFormAsync(form, extras);
            saved = true;
        } catch (err) {
            console.error("picker save failed:", err);
            showToast("Save failed");
            return;
        } finally {
            if (cdrop) cdrop.classList.remove("is-saving");
        }
        if (!saved) return;

        try {
            if (cdrop && submitter) {
                const summary = cdrop.querySelector(".cdrop__summary");
                const newLabel = (submitter.textContent || "").trim() || submitter.value || "";
                if (summary) summary.textContent = newLabel;
                cdrop.querySelectorAll(".cdrop__option").forEach((b) => {
                    b.classList.toggle("is-selected", b === submitter);
                });
                cdrop.open = false;
            }
        } catch (err) {
            console.error("post-save UI update threw:", err);
        }

        try {
            const title = inferItemTitle(form);
            const row = form.closest(".item");
            // Row-delete forms have their own UX: fade the row out.
            if (form.hasAttribute("data-row-delete")) {
                showToast(title ? `Deleted “${title}”` : "Deleted");
                if (row) fadeOutRow(row);
                return;
            }
            const field = inferFieldFromAction(form.action);
            const value = submitter
                ? ((submitter.textContent || "").trim() || submitter.value || "—")
                : "";
            showToast(buildToastMessage({ title, field, value }));
            if (field === "status" && submitter) {
                if (row) row.dataset.status = submitter.value;
                maybeRemoveFromView(form, submitter.value);
            } else if (field === "priority" && submitter && row) {
                row.dataset.priority = submitter.value;
                applyPriorityClass(row, submitter.value);
                resortByPriority(row);
            }
        } catch (err) {
            console.error("toast build threw:", err);
            showToast("Saved");
        }
    });

    // Row picker panels are built lazily on first open, from the shared
    // #gtd-picker-data blob, rather than inlined in every row (which bloated
    // the DOM). Once built, the existing submit/close handlers take over.
    let _pickerData = null;
    function pickerData() {
        if (_pickerData === null) {
            const el = document.getElementById("gtd-picker-data");
            try {
                _pickerData = el ? JSON.parse(el.textContent) : {};
            } catch (err) {
                console.error("bad gtd-picker-data:", err);
                _pickerData = {};
            }
        }
        return _pickerData;
    }

    function buildPickerPanel(details) {
        if (details.querySelector(".cdrop__panel")) return; // already built
        const data = pickerData();
        const kind = details.dataset.picker;
        const name = details.dataset.name;
        const current = details.dataset.current || "";
        let options;
        if (kind === "parent") {
            options = [{ value: "", label: "no project" }];
            (data.projects || []).forEach((p) => {
                if (p.id !== details.dataset.exclude) {
                    options.push({ value: p.id, label: p.title });
                }
            });
        } else {
            options = (data[name] || []).map((v) => ({ value: v, label: v }));
        }
        const form = document.createElement("form");
        form.className = "cdrop__panel";
        form.method = "post";
        form.action = details.dataset.action;
        form.setAttribute("data-inline-submit", "");
        options.forEach((o) => {
            const btn = document.createElement("button");
            btn.type = "submit";
            btn.name = name;
            btn.value = o.value;
            btn.className = "cdrop__option" + (o.value === current ? " is-selected" : "");
            btn.textContent = o.label;
            form.appendChild(btn);
        });
        details.appendChild(form);
    }

    // `toggle` doesn't bubble, so listen in the capture phase.
    document.addEventListener(
        "toggle",
        (event) => {
            const details = event.target;
            if (
                details instanceof HTMLDetailsElement &&
                details.hasAttribute("data-cdrop") &&
                details.open
            ) {
                buildPickerPanel(details);
            }
        },
        true,
    );

    // Click outside an open custom dropdown -> close it.
    document.addEventListener("click", (event) => {
        const opened = document.querySelectorAll("[data-cdrop][open]");
        opened.forEach((d) => {
            if (!d.contains(event.target)) d.open = false;
        });
    });

    // Weekday picker: toggle the `is-on` class on change AND auto-save
    // the new selection via the picker's dedicated endpoint. Saves
    // immediately so the user doesn't need to remember to hit Save.
    document.addEventListener("change", async (event) => {
        const input = event.target;
        if (!(input instanceof HTMLInputElement)) return;
        if (input.name !== "recur_weekdays") return;
        const label = input.closest(".weekday-picker__day");
        if (label) label.classList.toggle("is-on", input.checked);

        const picker = input.closest(".weekday-picker[data-recur-autosave-url]");
        if (!picker) return;
        const url = picker.getAttribute("data-recur-autosave-url");
        const selected = Array.from(
            picker.querySelectorAll('input[name="recur_weekdays"]:checked')
        ).map((el) => el.value);
        const formData = new FormData();
        for (const value of selected) formData.append("recur_weekdays", value);
        picker.classList.add("is-saving");
        try {
            const res = await fetch(url, { method: "POST", body: formData });
            if (!res.ok) throw new Error("recur save failed: " + res.status);
            const days = selected
                .map((v) => ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"][parseInt(v, 10)])
                .join(", ");
            showToast(days ? `Repeats on ${days}` : "Recurrence cleared");
        } catch (err) {
            console.error(err);
            showToast("Save failed");
        } finally {
            picker.classList.remove("is-saving");
        }
    });

    // CMD/Ctrl+Enter inside the inbox capture textarea fires "File".
    document.addEventListener("keydown", (event) => {
        if (event.key !== "Enter") return;
        if (!(event.metaKey || event.ctrlKey)) return;
        const ta = event.target.closest(".capture textarea");
        if (!ta) return;
        const form = ta.closest("form");
        if (!form) return;
        event.preventDefault();
        if (typeof form.requestSubmit === "function") {
            form.requestSubmit();
        } else {
            form.submit();
        }
    });

    // Inline description expand on list pages. The full body is rendered but
    // CSS-clamps to a few lines; where it actually overflows we add a
    // "more"/"less" toggle so the whole note can be read without opening the
    // edit page. Runs after fonts load (line heights settle) so the overflow
    // check is accurate.
    function initBodyPreviews() {
        document.querySelectorAll("[data-body-preview]").forEach((el) => {
            if (el.dataset.previewChecked) return;
            el.dataset.previewChecked = "1";
            // A clamped element that overflows has scrollHeight > clientHeight.
            if (el.scrollHeight - el.clientHeight > 1) {
                const btn = document.createElement("button");
                btn.type = "button";
                btn.className = "item__body-toggle";
                btn.textContent = "more";
                btn.setAttribute("aria-expanded", "false");
                el.insertAdjacentElement("afterend", btn);
            }
        });
    }

    document.addEventListener("click", (event) => {
        const btn = event.target.closest(".item__body-toggle");
        if (!btn) return;
        const preview = btn.previousElementSibling;
        if (!preview || !preview.matches("[data-body-preview]")) return;
        const expanded = preview.classList.toggle("is-expanded");
        btn.textContent = expanded ? "less" : "more";
        btn.setAttribute("aria-expanded", expanded ? "true" : "false");
    });

    if (document.fonts && document.fonts.ready) {
        document.fonts.ready.then(initBodyPreviews);
    }
    window.addEventListener("load", initBodyPreviews);

    // Expose helpers for inline scripts (e.g. the Weekly Review page).
    window.gtdCopyText = copyText;
    window.gtdShowToast = showToast;
})();
