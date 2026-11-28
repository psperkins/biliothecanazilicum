// AnythingLLM custom agent skill: the drafts -> pending -> canon writing archive.
// Files live in <CANON_SYNC_DIR>/source/; every action is appended to source/ledger/ingest.log,
// the same ledger canon_sync.py writes to. Promoting into canon runs canon_sync.py.

const fs = require("fs");
const path = require("path");
const os = require("os");
const { execFile } = require("child_process");

const STAGES = ["drafts", "pending", "canon"];
const NEXT = { drafts: "pending", pending: "canon" };
const TEXT = /\.(txt|md)$/i;
// Files moved during each agent run: a file moves at most one stage per user message,
// even if the model calls promote or publish again on its own.
const promotedThisRun = new Map();
// Prompts whose replies are status messages, not writing (presets as stored, or unexpanded slash commands).
const SKIP_PROMPT = new RegExp(
  "^(@agent\\s*)?((use|call) the canon archive (skill|tool) with action (draft|revise|edit|promote|publish|retract|list)\\b" +
  "|\\/(draft|revise|edit|promote|publish|retract|audit)\\b)", "i");
const preview = (body) => {
  const lines = body.split("\n").map((l) => l.trim()).filter(Boolean);
  const line = lines.find((l) => !l.startsWith("#")) || lines[0] || "";
  return line.length > 90 ? line.slice(0, 90) + "…" : line;
};

function slug(title) {
  return String(title).toLowerCase().normalize("NFKD").replace(/[̀-ͯ]/g, "")
    .replace(/[^a-z0-9]+/g, "-").replace(/^-+|-+$/g, "").slice(0, 80) || "untitled";
}

function stamp() {
  return new Date().toISOString().replace(/\.\d{3}Z$/, "+00:00");
}

