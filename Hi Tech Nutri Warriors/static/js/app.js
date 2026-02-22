/* Hi Tech Nutri Warriors — Camera, Identify, Confirm & Analyze */

// ── Nutrient display config ───────────────────────────────
// Each entry is either a section header { h: "Label" }
// or a data row     [ key, display label, unit ]
const MICRO_SECTIONS = [
  { h: "Carbohydrates" },
  ["Fiber_g",        "Dietary Fiber",  "g"],
  ["TotalSugar_g",   "Total Sugar",    "g"],
  ["AddedSugar_g",   "Added Sugar",    "g"],
  { h: "Fats" },
  ["SatFat_g",       "Saturated Fat",  "g"],
  ["TransFat_g",     "Trans Fat",      "g"],
  { h: "Other" },
  ["Cholesterol_mg", "Cholesterol",    "mg"],
  ["Sodium_mg",      "Sodium",         "mg"],
  { h: "Vitamins" },
  ["VitaminC_mg",    "Vitamin C",      "mg"],
  ["VitaminA_mcg",   "Vitamin A",      "mcg"],
  ["VitaminK_mcg",   "Vitamin K",      "mcg"],
  ["Folate_mcg",     "Folate (B9)",    "mcg"],
  { h: "Minerals" },
  ["Calcium_mg",     "Calcium",        "mg"],
  ["Iron_mg",        "Iron",           "mg"],
  ["Potassium_mg",   "Potassium",      "mg"],
  ["Magnesium_mg",   "Magnesium",      "mg"],
];

// ── State ─────────────────────────────────────────────────
let currentRecord    = null;   // full nutrition record after analysis
let currentImageData = null;   // base64 image stored between identify & analyze

// ── DOM refs ──────────────────────────────────────────────
const video            = document.getElementById("video");
const canvas           = document.getElementById("canvas");
const capturedImg      = document.getElementById("captured-img");
const placeholder      = document.getElementById("camera-placeholder");
const startBtn         = document.getElementById("start-btn");
const captureBtn       = document.getElementById("capture-btn");
const scanAgainBtn     = document.getElementById("scan-again-btn");
const loadingCard      = document.getElementById("loading-card");
const loadingMsg       = document.getElementById("loading-msg");
const loadingSub       = document.getElementById("loading-sub");
const confirmCard      = document.getElementById("confirm-card");
const identifiedNameEl = document.getElementById("identified-name");
const confirmYesBtn    = document.getElementById("confirm-yes-btn");
const correctionInput  = document.getElementById("correction-input");
const confirmCorrectBtn= document.getElementById("confirm-correct-btn");
const resultsCard      = document.getElementById("results-card");
const exportCard       = document.getElementById("export-card");
const foodNameEl       = document.getElementById("food-name");
const dataTypeEl       = document.getElementById("data-type");
const confidenceBadge  = document.getElementById("confidence-badge");
const servingSizeEl    = document.getElementById("serving-size");
const mEnergy          = document.getElementById("m-energy");
const mProtein         = document.getElementById("m-protein");
const mCarbs           = document.getElementById("m-carbs");
const mFat             = document.getElementById("m-fat");
const nutrientsBody    = document.getElementById("nutrients-body");
const downloadCsvBtn   = document.getElementById("download-csv-btn");
const exportStatus     = document.getElementById("export-status");

// ── Camera ────────────────────────────────────────────────
let cameraStream = null;

startBtn.addEventListener("click", async () => {
  try {
    cameraStream = await navigator.mediaDevices.getUserMedia({
      video: {
        facingMode: { ideal: "environment" },
        width:  { ideal: 1280 },
        height: { ideal: 960 },
      },
    });
    video.srcObject = cameraStream;
    placeholder.style.display = "none";
    video.style.display = "block";
    startBtn.classList.add("hidden");
    captureBtn.classList.remove("hidden");
  } catch (err) {
    alert("Camera access failed: " + err.message +
          "\n\nMake sure you allow camera permissions in the browser.");
  }
});

captureBtn.addEventListener("click", () => {
  // Snapshot current frame
  canvas.width  = video.videoWidth;
  canvas.height = video.videoHeight;
  canvas.getContext("2d").drawImage(video, 0, 0);
  currentImageData = canvas.toDataURL("image/jpeg", 0.92);

  // Show frozen snapshot
  capturedImg.src = currentImageData;
  capturedImg.style.display = "block";
  video.style.display = "none";
  captureBtn.classList.add("hidden");

  // Stop camera stream
  if (cameraStream) {
    cameraStream.getTracks().forEach((t) => t.stop());
    cameraStream = null;
  }

  identifyFood();
});

scanAgainBtn.addEventListener("click", resetUI);

// ── Step 1: Identify ──────────────────────────────────────
async function identifyFood() {
  setLoading(true, "Identifying food…", "Using AI to recognise what's in the photo");
  hideAll([confirmCard, resultsCard, exportCard]);

  try {
    const res = await fetch("/identify", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ image: currentImageData }),
    });
    const result = await res.json();

    if (result.success) {
      showConfirmation(result.food_name);
    } else {
      alert("Identification failed: " + result.error);
      scanAgainBtn.classList.remove("hidden");
    }
  } catch (err) {
    alert("Network error: " + err.message);
    scanAgainBtn.classList.remove("hidden");
  } finally {
    setLoading(false);
  }
}

