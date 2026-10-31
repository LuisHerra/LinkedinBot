"""Inspecciona la estructura HTML de una página de LinkedIn para ajustar selectores (solo lectura).

Uso local, con las mismas variables que el bot (CHROME_PROFILE_DIR, PROXY_PRIMARY...):
    python tools/inspect_page.py "https://www.linkedin.com/search/results/people/?keywords=data&network=%5B%22S%22%5D"
"""
import json
import random
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from bot.browser import choose_proxy, open_page  # noqa: E402
from bot.config import load_config  # noqa: E402
from bot.linkedin import LinkedIn  # noqa: E402

PROBE = """() => {
    const q = s => document.querySelectorAll(s).length;
    const profileLinks = [...document.querySelectorAll('a[href*="/in/"]')];
    const sample = profileLinks.slice(0, 4).map(a => {
        const chain = [];
        let el = a;
        for (let i = 0; i < 8 && el; i++, el = el.parentElement) {
            chain.push(el.tagName.toLowerCase()
                + (el.getAttribute('role') ? `[role=${el.getAttribute('role')}]` : '')
                + (el.getAttribute('data-view-name') ? `[dvn=${el.getAttribute('data-view-name')}]` : '')
                + (el.componentKey || el.getAttribute('componentkey') ? '[componentkey]' : ''));
        }
        return {href: a.getAttribute('href').slice(0, 80), text: a.innerText.slice(0, 60), chain};
    });
    const lis = [...document.querySelectorAll('[role="listitem"], li')].filter(e => e.innerText.includes('común'));
    return {
        url: location.href,
        main: q('main'), roleMain: q('[role="main"]'),
        profileLinks: profileLinks.length,
        listitems: q('[role="listitem"]'), lis: q('li'),
        cardsWithMutual: lis.length,
        mutualSample: lis.slice(0, 2).map(e => e.innerText.slice(0, 400)),
        sample,
    };
}"""


# Controles clicables con su texto/aria-label y la sección que los contiene (para la tarjeta de perfil).
PROBE_ACTIONS = """() => {
    const out = [];
    for (const el of document.querySelectorAll('button, a, [role="button"], [role="menuitem"]')) {
        const label = (el.getAttribute('aria-label') || el.innerText || '').trim().replace(/\\s+/g, ' ');
        if (!/conectar|connect|seguir|follow|más|more|mensaje|message|pendiente|pending/i.test(label)) continue;
        if (label.length > 80) continue;
        const sec = el.closest('section');
        out.push({
            tag: el.tagName.toLowerCase(), role: el.getAttribute('role'), label,
            href: (el.getAttribute('href') || '').slice(0, 90),
            inMain: !!el.closest('main'),
            sectionIndex: sec ? [...document.querySelectorAll('main section')].indexOf(sec) : null,
            top: Math.round(el.getBoundingClientRect().top),
        });
    }
    return {sections: document.querySelectorAll('main section').length, actions: out};
}"""


def main(url, mode="search"):
    cfg = load_config()
    _, proxy_url, ip = choose_proxy(cfg, "2026-W00")
    print("IP:", ip)
    with open_page(cfg, proxy_url) as page:
        li = LinkedIn(page, cfg, random.Random())
        li.goto(url)
        if mode == "profile":
            print(json.dumps(page.evaluate(PROBE_ACTIONS), indent=2, ensure_ascii=False))
            return
        li.scroll_a_bit(4)
        print(json.dumps(page.evaluate(PROBE), indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main(*sys.argv[1:3])
