#!/usr/bin/env python3
"""
WCAG contrast audit of every page, public / member / admin, in both themes.

Measures the real computed text color against the real (composited) background
of every visible text element, input value and placeholder: 4.5:1 for normal
text, 3:1 for large text. Disabled controls are reported separately (exempt).
Gradient / image backgrounds are approximated by their average color.

Usage (needs `pip install playwright` and a Chromium):
  cd frontend && VITE_API_MOCK=true npm run dev -- --port 5199   # mock mode: no backend needed
  python3 scripts/contrast_audit.py              # exits 1 if anything fails

Env: AUDIT_BASE (default http://localhost:5199), AUDIT_OUT (JSON output dir,
default /tmp/contrast-audit), CHROMIUM_PATH (optional Chromium executable).
"""
import base64, json, re, sys, time
from playwright.sync_api import sync_playwright

import os
S = os.environ.get("AUDIT_OUT", "/tmp/contrast-audit")
os.makedirs(S, exist_ok=True)
BASE = os.environ.get("AUDIT_BASE", "http://localhost:5199")
CHROMIUM = os.environ.get("CHROMIUM_PATH") or None

def b64(o): return base64.urlsafe_b64encode(json.dumps(o).encode()).decode().rstrip("=")
REG = f"{b64({'email':'a@b.co','exp':int(time.time())+86400})}.sig"
ADM = f"{b64({'alg':'none'})}.{b64({'exp':int(time.time())+86400,'email':'admin@x.test'})}.sig"