// ── Step 2: Confirm ───────────────────────────────────────
function showConfirmation(foodName) {
  identifiedNameEl.textContent = foodName;
  correctionInput.value = "";
  confirmCard.classList.remove("hidden");
  confirmCard.scrollIntoView({ behavior: "smooth", block: "start" });
}

// "Yes, that's correct" — use the identified name as-is
confirmYesBtn.addEventListener("click", () => {
  const name = identifiedNameEl.textContent.trim();
  confirmCard.classList.add("hidden");
  runFullAnalysis(name);
});

// "Analyze" with user-provided correction
confirmCorrectBtn.addEventListener("click", () => {
  const corrected = correctionInput.value.trim();
  if (!corrected) {
    correctionInput.focus();
    return;
  }
  confirmCard.classList.add("hidden");
  runFullAnalysis(corrected);
});

// Allow pressing Enter in the correction field
correctionInput.addEventListener("keydown", (e) => {
  if (e.key === "Enter") confirmCorrectBtn.click();
});

// ── Step 3: Analyze ───────────────────────────────────────
async function runFullAnalysis(confirmedName) {
  setLoading(true,
    `Getting nutrition for "${confirmedName}"…`,
    "Querying nutrition database via Claude"
  );

  try {
    const res = await fetch("/analyze", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        image: currentImageData,
        kid_name: "Web_User",
        confirmed_name: confirmedName,
      }),
    });
    const result = await res.json();

    if (result.success) {
      currentRecord = result.data;
      displayResults(result.data);
    } else {
      alert("Analysis failed: " + result.error);
      scanAgainBtn.classList.remove("hidden");
    }
  } catch (err) {
    alert("Network error: " + err.message);
    scanAgainBtn.classList.remove("hidden");
  } finally {
    setLoading(false);
  }
}

// ── Display results ───────────────────────────────────────
function displayResults(d) {
  foodNameEl.textContent    = d.Food_Name || "Unknown Food";
  dataTypeEl.textContent    = d.Data_Type || "";
  servingSizeEl.textContent = d.Serving_Size_g ?? "N/A";

  const conf = d.Confidence ?? 0;
  confidenceBadge.textContent = Math.round(conf * 100) + "% confidence";
  confidenceBadge.className   =
    "badge " + (conf >= 0.7 ? "high" : conf >= 0.4 ? "medium" : "low");

  mEnergy.textContent  = fmt(d.Energy_kcal);
  mProtein.textContent = fmt(d.Protein_g);
  mCarbs.textContent   = fmt(d.Carbohydrate_g);
  mFat.textContent     = fmt(d.Fat_g);

  nutrientsBody.innerHTML = MICRO_SECTIONS.map((item) => {
    if (item.h) {
      return `<tr class="section-header"><td colspan="2">${item.h}</td></tr>`;
    }
    const [key, label, unit] = item;
    const val = d[key];
    return `<tr>
      <td>${label}</td>
      <td>${val != null ? Number(val).toFixed(1) + " " + unit : "N/A"}</td>
    </tr>`;
  }).join("");

  resultsCard.classList.remove("hidden");
  exportCard.classList.remove("hidden");
  scanAgainBtn.classList.remove("hidden");
  resultsCard.scrollIntoView({ behavior: "smooth", block: "start" });
}

function fmt(val) {
  return val != null ? Number(val).toFixed(1) : "—";
}

// ── CSV Export ────────────────────────────────────────────
downloadCsvBtn.addEventListener("click", () => {
  if (!currentRecord) return;
  const headers = Object.keys(currentRecord);
  const values  = Object.values(currentRecord).map((v) => {
    if (v == null) return "";
    return `"${String(v).replace(/"/g, '""')}"`;
  });
  const csv  = [headers.join(","), values.join(",")].join("\n");
  const blob = new Blob([csv], { type: "text/csv;charset=utf-8;" });
  const url  = URL.createObjectURL(blob);
  const a    = document.createElement("a");
  a.href     = url;
  a.download = "nutrition_" +
    (currentRecord.Food_Name || "result").replace(/\s+/g, "_") + ".csv";
  document.body.appendChild(a);
  a.click();
  document.body.removeChild(a);
  URL.revokeObjectURL(url);
  setExportStatus("CSV downloaded!", "success");
});

// ── Helpers ───────────────────────────────────────────────
function setLoading(on, msg = "", sub = "") {
  loadingCard.classList.toggle("hidden", !on);
  if (on) {
    loadingMsg.textContent = msg;
    loadingSub.textContent = sub;
  }
}

function hideAll(elements) {
  elements.forEach((el) => el.classList.add("hidden"));
}

function setExportStatus(msg, type) {
  exportStatus.textContent = msg;
  exportStatus.className   = "export-status " + (type || "");
  exportStatus.classList.toggle("hidden", !msg);
}

function resetUI() {
  // Camera
  capturedImg.style.display = "none";
  capturedImg.src = "";
  placeholder.style.display = "";
  video.style.display = "none";

  // Buttons
  startBtn.classList.remove("hidden");
  captureBtn.classList.add("hidden");
  scanAgainBtn.classList.add("hidden");

  // Cards
  hideAll([loadingCard, confirmCard, resultsCard, exportCard]);

  // State
  currentRecord    = null;
  currentImageData = null;
  setExportStatus("", "");
}
