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

# Tab в textarea по умолчанию переносит фокус. Скрипт вставляет четыре пробела вместо этого.
# Shift+Tab работает как раньше (фокус на предыдущий элемент).
_TAB_SCRIPT = """
<script>
(function () {
  var win = window.parent, doc = win.document;
  var SELECTOR = 'textarea[aria-label="Код на Python"], [class*="st-key-code_"] textarea';
  function attach() {
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
    win.__tabFixObserver.observe(doc.body, { childList: true, subtree: true });
  }
})();
</script>
"""


def code_editor(key: str, height: int = 190, value: str = "") -> str:
    """Возвращает текущий текст кода из поля ввода."""
    if st_ace is not None:
        try:
            return st_ace(value=value, language="python",
                          theme="monokai" if is_dark_theme() else "github",
                          keybinding="vscode", font_size=14, tab_size=4,
                          height=height, auto_update=True, key=key) or ""
        except Exception:  # несовместимая версия компонента: остаёмся на обычном поле
            pass
    code = st.text_area("Код на Python", key=key, value=value, height=height,
                        label_visibility="collapsed", placeholder=PLACEHOLDER)
    components.html(_TAB_SCRIPT, height=0)
    return code