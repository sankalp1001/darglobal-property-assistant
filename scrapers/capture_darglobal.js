// DarGlobal capture script — run in the DevTools console of a normal browser
// tab that is already open on https://darglobal.co.uk (so the site has loaded
// normally for you). It fetches each page same-origin, 1.5 s apart, reads the
// page's own Next.js data (__NEXT_DATA__), condenses it, and returns JSON for
// data/raw/darglobal/_projects.json and _index_and_pages.json.
//
// Why: darglobal.co.uk serves an Imperva/Incapsula JavaScript challenge to
// non-browser HTTP clients. We don't try to defeat that from a script; the
// pages are read the same way a visitor's browser reads them, at a slow rate.
//
// Page list: project + company pages from https://darglobal.co.uk/sitemap.xml
// (blog/press/insights/partners/legal/forms excluded).

(async () => {
  const PATHS = ["/about","/faq","/why-invest","/projects","/hospitality","/development-management","/commercial","/luxury-golf-communities","/tokenization","/aida-360","/one-of-one","/aida-trump-international-apartments","/La-Collina","/the-mulliner","/fairway-villas-aida-oman","/amour-sans-detour","/marriott-residences-aida-oman","/trump-tower-jeddah","/the-great-escape-1-aida-oman","/the-great-escape-2-aida-oman","/aida-coastal-investment-villas","/amour-sans-detour-2","/aida-trump-international-hotel","/aida-trump-international-cliff-villas","/aida-trump-villas","/Sunrise-Haven-Luxury-Villas","/les-vagues","/neptune","/tierra-viva","/w-residences","/davinci-tower-by-pagani","/dg1","/trump-international-hotel-and-tower-dubai","/d-villas-at-jge","/urban-oasis-by-missoni","/marea","/the-astera","/one-of-one/one-of-one-unlisted-by-pagani","/one-of-one/project-pagani","/one-of-one/THE-ALBERT-HALL-MANSION","/one-of-one/painite-villas-design-inspired-by-Automobili-Lamborghini","/one-of-one/top-of-the-astera","/one-of-one/trump-penthouse","/one-of-one/fendi-casa-azure-oceanfront-villas","/rayana/trump-mansions","/rayana/trump-international-qatar","/rayana/darglobal-mansions","/rayana/darglobal-mansions-fendi-casa","/amaya/amaya-plots","/amaya/padel-living","/trump-plaza/executive-residences","/trump-plaza/park-residences","/sohar-islamic","/exclusive-plots","/invest-in-aida"];
  const INFO = new Set(["/about","/faq","/why-invest","/hospitality","/development-management","/commercial","/luxury-golf-communities","/tokenization","/aida-360","/one-of-one"]);
  const SKIP = new Set(["globalData","otherProjectData","thankyouPageData","localizations","versions","SEO","PhotoGallery","ProgressUpdate","galleryList","CountryList","LiveFeed","View360","image","Image","images","media","desktopThumbnail","mobileThumbnail","HeroBanner","HeroBannerMobile","icon","logo","video","formats"]);
  const strip = s => s.replace(/<[^>]+>/g," ").replace(/&nbsp;/g," ").replace(/&amp;/g,"&").replace(/\s+/g," ").trim();
  const T = (s,n) => s && s.length > n ? s.slice(0,n) + "…" : s;
  const sleep = ms => new Promise(r => setTimeout(r, ms));

  // Flatten pageProps into [keyPath, text] pairs, skipping media/SEO noise.
  function flat(pp) {
    const out = [], seen = new Set();
    const clean = p => p.replace(/\.attributes/g,"").replace(/\.data(?=\.|\[|$)/g,"");
    (function walk(o, p) {
      if (Array.isArray(o)) {
        if (o.length && o.every(x => x && typeof x === "object" && ["paragraph","heading","list"].includes(x.type))) {
          const t = strip((JSON.stringify(o).match(/"text":"((?:[^"\\]|\\.)*)"/g) || []).map(x => JSON.parse("{"+x+"}").text).join(" "));
          if (t && !seen.has(t)) { seen.add(t); out.push([clean(p), t]); }
          return;
        }
        o.forEach((v,i) => walk(v, `${p}[${i}]`)); return;
      }
      if (o && typeof o === "object") {
        if (typeof o.name === "string" && ["string","number"].includes(typeof o.value) && Object.keys(o).length <= 4) {
          out.push([clean(p), `${strip(o.name)}: ${strip(String(o.value))}`]); return;
        }
        for (const k in o) if (!SKIP.has(k)) walk(o[k], p ? `${p}.${k}` : k);
        return;
      }
      if (typeof o === "number" && /price/i.test(p)) { out.push([clean(p), o]); return; }
      if (typeof o !== "string") return;
      const k = p.split(".").pop();
      if (/^(id|vuid|slug|locale|href|url|target|createdAt|updatedAt|publishedAt|ButtonTheme|alt|mime|ext|hash|provider|events|label|caption|previewUrl|width|height)$/i.test(k)) return;
      const s = strip(o);
      if (s.length < 2 || /^(https?:|\/)/.test(s) || /^\d{4}-\d\d-\d\dT/.test(s) || seen.has(s)) return;
      seen.add(s); out.push([clean(p), s]);
    })(pp, "");
    return out;
  }

  const cap = {};
  for (const p of PATHS) {
    const html = await (await fetch(p, { credentials: "include" })).text();
    const m = html.match(/<script id="__NEXT_DATA__"[^>]*>([\s\S]*?)<\/script>/);
    if (m) cap[p] = flat(JSON.parse(m[1]).props.pageProps); else console.warn("no data", p);
    await sleep(1500);
  }

  const g = (arr, re) => arr.filter(([k]) => re.test(k)).map(([,v]) => v);
  const cards = {}, tabs = {};
  for (const [k,v] of cap["/projects"] || []) {
    const m = k.match(/Tabs\[(\d+)\]\.TabData\[(\d+)\]\.(\w+)$/); if (m) (cards[m[1]+"_"+m[2]] ||= {})[m[3]] = v;
    const t = k.match(/Tabs\[(\d+)\]\.TabHeading$/); if (t) tabs[t[1]] = v;
  }
  const cardList = Object.entries(cards).map(([id,o]) => ({ tab: tabs[id.split("_")[0]], title: o.ProjectTitle, location: o.Location, type: o.ProjectType, completion: o.completionDate, bedrooms: o.Bedrooms, desc: T(o.ProjectDescription, 300) }));

  const projects = {}, pages = {};
  for (const [p, arr] of Object.entries(cap)) {
    if (p.startsWith("/projects")) continue;
    if (INFO.has(p)) {
      let n = 0;
      pages[p] = arr.filter(([k,v]) => typeof v === "string" && v.length >= 20 && !/name$|alternativeText|circleColor|__component|imagePosition/.test(k)).map(([,v]) => v).filter(t => (n += t.length) < 5000);
      continue;
    }
    const q = g(arr, /faqs\[\d+\]\.question$/), a = g(arr, /faqs\[\d+\]\.answer$/);
    const rec = {
      title: g(arr, /(projectDetailsData|pageData)\.title$/i)[0] || g(arr, /ProjectBanner\.Heading$/)[0],
      location: g(arr, /ProjectBanner\.Location$/)[0], price: g(arr, /price/i)[0],
      aboutTitle: g(arr, /AboutProject\.title$/)[0], about: T(g(arr, /AboutProject\.(richDescription|description)$/).join(" "), 1100),
      details: g(arr, /AboutDetailsType\[\d+\]$/), amenities: g(arr, /amenitiesList\[\d+\]$/).map(x => x.split(": ")[0]),
      nearby: arr.filter(([k]) => /keyfeaturelist\[\d+\]\.(Title|Duration)$/.test(k)).map(([,v]) => v).join(" | "),
      locationDesc: T(g(arr, /loaction\.description$/)[0], 500),
      faqs: q.slice(0,6).map((qq,i) => [qq, T(a[i] || "", 320)]),
    };
    if (!rec.about) rec.fallback = T(arr.filter(([k,v]) => typeof v === "string" && v.length >= 40).map(([,v]) => v).join(" "), 1100);
    for (const k in rec) if (rec[k] == null || rec[k] === "" || (Array.isArray(rec[k]) && !rec[k].length)) delete rec[k];
    projects[p] = rec;
  }
  window.__darglobal = { projects, cards: cardList, pages };
  console.log("done: copy(JSON.stringify(window.__darglobal))");
})();
