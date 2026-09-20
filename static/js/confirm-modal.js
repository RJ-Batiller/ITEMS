(function () {
    var style = document.createElement("style");
    style.textContent = '.app-confirm-modal{position:fixed;inset:0;z-index:5000;display:grid;place-items:center;padding:20px;background:rgba(19,24,31,.5);backdrop-filter:blur(5px)}.app-confirm-modal[hidden]{display:none}.app-confirm-dialog{width:min(100%,460px);padding:28px;border:1px solid rgba(201,45,71,.22);border-radius:22px;background:#fff;box-shadow:0 24px 70px rgba(26,31,42,.28);display:grid;grid-template-columns:auto 1fr;gap:0 16px}.app-confirm-icon{width:42px;height:42px;border-radius:14px;display:grid;place-items:center;background:#fff0f2;color:#c92d47;font-size:1.35rem;font-weight:800}.app-confirm-copy{min-width:0}.app-confirm-kicker{margin:0 0 5px;color:#c92d47;text-transform:uppercase;letter-spacing:.14em;font-size:.68rem;font-weight:800}.app-confirm-copy h2{margin:0;color:#20242b;font-size:1.25rem}.app-confirm-copy p:last-child{margin:10px 0 0;color:#626a75;line-height:1.55}.app-confirm-actions{grid-column:1/-1;display:flex;justify-content:flex-end;gap:10px;margin-top:24px}.app-confirm-actions .btn{min-width:100px}.app-confirm-actions .btn-danger{background:#c92d47;border-color:#c92d47}.app-confirm-actions .btn-danger:hover{background:#a92239;border-color:#a92239}@media(max-width:520px){.app-confirm-dialog{padding:22px;border-radius:18px}.app-confirm-actions{flex-direction:column-reverse}.app-confirm-actions .btn{width:100%}}';
    document.head.appendChild(style);
    var pending = null;
    var modal = document.createElement("div");
    modal.className = "app-confirm-modal";
    modal.hidden = true;
    modal.innerHTML = '<div class="app-confirm-dialog" role="dialog" aria-modal="true" aria-labelledby="app-confirm-title" aria-describedby="app-confirm-message"><div class="app-confirm-icon">!</div><div class="app-confirm-copy"><p class="app-confirm-kicker">Please confirm</p><h2 id="app-confirm-title">Confirm action</h2><p id="app-confirm-message"></p></div><div class="app-confirm-actions"><button type="button" class="btn btn-light" data-confirm-cancel>Cancel</button><button type="button" class="btn btn-danger" data-confirm-continue>Continue</button></div></div>';
    document.body.appendChild(modal);

    var messageNode = modal.querySelector("#app-confirm-message");
    var cancelButton = modal.querySelector("[data-confirm-cancel]");
    var continueButton = modal.querySelector("[data-confirm-continue]");

    function getMessage(handler) {
        var match = handler && handler.match(/confirm\(\s*(['"])([\s\S]*?)\1\s*\)/);
        return match ? match[2] : "Are you sure you want to continue?";
    }

    function close() {
        modal.hidden = true;
        pending = null;
    }

    function proceed() {
        if (!pending) return;
        var action = pending.element;
        var type = pending.type;
        close();
        if (type === "submit") {
            action.removeAttribute("onsubmit");
            if (action.requestSubmit) action.requestSubmit();
            else HTMLFormElement.prototype.submit.call(action);
        } else {
            action.removeAttribute("onclick");
            action.click();
        }
    }

    function open(element, type, handler) {
        pending = { element: element, type: type };
        messageNode.textContent = getMessage(handler);
        modal.hidden = false;
        cancelButton.focus();
    }

    document.addEventListener("submit", function (event) {
        var form = event.target;
        var handler = form.getAttribute("onsubmit");
        if (!handler || handler.indexOf("confirm(") === -1) return;
        event.preventDefault();
        event.stopImmediatePropagation();
        open(form, "submit", handler);
    }, true);

    document.addEventListener("click", function (event) {
        var button = event.target.closest("[onclick]");
        if (!button) return;
        var handler = button.getAttribute("onclick");
        if (handler.indexOf("confirm(") === -1) return;
        event.preventDefault();
        event.stopImmediatePropagation();
        open(button, "click", handler);
    }, true);

    cancelButton.addEventListener("click", close);
    continueButton.addEventListener("click", proceed);
    modal.addEventListener("click", function (event) {
        if (event.target === modal) close();
    });
    document.addEventListener("keydown", function (event) {
        if (event.key === "Escape" && !modal.hidden) close();
    });
}());

