(function () {
    function isSensitive(field) {
        return field.type === "password" || field.type === "file" || field.type === "hidden" || field.name === "csrf_token";
    }

    function formKey(form, index) {
        var variant = form.querySelector("input[name=\"maintenance_action\"]");
        var variantKey = variant ? ":" + variant.value : "";
        return "items-form-draft:" + window.location.pathname + ":" +
            (form.dataset.draftKey || form.id || form.action || index) + variantKey;
    }

    function fieldKey(field, index) {
        return (field.name || field.id || "field") + ":" + index;
    }

    function restoreDraft(form, key) {
        var saved = sessionStorage.getItem(key);
        if (!saved) return;
        try {
            var values = JSON.parse(saved);
            Array.prototype.forEach.call(form.elements, function (field, index) {
                var value = values[fieldKey(field, index)];
                if (value === undefined || isSensitive(field)) return;
                if (field.type === "checkbox" || field.type === "radio") field.checked = value === true;
                else field.value = value;
                field.dispatchEvent(new Event("input", { bubbles: true }));
                field.dispatchEvent(new Event("change", { bubbles: true }));
            });
        } catch (error) {
            sessionStorage.removeItem(key);
        }
    }

    function watchForm(form, index) {
        var key = formKey(form, index);
        if (document.querySelector(".alert-success")) sessionStorage.removeItem(key);
        restoreDraft(form, key);
        form.addEventListener("input", function () {
            var values = {};
            Array.prototype.forEach.call(form.elements, function (field, fieldIndex) {
                if (!field.name || isSensitive(field)) return;
                values[fieldKey(field, fieldIndex)] = field.type === "checkbox" || field.type === "radio"
                    ? field.checked
                    : field.value;
            });
            sessionStorage.setItem(key, JSON.stringify(values));
        });
        form.addEventListener("submit", function () {
            form.dispatchEvent(new Event("input", { bubbles: false }));
        });
        form.addEventListener("change", function () {
            form.dispatchEvent(new Event("input", { bubbles: false }));
        });
    }

    function setupPasswordStrength(input) {
        var container = input.dataset.passwordStrength
            ? document.querySelector(input.dataset.passwordStrength)
            : null;
        if (!container) {
            container = document.createElement("div");
            container.className = "password-rules";
            container.innerHTML = '<div class="password-rule is-invalid" data-password-rule="length"><span class="password-rule-icon">-</span>8 characters</div>' +
                '<div class="password-rule is-invalid" data-password-rule="special"><span class="password-rule-icon">-</span>At least 1 special character</div>' +
                '<div class="password-rule is-invalid" data-password-rule="uppercase"><span class="password-rule-icon">-</span>At least 1 uppercase letter</div>' +
                '<div class="password-rule is-invalid" data-password-rule="number"><span class="password-rule-icon">-</span>At least 1 number</div>';
            input.insertAdjacentElement("afterend", container);
        }

        var rules = {
            length: function (value) { return value.length >= 8; },
            uppercase: function (value) { return /[A-Z]/.test(value); },
            number: function (value) { return /[0-9]/.test(value); },
            special: function (value) { return /[^A-Za-z0-9]/.test(value); }
        };

        function update() {
            var value = input.value;
            Object.keys(rules).forEach(function (name) {
                var item = container.querySelector('[data-password-rule="' + name + '"]');
                if (!item) return;
                var valid = rules[name](value);
                item.classList.toggle("is-valid", valid);
                item.classList.toggle("is-invalid", !valid);
                item.querySelector(".password-rule-icon").textContent = valid ? "+" : "-";
            });
        }

        input.addEventListener("input", update);
        update();
    }

    document.querySelectorAll("[data-password-strength], input[name='new_password']").forEach(setupPasswordStrength);
    document.querySelectorAll("form").forEach(watchForm);
}());


