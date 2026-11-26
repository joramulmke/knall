/*
 * Knall im Fediverse — minimaler ActivityPub-Dienst (Worker-Skript).
 *
 * Kann: gefunden werden (WebFinger), Profil zeigen, Follows annehmen und
 * beenden, gespeicherte Posts ausliefern. Zugestellt werden neue Posts von
 * der Pipeline (scripts/fediverse.py) — der Dienst selbst signiert nur das
 * "Accept" auf einen Follow und eigene Abrufe (wenig Rechenzeit).
 *
 * Bindungen (siehe fediverse/README.md):
 *   KNALL          Key-Value-Speicher (Follower, Posts)
 *   SITE_URL       öffentliche Adresse der Seite, mit / am Ende
 *   PUBLIC_KEY     öffentlicher Schlüssel (PEM)
 *   PRIVATE_KEY    privater Schlüssel (PEM, PKCS#8) — geheim
 *   PUBLISH_TOKEN  Zugangstoken der Pipeline — geheim
 */
const USER = "knall";
const AS = "application/activity+json";
const PUBLIC = "https://www.w3.org/ns/activitystreams#Public";
const CTX = ["https://www.w3.org/ns/activitystreams", "https://w3id.org/security/v1"];
const NAME = "Knall — Das linke Boulevard";
const SUMMARY = "<p>Linke Nachrichten im Boulevard-Format: Arbeit, Miete, Pflege, Klima "
  + "und Ostdeutschland. Automatisch aus verlinkten Quellen erstellt und mehrstufig geprüft.</p>";
const MAX_BODY = 200000;
const MAX_OUTBOX = 20;

export default {
  async fetch(req, env, ctx) {
    const url = new URL(req.url);
    const base = url.origin;
    const p = url.pathname.replace(/\/+$/, "") || "/";
    try {
      if (p === "/.well-known/webfinger") return webfinger(url, base, env);
      if (p === "/inbox" || p === `/users/${USER}/inbox`) {
        return req.method === "POST" ? await inbox(req, env, base, ctx) : status(405);
      }
      if (p === "/api/notes" && req.method === "POST") return await apiNotes(req, env, base);
      if (p === "/api/inboxes" && req.method === "GET") return await apiInboxes(req, env);
      if (req.method !== "GET" && req.method !== "HEAD") return status(405);
      if (p === "/" || p === "/actor" || p === `/@${USER}` || p === `/users/${USER}`) {
        return wantsHtml(req) ? Response.redirect(env.SITE_URL, 302) : json(actor(base, env));
      }
      if (p === "/outbox") return json(await outbox(env, base));
      if (p === "/followers") return json(await followers(env, base));
      const m = p.match(/^\/notes\/([a-z0-9-]{1,120})$/);
      if (m) {
        const note = await env.KNALL.get("n:" + m[1], "json");
        if (!note) return status(404);
        if (wantsHtml(req)) return Response.redirect(note.url || env.SITE_URL, 302);
        return json(note);
      }
      return status(404);
    } catch (err) {
      console.log("Fehler:", err && err.stack || err);
      return status(500);
    }
  },
};

