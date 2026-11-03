"""Поле для кода. Если установлен streamlit-ace (pip install streamlit-ace), это настоящий редактор
с подсветкой и клавишей Tab. Иначе обычное текстовое поле, которому скрипт возвращает Tab.
"""
import streamlit as st
import streamlit.components.v1 as components

from frontend.style import is_dark_theme

try:
    from streamlit_ace import st_ace
except ImportError:  # необязательная зависимость
    st_ace = None

PLACEHOLDER = "def strlen(s):\n    ..."

# Тема Ace синхронизируется в браузере: st.context.theme запаздывает при первом
# открытии и смене темы. Tab в textarea вставляет четыре пробела вместо смены фокуса.
# Shift+Tab работает как раньше (фокус на предыдущий элемент).
_TAB_SCRIPT = r"""
<script>
(function () {
  var win = window.parent, doc = win.document;
  var SELECTOR = 'textarea[aria-label="Код на Python"], [class*="st-key-code_"] textarea';
  function attach() {
    var app = doc.querySelector('[data-testid="stApp"]');
    if (app) {
      var rgb = win.getComputedStyle(app).backgroundColor.match(/\d+/g);
      if (rgb && rgb.length >= 3) {
        var dark = 0.2126 * Number(rgb[0]) + 0.7152 * Number(rgb[1]) + 0.0722 * Number(rgb[2]) < 128;
        var theme = "ace/theme/" + (dark ? "monokai" : "github");
        doc.querySelectorAll('iframe[title="streamlit_ace.streamlit_ace"]').forEach(function (frame) {
          try {
            var node = frame.contentDocument.querySelector(".ace_editor");
            var editor = node && node.env && node.env.editor;
            if (editor && editor.getTheme() !== theme) editor.setTheme(theme);
          } catch (e) { /* Компонент может ещё загружаться. */ }
          if (!frame.dataset.themeSync) {
            frame.dataset.themeSync = "1";
            frame.addEventListener("load", attach);
          }
        });
      }
    }
    doc.querySelectorAll(SELECTOR).forEach(function (ta) {
      if (ta.dataset.tabFix) return;
      ta.dataset.tabFix = "1";
      ta.addEventListener("keydown", function (e) {
        if (e.key !== "Tab" || e.shiftKey) return;
        e.preventDefault();
        var s = ta.selectionStart, end = ta.selectionEnd;
        var setter = Object.getOwnPropertyDescriptor(win.HTMLTextAreaElement.prototype, "value").set;
        setter.call(ta, ta.value.substring(0, s) + "    " + ta.value.substring(end));
        ta.selectionStart = ta.selectionEnd = s + 4;
        ta.dispatchEvent(new Event("input", { bubbles: true }));
      });
    });
  }
  attach();
  if (!win.__tabFixObserver) {
    win.__tabFixObserver = new win.MutationObserver(attach);
    win.__tabFixObserver.observe(doc.body, { attributes: true, childList: true, subtree: true });
  }
})();
</script>
"""


def code_editor(key: str, height: int = 190, value: str = "") -> str:
    """Возвращает текущий текст кода из поля ввода."""
    if st_ace is not None:
        try:
            code = st_ace(value=value, language="python",
                          theme="monokai" if is_dark_theme() else "github",
                          keybinding="vscode", font_size=14, tab_size=4,
                          height=height, auto_update=True, key=key) or ""
            components.html(_TAB_SCRIPT, height=0)
            return code
        except Exception:  # несовместимая версия компонента: остаёмся на обычном поле
            pass
    code = st.text_area("Код на Python", key=key, value=value, height=height,
                        label_visibility="collapsed", placeholder=PLACEHOLDER)
    components.html(_TAB_SCRIPT, height=0)
    return code