AUDIT_JS = r"""
() => {
  const parse = (c) => {
    const m = c.match(/rgba?\(([^)]+)\)/);
    if (m) { const p = m[1].split(/[ ,\/]+/).filter(Boolean).map(Number); return {r:p[0],g:p[1],b:p[2],a:p.length>3?p[3]:1}; }
    const s = c.match(/color\(srgb ([^)]+)\)/);
    if (s) { const p = s[1].split(/[ \/]+/).map(Number); return {r:p[0]*255,g:p[1]*255,b:p[2]*255,a:p.length>3?p[3]:1}; }
    return null;
  };
  const over = (top, bot) => { const a = top.a + bot.a*(1-top.a); if (a===0) return {r:0,g:0,b:0,a:0};
    return { r:(top.r*top.a+bot.r*bot.a*(1-top.a))/a, g:(top.g*top.a+bot.g*bot.a*(1-top.a))/a, b:(top.b*top.a+bot.b*bot.a*(1-top.a))/a, a }; };
  const lum = (c) => { const f = (v) => { v/=255; return v<=0.03928 ? v/12.92 : Math.pow((v+0.055)/1.055, 2.4); }; return 0.2126*f(c.r)+0.7152*f(c.g)+0.0722*f(c.b); };
  const ratio = (a, b) => { const l1 = lum(a), l2 = lum(b); return (Math.max(l1,l2)+0.05)/(Math.min(l1,l2)+0.05); };
  const gradientColors = (img) => { const out = []; const re = /rgba?\([^)]+\)/g; let m; while ((m = re.exec(img))) out.push(parse(m[0])); return out.filter(Boolean); };

  // Effective background: composite ancestors' backgrounds bottom-up over white.
  const bgOf = (el) => {
    const chain = []; for (let n = el; n; n = n.parentElement) chain.unshift(n);
    let base = {r:255,g:255,b:255,a:1}; let approx = false;
    for (const n of chain) {
      const cs = getComputedStyle(n);
      const c = parse(cs.backgroundColor); if (c && c.a > 0) base = over(c, base);
      if (cs.backgroundImage && cs.backgroundImage !== 'none') {
        const cols = gradientColors(cs.backgroundImage);
        if (cols.length) { // approximate a gradient by the worst case: use each stop, keep list
          approx = true;
          // alpha-weighted (premultiplied) average so transparent stops don't darken the result
          const wsum = cols.reduce((t,c)=>t+c.a,0);
          const avg = wsum>0 ? {r:cols.reduce((t,c)=>t+c.r*c.a,0)/wsum, g:cols.reduce((t,c)=>t+c.g*c.a,0)/wsum, b:cols.reduce((t,c)=>t+c.b*c.a,0)/wsum, a:wsum/cols.length} : {r:0,g:0,b:0,a:0};
          base = over(avg, base);
        } else if (cs.backgroundImage.includes('url(')) approx = true;
      }
    }
    return {c: base, approx};
  };
  const opacityOf = (el) => { let o = 1; for (let n = el; n; n = n.parentElement) o *= parseFloat(getComputedStyle(n).opacity); return o; };
  const visible = (el) => { const r = el.getBoundingClientRect(); if (r.width < 1 || r.height < 1) return false;
    for (let n = el; n; n = n.parentElement) { const cs = getComputedStyle(n); if (cs.display==='none'||cs.visibility==='hidden') return false; if (parseFloat(cs.opacity)===0) return false; }
    return true; };
  const sel = (el) => { let s = el.tagName.toLowerCase(); if (el.id) s += '#'+el.id; const c = (el.className && el.className.baseVal===undefined ? String(el.className) : '').trim().split(/\s+/).filter(Boolean).slice(0,3); if (c.length) s += '.'+c.join('.'); return s; };

  const results = [];
  const check = (el, text, fgStr, kind) => {
    const fgRaw = parse(fgStr); if (!fgRaw) return;
    const {c: bg, approx} = bgOf(el);
    const op = opacityOf(el);
    const fg = over({...fgRaw, a: fgRaw.a*op}, bg);
    const cs = getComputedStyle(el);
    const size = parseFloat(cs.fontSize), weight = parseInt(cs.fontWeight) || 400;
    const large = size >= 24 || (size >= 18.66 && weight >= 700);
    const r = ratio(fg, bg);
    const need = large ? 3 : 4.5;
    results.push({sel: sel(el), text: text.slice(0,48), ratio: Math.round(r*100)/100, need, fail: r < need, kind,
      fg: `rgb(${Math.round(fg.r)},${Math.round(fg.g)},${Math.round(fg.b)})`, bg: `rgb(${Math.round(bg.r)},${Math.round(bg.g)},${Math.round(bg.b)})`, size, weight, approx,
      disabled: !!el.disabled || el.getAttribute('aria-disabled')==='true' || !!el.closest('[disabled]')});
  };

  const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
  const seen = new Set();
  while (walker.nextNode()) {
    const t = walker.currentNode; const text = t.nodeValue.replace(/\s+/g,' ').trim();
    if (!text || !/[\p{L}\p{N}]/u.test(text)) continue; const el = t.parentElement; if (!el || seen.has(el)) continue;
    if (['SCRIPT','STYLE','NOSCRIPT','OPTION'].includes(el.tagName)) continue;
    if (!visible(el)) continue; seen.add(el);
    check(el, text, getComputedStyle(el).color, 'text');
  }
  for (const el of document.querySelectorAll('input, textarea, select')) {
    if (!visible(el) || ['checkbox','radio','hidden','file','range','color'].includes(el.type)) continue;
    const cs = getComputedStyle(el);
    if (el.value) check(el, el.value, cs.color, 'input');
    else if (el.placeholder) check(el, el.placeholder, getComputedStyle(el, '::placeholder').color, 'placeholder');
  }
  return results;
}
"""

findings = []  # (theme, page, item)

def audit(pg, theme, label):
    pg.wait_for_timeout(700)
    items = pg.evaluate(AUDIT_JS)
    for it in items:
        findings.append({"theme": theme, "page": label, **it})
    bad = [i for i in items if i["fail"] and not i["disabled"]]
    print(f"  {theme:5} {label:42} {len(items):4} checked, {len(bad):3} failing", flush=True)

