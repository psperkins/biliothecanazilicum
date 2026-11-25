// AnythingLLM custom agent skill: the drafts -> pending -> canon writing archive.
// Files live in <CANON_SYNC_DIR>/source/; every action is appended to source/ledger/ingest.log,
// the same ledger canon_sync.py writes to. Promoting into canon runs canon_sync.py.

const fs = require("fs");
const path = require("path");
const { execFile } = require("child_process");

const STAGES = ["drafts", "pending", "canon"];
const NEXT = { drafts: "pending", pending: "canon" };
const TEXT = /\.(txt|md)$/i;
// Files moved during each agent run: a file moves at most one stage per user message,
// even if the model calls promote or publish again on its own.
const promotedThisRun = new Map();

function slug(title) {
  return String(title).toLowerCase().normalize("NFKD").replace(/[̀-ͯ]/g, "")
    .replace(/[^a-z0-9]+/g, "-").replace(/^-+|-+$/g, "").slice(0, 80) || "untitled";
}

function stamp() {
  return new Date().toISOString().replace(/\.\d{3}Z$/, "+00:00");
}

module.exports.runtime = {
  handler: async function ({ action, title, text, file }) {
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

      if (action === "draft") {
        if (!title || !text) return "To start a draft I need both a title and the full text.";
        const name = `${slug(title)}.txt`;
        const existing = locate(name);
        if (existing) return `${existing.stage}/${existing.name} already exists. Use action revise to change it, or pick another title.`;
        let body = String(text).trim();
        const [first, ...rest] = body.split("\n");
        if (first.replace(/^title:\s*/i, "").trim().toLowerCase() === String(title).trim().toLowerCase()) {
          body = rest.join("\n").trim();  // The title was repeated as the text's first line.
        }
        fs.writeFileSync(path.join(source, "drafts", name), `Title: ${String(title).trim()}\n\n${body}\n`);
        ledger("drafted", `drafts/${name}`, `"${String(title).trim()}"`);
        say(`saved drafts/${name}`);
        return `Saved new draft drafts/${name} and recorded it in the ledger.`;
      }

      if (action === "revise") {
        const hit = locate(file);
        if (!hit) return notFound(file);
        if (hit.stage === "canon") return `${hit.name} is in canon waiting to sync. Promote it or edit it by hand.`;
        if (!text) return "To revise I need the full new text.";
        const old = fs.readFileSync(hit.full, "utf8");
        const header = old.match(/^Title:.*\n/i);
        const body = String(text).trim();
        fs.writeFileSync(hit.full, (/^title:/i.test(body) || !header ? "" : header[0] + "\n") + body + "\n");
        ledger("revised", `${hit.stage}/${hit.name}`);
        say(`revised ${hit.stage}/${hit.name}`);
        return `Revised ${hit.stage}/${hit.name} and recorded it in the ledger.`;
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

      if (action === "retract") {
        // Same as `python3 canon_sync.py retract <file>`: the script checks the name and refuses
        // while the file is still in source/canon, and it writes the ledger entry itself.
        if (!file) return "To retract I need the manuscript's file name, e.g. the-oculus-draconis.txt.";
        let name = path.basename(String(file).trim());
        if (!TEXT.test(name)) name += ".txt";
        say(`running canon_sync.py retract ${name}`);
        const out = await runScript(["retract", name]);
        if (out.err) {
          const hint = /still exists/.test(out.log) ? " If it has already been ingested, run `python3 canon_sync.py gc` to clear it from canon, then retract again." : "";
          return `Nothing was retracted. ${out.log.slice(-1500)}${hint}`;
        }
        return `Retracted ${name}: it is no longer in the workspace.\n${out.log.slice(-800)}`;
      }

      return `Unknown action '${action}'. Use draft, revise, promote, publish, retract, list or read.`;
    } catch (e) {
      return `The canon archive action failed: ${e.message}`;
    }
  },
};