module.exports.runtime = {
  handler: async function ({ action, title, text, file, use_last_reply }) {
    const base = this.runtimeArgs?.CANON_SYNC_DIR || "/Users/patrickperkins/sites/drupalmtd/tools/canon-sync";
    const python = this.runtimeArgs?.PYTHON || "/opt/homebrew/bin/python3";
    const source = path.join(base, "source");
    const say = (msg) => this.introspect?.(`${this.caller}: ${msg}`);
    const runScript = (args) => new Promise((resolve) => {
      execFile(python, ["canon_sync.py", ...args], { cwd: base, timeout: 15 * 60 * 1000, maxBuffer: 4 * 1024 * 1024 },
        (err, stdout, stderr) => resolve({ err, log: `${stdout || ""}${stderr || ""}`.trim() }));
    });

    const ledger = (event, name, detail = "") => {
      fs.mkdirSync(path.join(source, "ledger"), { recursive: true });
      fs.appendFileSync(path.join(source, "ledger", "ingest.log"),
        `${stamp()}  ${event.padEnd(9)} ${name}${detail ? "  " + detail : ""}\n`);
    };
    const files = (stage) => {
      const dir = path.join(source, stage);
      return fs.existsSync(dir) ? fs.readdirSync(dir).filter((f) => TEXT.test(f) && !f.startsWith(".")).sort() : [];
    };
    // Find a file by exact name, or by name without extension / slugged title, in any stage.
    const locate = (name) => {
      if (!name) return null;
      const wanted = path.basename(String(name));
      const loose = slug(wanted.replace(TEXT, ""));
      for (const stage of STAGES) {
        for (const f of files(stage)) {
          if (f === wanted || slug(f.replace(TEXT, "")) === loose) return { stage, name: f, full: path.join(source, stage, f) };
        }
      }
      return null;
    };
    // The Librarian's most recent real reply in this chat thread, read from AnythingLLM's own database,
    // so long text never has to be copied into a tool call. Replies to archive commands that change or
    // list files (draft, revise, edit, promote, publish, retract, audit) are skipped; replies to /read and
    // to free-form requests ("editorialize it") count.
    const lastReply = () => new Promise((resolve) => {
      const inv = this.super?.handlerProps?.invocation || {};
      const workspace = Number(inv.workspace_id);
      if (!workspace) return resolve(null);
      const thread = inv.thread_id ? `= ${Number(inv.thread_id)}` : "IS NULL";
      const storage = process.env.STORAGE_DIR ||
        path.join(os.homedir(), "Library", "Application Support", "anythingllm-desktop", "storage");
      const sql = `SELECT id, prompt, response FROM workspace_chats WHERE workspaceId = ${workspace} ` +
        `AND thread_id ${thread} AND include = 1 ORDER BY id DESC LIMIT 25;`;
      execFile("/usr/bin/sqlite3", ["-readonly", "-json", path.join(storage, "anythingllm.db"), sql],
        { maxBuffer: 64 * 1024 * 1024 }, (err, stdout) => {
          if (err) return resolve(null);
          let rows = [];
          try { rows = JSON.parse(stdout || "[]"); } catch { return resolve(null); }
          for (const row of rows) {
            const prompt = String(row.prompt || "").trim();
            if (SKIP_PROMPT.test(prompt)) continue;
            let reply = "";
            try { reply = JSON.parse(row.response || "{}").text || ""; } catch { continue; }
            reply = reply.replace(/<think>[\s\S]*?<\/think>/gi, "").replace(/^[\s\S]*<\/think>/i, "").trim();
            if (reply) return resolve(reply);
          }
          resolve(null);
        });
    });
    // Canon entries synced into the workspace, read from canon_sync.sqlite (one row per document in
    // AnythingLLM's bibliotheca-canon folder). Matches a title (exact, then loosely), or a document file
    // name as shown under Upload Documents. A leading type word ("song The Crystal Caves") narrows it.
    const canonDb = (sql) => new Promise((resolve, reject) => {
      execFile("/usr/bin/sqlite3", ["-readonly", "-json", path.join(base, "canon_sync.sqlite"), sql],
        { maxBuffer: 64 * 1024 * 1024 }, (err, stdout) => {
          if (err) return reject(new Error(`could not read canon_sync.sqlite (${err.message.trim()})`));
          try { resolve(JSON.parse(stdout || "[]")); } catch { resolve([]); }
        });
    });
    const recall = async (query) => {
      const raw = String(query || "").trim().replace(/^["'“”]+|["'“”]+$/g, "");
      if (!raw) return "Recall needs a title or document name.";
      const rows = await canonDb("SELECT key, type, title, url, location FROM docs;");
      if (!rows.length) return "Nothing is synced yet. Run canon_sync.py first.";
      const types = [...new Set(rows.map((r) => r.type))];
      const asType = raw.toLowerCase().replace(/s$/, "").replace(/^(all|list)\s+/, "");
      if (types.includes(asType)) {
        const titles = rows.filter((r) => r.type === asType).map((r) => r.title).sort((a, b) => a.localeCompare(b));
        return `${titles.length} canon ${asType} entries:\n${titles.map((t) => `- ${t}`).join("\n")}`;
      }
      const uuidTail = /-[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/;
      const find = (q, pool) => {
        const file = path.basename(q).replace(/\.json$/i, "");
        const byFile = pool.filter((r) => path.basename(r.location || "", ".json") === file);
        if (byFile.length) return byFile;
        const s = slug(file.replace(uuidTail, "").replace(/^raw-/, ""));
        const exact = pool.filter((r) => String(r.title).toLowerCase() === q.toLowerCase());
        if (exact.length) return exact;
        const same = pool.filter((r) => slug(r.title) === s);
        if (same.length) return same;
        const words = s.split("-").filter(Boolean);
        return pool.filter((r) => { const t = `-${slug(r.title)}-`; return words.every((w) => t.includes(`-${w}-`)); });
      };
      let hits = [];
      const m = raw.match(/^(\w+)[\s:]+(.+)$/);
      if (m && types.includes(m[1].toLowerCase())) hits = find(m[2].trim(), rows.filter((r) => r.type === m[1].toLowerCase()));
      if (!hits.length) hits = find(raw, rows);
      if (!hits.length) return `No canon entry matches '${raw}'. Recall a type on its own (song, fragment, annotation, album, collection, heading, section, manuscript) to list its titles.`;
      if (hits.length > 1) {
        const shown = hits.slice(0, 25).map((r) => `- ${r.type}: ${r.title}`).join("\n");
        return `'${raw}' matches ${hits.length} canon entries. Ask the user which one (they can put the type first, ` +
          `e.g. "${hits[0].type} ${hits[0].title}"):\n${shown}${hits.length > 25 ? "\n…" : ""}`;
      }
      const key = String(hits[0].key).replace(/'/g, "''");
      const [doc] = await canonDb(`SELECT type, title, url, location, text FROM docs WHERE key = '${key}';`);
      return `Canon entry (${doc.type}) "${doc.title}"${doc.url ? ` — ${doc.url}` : ""}\n` +
        `Document: ${doc.location}\n\n${doc.text}`;
    };
    const notFound = (name) => `No file named '${name}' in drafts, pending or canon. ` + listing();
    const listing = () => STAGES.map((s) => `${s}: ${files(s).join(", ") || "(empty)"}`).join("\n");

    try {
      STAGES.concat("ledger").forEach((s) => fs.mkdirSync(path.join(source, s), { recursive: true }));

      if (action === "list") return listing();

      if (action === "read") {
        const hit = locate(file);
        if (!hit) return notFound(file);
        return `${hit.stage}/${hit.name}:\n\n${fs.readFileSync(hit.full, "utf8")}`;
      }

      if (action === "recall") return await recall(file || title);

      if (action === "draft") {
        if (!title) return "To start a draft I need a title.";
        // A title given as a file name ("to-bend-the-law.md") becomes "To Bend The Law".
        if (TEXT.test(String(title).trim()) || /^[a-z0-9]+(-[a-z0-9]+)+$/.test(String(title).trim())) {
          title = String(title).trim().replace(TEXT, "").replace(/[-_]+/g, " ").replace(/\b\w/g, (c) => c.toUpperCase());
        }
        const name = `${slug(title)}.md`;
        const existing = locate(name);
        if (existing) return `${existing.stage}/${existing.name} already exists. Use action revise to change it, or pick another title.`;
        let fromReply = false;
        if (use_last_reply || !String(text || "").trim()) {
          text = await lastReply();
          fromReply = true;
          if (!text) return "Nothing was saved: I could not find an earlier reply in this chat to save. Paste the text after /draft instead.";
        }
        let body = String(text).trim();
        const [first, ...rest] = body.split("\n");
        if (first.replace(/^(title:|#)\s*/i, "").trim().toLowerCase() === String(title).trim().toLowerCase()) {
          body = rest.join("\n").trim();  // The title was repeated as the text's first line.
        }
        fs.writeFileSync(path.join(source, "drafts", name), `# ${String(title).trim()}\n\n${body}\n`);
        ledger("drafted", `drafts/${name}`, `"${String(title).trim()}"${fromReply ? " (from previous reply)" : ""}`);
        say(`saved drafts/${name}`);
        return `Saved new draft drafts/${name} (${body.length} characters${fromReply ? ", taken from your previous reply" : ""}) ` +
          `and recorded it in the ledger. It begins: "${preview(body)}". The user can open it with /edit ${name}. Tell the user this exactly.`;
      }

      if (action === "revise") {
        const hit = locate(file);
        if (!hit) return notFound(file);
        if (hit.stage === "canon") return `${hit.name} is in canon waiting to sync. Promote it or edit it by hand.`;
        let fromReply = false;
        if (use_last_reply || !String(text || "").trim()) {
          text = await lastReply();
          fromReply = true;
          if (!text) return "Nothing was changed: I could not find an earlier reply in this chat to use. Paste the new text after /revise instead.";
        }
        const old = fs.readFileSync(hit.full, "utf8");
        // Keep the file's title line (Markdown '# Heading' or 'Title: …') unless the new text brings its own.
        const header = old.match(/^(Title:.*|# .*)\n/i);
        const body = String(text).trim();
        fs.writeFileSync(hit.full, (/^(title:|# )/i.test(body) || !header ? "" : header[0] + "\n") + body + "\n");
        ledger("revised", `${hit.stage}/${hit.name}`, fromReply ? "(from previous reply)" : "");
        say(`revised ${hit.stage}/${hit.name}`);
        return `Revised ${hit.stage}/${hit.name} (${body.length} characters${fromReply ? ", taken from your previous reply" : ""}) ` +
          `and recorded it in the ledger. It now begins: "${preview(body)}". Tell the user this exactly.`;
      }

      if (action === "promote" || action === "publish") {
        const hit = locate(file);
        if (!hit) return notFound(file);
        // One agent session can span several user messages, so key on the session and message count.
        const userTurns = (this.super?._chats || []).filter((c) => c.from === "USER").length;
        const run = `${this.super?.handlerProps?.invocation?.uuid || "run"}:${userTurns}`;
        const done = promotedThisRun.get(run) || new Set();
        if (done.has(hit.name)) {
          return `${hit.name} was already moved once in this request and is now in ${hit.stage}. ` +
            "Files move one stage per request. Do not call promote or publish again; tell the user where the file is now.";
        }
        // promote: drafts -> pending only. publish: pending -> canon only.
        if (action === "promote" && hit.stage !== "drafts") {
          return hit.stage === "pending"
            ? `${hit.name} is in pending. Nothing was moved. Moving it into canon is done with publish, which the user must ask for (/publish ${hit.name}).`
            : `${hit.name} is already in canon. Nothing was moved.`;
        }
        if (action === "publish" && hit.stage !== "pending") {
          return hit.stage === "drafts"
            ? `${hit.name} is still in drafts. Nothing was moved. It must be promoted to pending first (/promote ${hit.name}).`
            : `${hit.name} is already in canon. Nothing was moved; run canon_sync.py if it has not been ingested.`;
        }
        const to = NEXT[hit.stage];
        const dest = path.join(source, to, hit.name);
        const replaced = fs.existsSync(dest);
        fs.renameSync(hit.full, dest);
        done.add(hit.name);
        promotedThisRun.set(run, done);
        ledger(action === "publish" ? "published" : "promoted", `${hit.stage}/${hit.name} -> ${to}/`,
          replaced ? "(replaced older copy)" : "");
        say(`moved ${hit.name} to ${to}`);
        if (action === "promote") {
          return `Done: moved ${hit.name} from drafts to pending. This request is complete. ` +
            `Do not move it further; it stays in pending until the user publishes it (/publish ${hit.name}).`;
        }

        say("running canon_sync.py to ingest canon (this can take a minute)");
        const out = await runScript([]);
        if (out.err) {
          ledger("syncfail", `canon/${hit.name}`, out.err.message.split("\n")[0]);
          return `Moved ${hit.name} into canon, but the sync failed, so it is not in the workspace yet:\n${out.log.slice(-1500)}`;
        }
        return `Published ${hit.name} into canon and synced it into the workspace.\n${out.log.slice(-800)}`;
      }

      if (action === "edit") {
        // Open the file in Obsidian. With a vault name it opens in that vault (relative to source/);
        // without one Obsidian picks the vault that contains the path.
        const hit = locate(file);
        if (!hit) return notFound(file);
        const vault = (this.runtimeArgs?.OBSIDIAN_VAULT ?? "source").trim();
        const url = vault
          ? `obsidian://open?vault=${encodeURIComponent(vault)}&file=${encodeURIComponent(`${hit.stage}/${hit.name}`)}`
          : `obsidian://open?path=${encodeURIComponent(hit.full)}`;
        const err = await new Promise((resolve) => execFile("/usr/bin/open", [url], (e) => resolve(e)));
        if (err) return `Could not open ${hit.stage}/${hit.name} in Obsidian: ${err.message}`;
        say(`opened ${hit.stage}/${hit.name} in Obsidian`);
        const note = hit.stage === "canon" ? " It is in canon, so edits there are picked up by the next sync." : "";
        return `Opened ${hit.stage}/${hit.name} in Obsidian. Nothing was moved or changed.${note}`;
      }

      if (action === "retract") {
        // Same as `python3 canon_sync.py retract <file>`: the script checks the name and refuses
        // while the file is still in source/canon, and it writes the ledger entry itself.
        if (!file) return "To retract I need the manuscript's file name, e.g. the-oculus-draconis.md.";
        // The script matches the name with or without its extension.
        const name = path.basename(String(file).trim());
        say(`running canon_sync.py retract ${name}`);
        const out = await runScript(["retract", name]);
        if (out.err) {
          const hint = /still exists/.test(out.log) ? " If it has already been ingested, run `python3 canon_sync.py gc` to clear it from canon, then retract again." : "";
          return `Nothing was retracted. ${out.log.slice(-1500)}${hint}`;
        }
        return `Retracted ${name}: it is no longer in the workspace.\n${out.log.slice(-800)}`;
      }

      return `Unknown action '${action}'. Use draft, revise, edit, promote, publish, retract, list or read.`;
    } catch (e) {
      return `The canon archive action failed: ${e.message}`;
    }
  },
};