def mock_routes(pg):
    members = [{"email":"ann@x.test","name":"Ann Mathew","phone":"09011112222","memberId":"12","isMember":True,"renewedForNextYear":False,
                "npoStatus2025":"Member","npoStatus2026":"Non-member","lastActivePeriod":{"from":"2025-11-01","to":"2026-10-31"}},
               {"email":"bo@x.test","name":"Bo Nair","phone":"","memberId":"13","isMember":False,"renewedForNextYear":True,
                "npoStatus2025":"Member","npoStatus2026":"Renewed","lastActivePeriod":{"from":"2026-11-01","to":"2027-10-31"}}]
    agms = [{"agmId":"agm-2026","title":"WMF Japan AGM 2026","date":"2099-11-15","venue":"Tobu Kuminkan","enabled":True,"active":True,"registrationCount":2,"peopleCount":4},
            {"agmId":"agm-2025","title":"AGM 2025","date":"2025-11-16","venue":"Kasai Hall","enabled":False,"active":False,"registrationCount":1,"peopleCount":2}]
    regs = [{"name":"Ann","email":"a@x.test","attendance":"in_person","adults":2,"teens":0,"kids":1,"toddlers":0,"totalAttendees":3,"registeredAt":"2026-10-01"}]
    camps = [{"campaignId":"c1","type":"join","status":"sending","recipientCount":2134,"sent":640,"failed":2,"createdAt":"2026-10-08T01:00:00+00:00"}]
    agm = {"agmId":"agm-2026","title":"WMF Japan AGM 2026","date":"2099-11-15","startTime":"18:00","endTime":"21:00","venue":"Tobu Kuminkan, Edogawa-ku","venueJa":"江戸川区 東部区民館","access":"5-minute walk from Mizue Station","mapUrl":"https://maps.example/x","remoteNote":"Remote participation will also be available.","refreshmentsNote":"Free refreshments for those attending in person.","notes":""}
    def handle(route):
        req = route.request
        if req.resource_type not in ("fetch", "xhr"): return route.continue_()
        u = req.url
        def ok(body, status=200): return route.fulfill(status=status, content_type="application/json", body=json.dumps(body))
        if "site-config" in u and "admin" not in u: return ok({"config": {"agm": agm}})
        if "/admin/member-announcements" in u: return ok({"campaigns": camps})
        m = re.search(r"/admin/agms/([^/?]+)", u)
        if m: return ok({"agm": agms[0], "registrations": regs, "totals": {"adults":2,"teens":0,"kids":1,"toddlers":0,"registrations":1,"inPerson":3,"remote":0,"people":3}})
        if "/admin/agms" in u: return ok({"agms": agms})
        if "/admin/users" in u: return ok({"members": members})
        if "/admin/npo-membership" in u: return ok({"transactions": [{"memberid":"12","transactionNo":"1","paymentStatus":"pending","paidVia":"bank","membershipAmountPaid":1000}]})
        if "/admin/" in u: return ok({})
        return ok({})
    pg.route(re.compile(r".*(/admin|/site-config|/events|/visits|/partners|/membership|/my|/register|/seat|/celebrity|/entry|/available)[^.]*$"), handle)

PUBLIC = ["/", "/events", "/signin", "/sponsors", "/terms", "/privacy", "/cancellation", "/check-in", "/seat-check-in", "/admin"]
MEMBER_PAGES = [("/member/profile", "member"), ("/member/membership", "none"), ("/member/membership", "member"),
                ("/member/membership", "renewed"), ("/member/membership", "pending"), ("/member/membership", "pending-failed"),
                ("/my/registrations", "member"), ("/celebrity-dinner?event=sample_event", "none"),
                ("/my/hall-seat-selection", "none"), ("/my/seat-upgrade", "none")]

