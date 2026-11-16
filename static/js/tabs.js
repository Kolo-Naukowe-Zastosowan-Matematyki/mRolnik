// Dostępne zakładki (wzorzec WAI-ARIA Tabs: strzałki, Home/End, aktywacja automatyczna).

export function initTabs(tablist, onActivate) {
  const tabs = Array.from(tablist.querySelectorAll('[role="tab"]'));
  const root = tablist.getRootNode();

  function panelOf(tab) {
    return root.getElementById ? root.getElementById(tab.getAttribute('aria-controls')) : document.getElementById(tab.getAttribute('aria-controls'));
  }

  function activate(tab, focus = true) {
    tabs.forEach((t) => {
      const sel = t === tab;
      t.setAttribute('aria-selected', String(sel));
      t.tabIndex = sel ? 0 : -1;
      const p = panelOf(t);
      if (p) p.hidden = !sel;
    });
    if (focus) tab.focus();
    onActivate(tab.id.replace('mr-tab-', ''), panelOf(tab));
  }

  tabs.forEach((tab, i) => {
    tab.addEventListener('click', () => activate(tab, false));
    tab.addEventListener('keydown', (e) => {
      let idx = null;
      if (e.key === 'ArrowRight') idx = (i + 1) % tabs.length;
      else if (e.key === 'ArrowLeft') idx = (i - 1 + tabs.length) % tabs.length;
      else if (e.key === 'Home') idx = 0;
      else if (e.key === 'End') idx = tabs.length - 1;
      if (idx !== null) {
        e.preventDefault();
        activate(tabs[idx]);
      }
    });
  });

  return {
    select(name, focus = false) {
      const t = tabs.find((x) => x.id === `mr-tab-${name}`);
      if (t) activate(t, focus);
    },
    current() {
      const t = tabs.find((x) => x.getAttribute('aria-selected') === 'true');
      return t ? t.id.replace('mr-tab-', '') : null;
    },
  };
}
