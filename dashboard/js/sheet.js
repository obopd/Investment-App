// Bottom sheet: slides up, closes by tapping outside, Esc, or dragging the grabber down.
const sheet = document.getElementById("sheet");
const body = document.getElementById("sheet-body");

export function openSheet(html) {
  body.innerHTML = html;
  sheet.scrollTop = 0;
  sheet.classList.remove("closing");
  sheet.style.transform = "";
  sheet.showModal();
}

export function closeSheet() {
  if (!sheet.open || sheet.classList.contains("closing")) return;
  const reduced = matchMedia("(prefers-reduced-motion: reduce)").matches;
  sheet.classList.add("closing");
  setTimeout(() => { sheet.close(); sheet.classList.remove("closing"); sheet.style.transform = ""; }, reduced ? 0 : 210);
}

// Tap on the dimmed backdrop (outside the sheet's box) closes it.
sheet.addEventListener("click", (e) => {
  const r = sheet.getBoundingClientRect();
  if (e.clientY < r.top || e.clientX < r.left || e.clientX > r.right) closeSheet();
});
sheet.addEventListener("cancel", (e) => { e.preventDefault(); closeSheet(); });      // Esc

let startY = null;
sheet.querySelector(".grabber").addEventListener("pointerdown", (e) => { startY = e.clientY; e.target.setPointerCapture(e.pointerId); });
sheet.addEventListener("pointermove", (e) => {
  if (startY == null) return;
  sheet.style.transform = `translateY(${Math.max(0, e.clientY - startY)}px)`;
});
sheet.addEventListener("pointerup", (e) => {
  if (startY == null) return;
  const moved = e.clientY - startY; startY = null;
  if (moved > 80) closeSheet(); else sheet.style.transform = "";
});