def run(theme):
    with sync_playwright() as p:
        b = p.chromium.launch(executable_path=CHROMIUM)
        # 1) signed out public pages
        ctx = b.new_context(viewport={"width": 1100, "height": 900})
        ctx.add_init_script(f"localStorage.setItem('nk-theme','{theme}')")
        pg = ctx.new_page(); mock_routes(pg)
        for path in PUBLIC:
            pg.goto(BASE + path); audit(pg, theme, f"public {path}")
        pg.goto(BASE + "/member/profile"); audit(pg, theme, "login screen (otp email step)")
        pg.fill("#login-email", "a@b.co"); pg.get_by_role("button", name=re.compile("Send Code")).click(); pg.wait_for_timeout(800)
        audit(pg, theme, "login screen (otp code step)")
        ctx.close()
        # 2) signed in member pages
        for path, scenario in MEMBER_PAGES:
            ctx = b.new_context(viewport={"width": 1100, "height": 900})
            ctx.add_init_script(f"""localStorage.setItem('nk-theme','{theme}');localStorage.setItem('nk-reg-token','{REG}');
              localStorage.setItem('nk-reg-email','a@b.co');localStorage.setItem('nk-last-activity',String(Date.now()));
              localStorage.setItem('nk-mock-npo','{scenario}')""")
            pg = ctx.new_page(); mock_routes(pg)
            pg.goto(BASE + path); audit(pg, theme, f"member {path} [{scenario}]")
            if path == "/member/membership" and scenario == "none":
                pg.locator(".mypage-option input[type=checkbox]").first.check()
                pg.fill("#spouse-email", "unknown.person@gmail.com"); pg.wait_for_timeout(900)
                audit(pg, theme, "member /member/membership [spouse not a member]")
                pg.fill("#spouse-email", "member1@x.com"); pg.wait_for_timeout(900)
                pg.locator(".mypage-option input[type=checkbox]").nth(1).check(); pg.wait_for_timeout(300)
                audit(pg, theme, "member /member/membership [spouse ok + AGM form]")
            if path == "/member/profile":
                pg.click(".nk-user-trigger"); pg.wait_for_timeout(300)
                audit(pg, theme, "member account dropdown open")
            ctx.close()
        # 3) admin dashboard, every tab + a few dialogs
        ctx = b.new_context(viewport={"width": 1280, "height": 900})
        ctx.add_init_script(f"""localStorage.setItem('nk-theme','{theme}');
          for (const k of ['adminIdToken','adminAccessToken','adminRefreshToken']) localStorage.setItem(k,'{ADM}');
          localStorage.setItem('adminEmail','admin@x.test');localStorage.setItem('nk-last-activity',String(Date.now()))""")
        pg = ctx.new_page(); mock_routes(pg)
        pg.goto(BASE + "/admin"); pg.wait_for_timeout(1500)
        tabs = pg.locator(".admin-tab"); n = tabs.count()
        for i in range(n):
            name = tabs.nth(i).inner_text().strip()
            tabs.nth(i).click(); pg.wait_for_timeout(500)
            audit(pg, theme, f"admin tab: {name}")
            if name == "Members":
                pg.get_by_role("button", name=re.compile(r"\+ Add Member")).click(); pg.wait_for_timeout(300)
                audit(pg, theme, "admin dialog: add member")
                pg.get_by_role("button", name="Close").first.click(); pg.wait_for_timeout(200)
                pg.get_by_role("button", name="NPO", exact=True).first.click(); pg.wait_for_timeout(600)
                audit(pg, theme, "admin dialog: NPO transactions (pending row)")
                pg.get_by_role("button", name=re.compile("Edit", re.I)).first.click(); pg.wait_for_timeout(300)
                audit(pg, theme, "admin dialog: NPO transaction edit form")
                pg.get_by_role("button", name="Close").first.click(); pg.wait_for_timeout(200)
            if name == "AGM":
                pg.get_by_role("button", name=re.compile(r"\+ Add AGM")).click(); pg.wait_for_timeout(300)
                audit(pg, theme, "admin: add AGM form")
                pg.get_by_role("button", name="Cancel").first.click(); pg.wait_for_timeout(200)
                pg.get_by_role("button", name="WMF Japan AGM 2026").click(); pg.wait_for_timeout(500)
                audit(pg, theme, "admin: AGM detail + registrations")
                pg.get_by_role("button", name=re.compile("All AGMs")).click(); pg.wait_for_timeout(200)
        ctx.close(); b.close()

for theme in ("dark", "light"):
    print(theme, flush=True)
    run(theme)
json.dump(findings, open(S + "/findings.json", "w"))

bad = [i for i in findings if i["fail"] and not i["disabled"]]
print(f"\n{len(bad)} failing of {len(findings)} checked (details: {S}/findings.json)")
seen = set()
for i in sorted(bad, key=lambda i: i["ratio"]):
    key = (i["theme"], i["sel"], i["fg"], i["bg"])
    if key in seen:
        continue
    seen.add(key)
    print(f'{i["ratio"]:5.2f} (need {i["need"]}) {i["theme"]:5} {i["page"]:38} {i["sel"][:34]:34} "{i["text"][:24]}" {i["fg"]} on {i["bg"]}')
sys.exit(1 if bad else 0)
