/* Web Worker: loads a Hunspell dictionary with Typo.js and checks words off the UI thread. */
/* global Typo */
importScripts("/static/vendor/typo/typo.js");

let dict = null;
let lang = null;
const cache = new Map();

self.onmessage = async (e) => {
  const msg = e.data;
  if (msg.type === "load") {
    if (lang === msg.lang && dict) { self.postMessage({ type: "loaded", lang }); return; }
    try {
      const base = `/static/vendor/dictionaries/${msg.lang}/${msg.lang}`;
      const [aff, dic] = await Promise.all([fetch(base + ".aff").then((r) => r.text()), fetch(base + ".dic").then((r) => r.text())]);
      dict = new Typo(msg.lang, aff, dic, { platform: "any" });
      lang = msg.lang;
      cache.clear();
      self.postMessage({ type: "loaded", lang });
    } catch (err) {
      self.postMessage({ type: "error", message: String(err) });
    }
  } else if (msg.type === "check") {
    if (!dict) return;
    const bad = [];
    for (const w of msg.words) {
      let ok = cache.get(w);
      if (ok === undefined) {
        ok = dict.check(w) || dict.check(w.toLowerCase()) || (w.endsWith("'s") && dict.check(w.slice(0, -2)));
        cache.set(w, ok);
      }
      if (!ok) bad.push(w);
    }
    self.postMessage({ type: "checked", id: msg.id, words: msg.words, bad });
  } else if (msg.type === "suggest") {
    const s = dict ? dict.suggest(msg.word, 6) : [];
    self.postMessage({ type: "suggestions", id: msg.id, word: msg.word, suggestions: s });
  }
};
