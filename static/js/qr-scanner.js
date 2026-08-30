let lastScanned = "";
let scanning = true;

function esc(value) {
    if (value === null || value === undefined || value === "") return "-";
    return String(value)
        .replaceAll("&", "&amp;")
        .replaceAll("<", "&lt;")
        .replaceAll(">", "&gt;")
        .replaceAll('"', "&quot;")
        .replaceAll("'", "&#039;");
}

function showItem(data) {
    document.getElementById("scanStatus").className = "alert alert-success mt-3 mb-0";
    document.getElementById("scanStatus").textContent = "QR code scanned successfully.";
    document.getElementById("result").innerHTML = `
        <div class="mb-3"><span class="badge bg-primary">${esc(data.status)}</span></div>
        <div class="row g-3">
            <div class="col-md-6"><small class="text-muted">Asset Code</small><div class="fw-bold">${esc(data.asset_code)}</div></div>
            <div class="col-md-6"><small class="text-muted">Item Name</small><div class="fw-bold">${esc(data.name)}</div></div>
            <div class="col-md-6"><small class="text-muted">Serial Number</small><div>${esc(data.serial_number)}</div></div>
            <div class="col-md-6"><small class="text-muted">Category</small><div>${esc(data.category_name)}</div></div>
            <div class="col-md-6"><small class="text-muted">Office</small><div>${esc(data.office_name)}</div></div>
            <div class="col-md-6"><small class="text-muted">Acquisition Date</small><div>${esc(data.acquisition_date)}</div></div>
            <div class="col-12"><small class="text-muted">Description</small><div>${esc(data.description)}</div></div>
            <div class="col-12"><small class="text-muted">Specifications</small><div>${esc(data.specifications)}</div></div>
        </div>`;
}

function showError(message) {
    document.getElementById("scanStatus").className = "alert alert-danger mt-3 mb-0";
    document.getElementById("scanStatus").textContent = message;
}

function loadByAssetCode(assetCode) {
    fetch("/api/equipment/" + encodeURIComponent(assetCode))
        .then(response => {
            if (!response.ok) throw new Error("Request failed");
            return response.json();
        })
        .then(data => data.error ? showError("Item not found: " + assetCode) : showItem(data))
        .catch(() => showError("Unable to retrieve item information."));
}

function onScanSuccess(decodedText) {
    if (!scanning || decodedText === lastScanned) return;
    lastScanned = decodedText;
    scanning = false;

    try {
        const url = new URL(decodedText);
        const assetFromUrl = url.searchParams.get("asset_code") || url.searchParams.get("asset") || decodeURIComponent(url.pathname.split("/").filter(Boolean).pop() || "");
        loadByAssetCode(assetFromUrl && !assetFromUrl.includes(".") ? assetFromUrl : decodedText);
    } catch (error) {
        loadByAssetCode(decodedText.trim());
    }

    setTimeout(() => {
        scanning = true;
        lastScanned = "";
    }, 2000);
}

function onScanFailure() {}

if (typeof Html5QrcodeScanner === "undefined") {
    showError("The QR scanner library could not be loaded. Check your internet connection and refresh the page.");
} else {
    const scanner = new Html5QrcodeScanner("reader", {
        fps: 10,
        qrbox: { width: 250, height: 250 },
        rememberLastUsedCamera: true
    });
    scanner.render(onScanSuccess, onScanFailure);
}