// ---------- Antworten ----------
function status(code) { return new Response(null, { status: code }); }
function json(obj, type = AS, code = 200) {
  return new Response(JSON.stringify(obj), {
    status: code,
    headers: { "Content-Type": `${type}; charset=utf-8`, "Access-Control-Allow-Origin": "*" },
  });
}
function wantsHtml(req) {
  const a = req.headers.get("accept") || "";
  return a.includes("text/html") && !/activity\+json|ld\+json/.test(a);
}
function esc(t) {
  return String(t ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}
function idOf(x) { return typeof x === "string" ? x : x && x.id; }
function httpsUrl(u) { return typeof u === "string" && /^https:\/\/[^\s]+$/i.test(u) ? u : null; }

// ---------- Profil ----------
function actor(base, env) {
  const site = env.SITE_URL;
  return {
    "@context": CTX,
    id: `${base}/actor`,
    type: "Service", // in Mastodon als Bot-Account gekennzeichnet
    preferredUsername: USER,
    name: NAME,
    summary: SUMMARY,
    url: site,
    inbox: `${base}/inbox`,
    outbox: `${base}/outbox`,
    followers: `${base}/followers`,
    endpoints: { sharedInbox: `${base}/inbox` },
    manuallyApprovesFollowers: false,
    discoverable: true,
    indexable: true,
    icon: { type: "Image", mediaType: "image/png", url: `${site}icon.png` },
    image: { type: "Image", mediaType: "image/png", url: `${site}og.png` },
    attachment: [{ type: "PropertyValue", name: "Website", value: `<a href="${esc(site)}" rel="me">${esc(site)}</a>` }],
    publicKey: { id: `${base}/actor#main-key`, owner: `${base}/actor`, publicKeyPem: env.PUBLIC_KEY },
  };
}

function webfinger(url, base, env) {
  const host = new URL(base).host;
  const res = (url.searchParams.get("resource") || "").toLowerCase();
  const ok = [`acct:${USER}@${host}`.toLowerCase(), `${base}/actor`.toLowerCase()];
  if (!ok.includes(res)) return status(404);
  return json({
    subject: `acct:${USER}@${host}`,
    aliases: [`${base}/actor`],
    links: [
      { rel: "self", type: AS, href: `${base}/actor` },
      { rel: "http://webfinger.net/rel/profile-page", type: "text/html", href: env.SITE_URL },
    ],
  }, "application/jrd+json");
}

async function outbox(env, base) {
  const ids = (await env.KNALL.get("outbox", "json")) || [];
  const notes = (await Promise.all(ids.map((i) => env.KNALL.get("n:" + i, "json")))).filter(Boolean);
  return {
    "@context": "https://www.w3.org/ns/activitystreams",
    id: `${base}/outbox`,
    type: "OrderedCollection",
    totalItems: notes.length,
    orderedItems: notes.map((n) => create(base, n)),
  };
}

async function followers(env, base) {
  const n = Number(await env.KNALL.get("fcount")) || 0;
  return { "@context": "https://www.w3.org/ns/activitystreams", id: `${base}/followers`, type: "OrderedCollection", totalItems: n };
}

// ---------- Posts ----------
function note(base, nid, it) {
  const url = httpsUrl(it.url);
  const content = `<p><strong>${esc(it.titel)}</strong></p><p>${esc(it.teaser)}</p>`
    + (url ? `<p><a href="${esc(url)}">${esc(url)}</a></p>` : "");
  const published = /^\d{4}-\d{2}-\d{2}T[\d:]+Z$/.test(it.published || "") ? it.published : new Date().toISOString().replace(/\.\d+Z$/, "Z");
  return {
    "@context": "https://www.w3.org/ns/activitystreams",
    id: `${base}/notes/${nid}`,
    type: "Note",
    attributedTo: `${base}/actor`,
    published,
    to: [PUBLIC],
    cc: [`${base}/followers`],
    content,
    contentMap: { de: content },
    url: url || undefined,
    sensitive: false,
  };
}

function create(base, n) {
  return {
    "@context": "https://www.w3.org/ns/activitystreams",
    id: `${n.id}/create`,
    type: "Create",
    actor: `${base}/actor`,
    published: n.published,
    to: n.to,
    cc: n.cc,
    object: n,
  };
}

function authorized(req, env) {
  const t = env.PUBLISH_TOKEN;
  return !!t && t.length >= 20 && req.headers.get("authorization") === `Bearer ${t}`;
}

// Pipeline: neue Posts speichern; Antwort enthält nur die wirklich neuen
// "Create"-Aktivitäten, die die Pipeline dann an alle Follower zustellt.
async function apiNotes(req, env, base) {
  if (!authorized(req, env)) return status(401);
  const items = await req.json();
  if (!Array.isArray(items)) return status(400);
  let ids = (await env.KNALL.get("outbox", "json")) || [];
  const neu = [];
  for (const it of items.slice(0, 20)) {
    const nid = String(it && it.id || "").toLowerCase().replace(/[^a-z0-9-]/g, "").slice(0, 120);
    if (!nid || ids.includes(nid) || (await env.KNALL.get("n:" + nid))) continue;
    const n = note(base, nid, it);
    await env.KNALL.put("n:" + nid, JSON.stringify(n));
    ids = [nid, ...ids].slice(0, MAX_OUTBOX);
    neu.push(create(base, n));
  }
  if (neu.length) await env.KNALL.put("outbox", JSON.stringify(ids));
  return json({ neu, keyId: `${base}/actor#main-key` }, "application/json");
}

async function apiInboxes(req, env) {
  if (!authorized(req, env)) return status(401);
  const inboxes = new Set();
  let cursor;
  do {
    const r = await env.KNALL.list({ prefix: "f:", cursor });
    for (const k of r.keys) if (k.metadata && httpsUrl(k.metadata.inbox)) inboxes.add(k.metadata.inbox);
    cursor = r.list_complete ? undefined : r.cursor;
  } while (cursor);
  return json({ inboxes: [...inboxes] }, "application/json");
}

// ---------- Posteingang ----------
async function inbox(req, env, base, ctx) {
  const body = await req.text();
  if (body.length > MAX_BODY) return status(413);
  let a;
  try { a = JSON.parse(body); } catch { return status(400); }
  // Alles außer Follow/Undo wird ohne Prüfung quittiert und verworfen
  // (Mastodon schickt z. B. massenhaft "Delete" gelöschter Accounts).
  if (!a || (a.type !== "Follow" && a.type !== "Undo")) return status(202);
  const who = idOf(a.actor);
  if (!httpsUrl(who)) return status(400);
  if (!(await verify(req, body, who, env, base))) return status(401);

  if (a.type === "Follow" && idOf(a.object) === `${base}/actor`) {
    const r = await signedFetch(env, base, who, "GET");
    if (!r.ok) return status(502);
    const prof = await r.json();
    const own = httpsUrl(prof.inbox);
    const shared = httpsUrl(prof.endpoints && prof.endpoints.sharedInbox) || own;
    if (!own || prof.id !== who) return status(400);
    const neu = !(await env.KNALL.get("f:" + who));
    await env.KNALL.put("f:" + who, "1", { metadata: { inbox: shared } });
    if (neu) await env.KNALL.put("fcount", String((Number(await env.KNALL.get("fcount")) || 0) + 1));
    const accept = {
      "@context": "https://www.w3.org/ns/activitystreams",
      id: `${base}/accept/${crypto.randomUUID()}`,
      type: "Accept",
      actor: `${base}/actor`,
      object: a,
    };
    ctx.waitUntil(signedFetch(env, base, own, "POST", JSON.stringify(accept)).catch(() => {}));
  } else if (a.type === "Undo" && a.object && a.object.type === "Follow" && idOf(a.object.actor) === who) {
    if (await env.KNALL.get("f:" + who)) {
      await env.KNALL.delete("f:" + who);
      await env.KNALL.put("fcount", String(Math.max(0, (Number(await env.KNALL.get("fcount")) || 1) - 1)));
    }
  }
  return status(202);
}

// ---------- HTTP-Signaturen (draft-cavage, rsa-sha256) ----------
const ALG = { name: "RSASSA-PKCS1-v1_5", hash: "SHA-256" };
const enc = (s) => new TextEncoder().encode(s);
function b64(buf) {
  let s = "";
  for (const c of new Uint8Array(buf)) s += String.fromCharCode(c);
  return btoa(s);
}
function unb64(s) { return Uint8Array.from(atob(s), (c) => c.charCodeAt(0)); }
function pemBody(pem) { return unb64(String(pem).replace(/-----[^-]+-----/g, "").replace(/\s+/g, "")); }

let privKey;
async function privateKey(env) {
  privKey = privKey || await crypto.subtle.importKey("pkcs8", pemBody(env.PRIVATE_KEY), ALG, false, ["sign"]);
  return privKey;
}

async function signedFetch(env, base, target, method, body) {
  const u = new URL(target);
  const date = new Date().toUTCString();
  const headers = { Date: date, Accept: AS };
  let names = "(request-target) host date";
  let str = `(request-target): ${method.toLowerCase()} ${u.pathname}${u.search}\nhost: ${u.host}\ndate: ${date}`;
  if (body !== undefined) {
    const digest = "SHA-256=" + b64(await crypto.subtle.digest("SHA-256", enc(body)));
    headers.Digest = digest;
    headers["Content-Type"] = AS;
    names += " digest";
    str += `\ndigest: ${digest}`;
  }
  const sig = b64(await crypto.subtle.sign(ALG, await privateKey(env), enc(str)));
  headers.Signature = `keyId="${base}/actor#main-key",algorithm="rsa-sha256",headers="${names}",signature="${sig}"`;
  return fetch(target, { method, headers, body, redirect: "manual" });
}

async function verify(req, body, who, env, base) {
  const h = req.headers.get("signature");
  if (!h) return false;
  const f = {};
  for (const m of h.matchAll(/(\w+)="([^"]*)"/g)) f[m[1]] = m[2];
  const names = (f.headers || "date").toLowerCase().split(/\s+/);
  if (!f.keyId || !f.signature || !names.includes("(request-target)") || !names.includes("digest")) return false;
  const digest = req.headers.get("digest") || "";
  const expect = b64(await crypto.subtle.digest("SHA-256", enc(body)));
  if (digest.replace(/^sha-256=/i, "") !== expect) return false;
  const date = Date.parse(req.headers.get("date") || "");
  if (!(Math.abs(Date.now() - date) < 12 * 3600 * 1000)) return false;
  const url = new URL(req.url);
  const str = names.map((n) => n === "(request-target)"
    ? `(request-target): ${req.method.toLowerCase()} ${url.pathname}${url.search}`
    : `${n}: ${req.headers.get(n) ?? ""}`).join("\n");

  const keyUrl = httpsUrl(f.keyId.split("#")[0]);
  if (!keyUrl || new URL(keyUrl).host !== new URL(who).host) return false;
  const r = await signedFetch(env, base, keyUrl, "GET");
  if (!r.ok) return false;
  const doc = await r.json();
  const keys = [].concat(doc.publicKey || (doc.publicKeyPem ? doc : []));
  const pk = keys.find((k) => k && k.id === f.keyId) || keys[0];
  if (!pk || !pk.publicKeyPem || (pk.owner || doc.id) !== who) return false;
  const key = await crypto.subtle.importKey("spki", pemBody(pk.publicKeyPem), ALG, false, ["verify"]);
  return crypto.subtle.verify(ALG, key, unb64(f.signature), enc(str));
}
