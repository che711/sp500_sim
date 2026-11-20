"""Общая тема сайта и HTML-отчёта: стили и маленькие скрипты переключения светлой/тёмной темы."""

from __future__ import annotations

from pathlib import Path

CSS = (Path(__file__).with_name("theme.css")).read_text(encoding="utf-8")

# Ставит сохранённую тему до отрисовки страницы, чтобы не было вспышки
HEAD_JS = """
(function () {
  try {
    var saved = localStorage.getItem("theme");
    if (saved) document.documentElement.dataset.theme = saved;
  } catch (e) {}
})();
"""

# Кнопка-переключатель: у неё id="theme-toggle"
TOGGLE_JS = """
(function () {
  var button = document.getElementById("theme-toggle");
  if (!button) return;
  var root = document.documentElement;
  function current() {
    return root.dataset.theme ||
      (window.matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light");
  }
  function paint() { button.textContent = current() === "dark" ? "\\u2600" : "\\u263E"; }
  button.addEventListener("click", function () {
    var next = current() === "dark" ? "light" : "dark";
    root.dataset.theme = next;
    try { localStorage.setItem("theme", next); } catch (e) {}
    paint();
  });
  paint();
})();
"""

FAVICON = (
    "data:image/svg+xml,"
    "%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 24 24' fill='none' stroke='%232563eb' "
    "stroke-width='2.4' stroke-linecap='round' stroke-linejoin='round'%3E"
    "%3Cpolyline points='2,17 8,11 13,14 22,5'/%3E%3C/svg%3E"
)

LOGO_SVG = (
    '<svg viewBox="0 0 24 24" fill="none" stroke="var(--accent)" stroke-width="2.4" '
    'stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">'
    '<polyline points="2,17 8,11 13,14 22,5"/><polyline points="16,5 22,5 22,11"/></svg>'
)
