# Canon sync

Keeps the AnythingLLM workspace **The Librarian** in step with the canon published on the Drupal site.

It reads `/izizop/songs`, `/izizop/fragments`, `/izizop/albums`, `/izizop/collections` and `/izizop/headings`, turns every song, annotation, text fragment, album (IZIZOP vessel, with its description, tracklist and Bandcamp text), collection (description and contents grouped by heading), heading (tag) and library section into its own document with a citation header (title, type, song/vessel or collection, source URL), and adds a `Placement` line (e.g. `The Nazilic Concordance › Glossa Nazilicum`) to each fragment's header. It then:

- uploads and embeds new or changed items,
- removes items that were deleted, unpublished or trashed on the site,
- leaves unchanged items alone, so refreshes are quick,
- keeps every earlier version in a local SQLite file (`canon_sync.sqlite`), so canon changes are on record.

Documents are stored in the AnythingLLM documents folder `bibliotheca-canon`.

## Setup (once)

1. In AnythingLLM: **Settings → Developer API → Generate New API Key**.
2. Copy `.env.example` to `.env` in this folder and paste the key into `ANYTHINGLLM_API_KEY`.
3. Check `ANYTHINGLLM_WORKSPACE` matches the workspace slug (the last part of the workspace's URL).

AnythingLLM must be running. Only the Python standard library is needed.

## Use

```bash
python3 canon_sync.py --dry-run   # preview what would change
python3 canon_sync.py             # refresh the workspace
python3 canon_sync.py status      # what is synced
python3 canon_sync.py changes     # recent canon changes
python3 canon_sync.py --rebuild   # remove everything synced and re-add it
```

## Writing archive (`source/`)

Your own writing moves through folders in `source/` and enters the workspace once it reaches `canon/`:

```
source/
├── drafts/    raw writing
├── pending/   approved, awaiting entry
├── canon/     final: synced into AnythingLLM as "manuscript" documents
└── ledger/    ingest.log — what was ingested, cleared or retracted, and when
```

- Write in Markdown (`.md`), so the folder can be edited in Obsidian or any Markdown editor. Older `.txt` files still work. Only files directly inside these folders count; files left loose in `source/` are ignored.
- The title comes from a first line `# Heading` (or `Title: …`) if there is one; otherwise from the file name (`oculus-draconis.md` → "Oculus Draconis").
- Keep the same file name from draft to canon. That is how gc knows which drafts are earlier versions.

```bash
python3 canon_sync.py                  # ingests files in source/canon along with the site
python3 canon_sync.py gc --dry-run     # preview what gc would clear
python3 canon_sync.py gc               # clear ingested files out of source/
python3 canon_sync.py retract X.md     # take an ingested manuscript out of the workspace
```

`gc` deletes each file in `canon/` that has been ingested and not edited since, plus any file in `drafts/` or `pending/` with the same name (any extension). Files that are new or edited since ingest are kept until the next sync. The text stays in AnythingLLM and in `canon_sync.sqlite`.

Clearing a file does not remove it from the workspace, and `--rebuild` re-adds it from the stored text. To change a manuscript later, put a file with the same name in `canon/` and sync, which replaces it. To remove one, use `retract`. A renamed file counts as a new manuscript, so retract the old name.

## Driving the archive from AnythingLLM

`anythingllm-skill/canon-archive/` is a custom agent skill that lets the Librarian work the archive for you. It can create drafts, revise them, promote files from drafts to pending, publish them from pending into canon (publishing runs `canon_sync.py`), open a file in Obsidian, list each stage, read a file, and recall any published canon entry by title. Every action is added to `source/ledger/ingest.log`, next to the script's own ingested and cleared entries.

**Install (once).** Link the skill into AnythingLLM Desktop's storage, then restart AnythingLLM:

```bash
ln -s "$PWD/anythingllm-skill/canon-archive" "$HOME/Library/Application Support/anythingllm-desktop/storage/plugins/agent-skills/canon-archive"
```

Then open **Settings → Agent Skills**, switch on **Canon archive**, and check its settings: the folder holding `canon_sync.py`, the full path to `python3`, and the Obsidian vault name for `source/` (default `source`; leave it empty to let Obsidian choose the vault by path). Because it's a link, edits to the skill here take effect after an AnythingLLM restart.

**Aliases.** Type `/` in the chat box (or click the slash-command button) and add a new preset. Each preset's text replaces the command before AnythingLLM checks for `@agent`, so a preset that starts with `@agent` opens agent mode for you:

| Command | Prompt |
| --- | --- |
| `/draft` | `@agent Call the Canon archive tool with action draft now. If I paste text below, pass it word for word (its first line is the title). If I only give a title or refer to earlier text, pass just the title and set use_last_reply to true. Draft:` |
| `/revise` | `@agent Call the Canon archive tool with action revise now. If I paste text below, pass it word for word; if I refer to earlier text, set use_last_reply to true. File:` |
| `/edit` | `@agent Use the Canon archive skill with action edit to open this file in Obsidian:` |
| `/read` | `@agent Call the Canon archive tool with action read on this file, then do what I ask with its full text:` |
| `/recall` | `@agent Call the Canon archive tool with action recall, passing this title or document name exactly as I wrote it (a type word alone lists that type's titles), then do what I ask with the full text:` |
| `/promote` | `@agent Use the Canon archive skill with action promote (drafts to pending) on this file:` |
| `/publish` | `@agent Use the Canon archive skill with action publish (pending into canon, then sync) on this file:` |
| `/retract` | `@agent Use the Canon archive skill with action retract to take this published manuscript out of the workspace:` |
| `/audit` | `@agent Use the Canon archive skill with action list and show me what is in drafts, pending and canon.` |

`/edit the-oculus-draconis` opens that file in Obsidian, at whichever stage it's in, and changes nothing. For example, `/promote the-oculus-draconis.md` moves that draft to pending, and `/publish the-oculus-draconis.md` then moves it into canon and syncs it. Promote only goes from drafts to pending and publish only from pending to canon, and a file moves at most one stage per message. `/retract the-oculus-draconis.md` runs `canon_sync.py retract` with the same checks (the extension can be left off), so run `gc` first if the file is still in canon.

**Recalling canon.** `/recall` fetches the full text of anything in the workspace (songs, annotations, fragments, albums, collections, headings, library sections and your manuscripts) from `canon_sync.sqlite`, which holds one entry for each document in AnythingLLM's `bibliotheca-canon` folder. Unlike ordinary chat, it doesn't depend on search ranking, so the model gets the whole entry:

- `/recall a meeting with micaloz` matches by title; capitals and punctuation don't matter, and a few words of the title are enough if only one entry has them.
- `/recall raw-a-meeting-with-micaloz-798634e6-….json` takes a document name copied from Upload Documents.
- `/recall song The Crystal Caves` puts the type first when a song and a fragment share a title. If a name matches several entries, the reply lists them with their types.
- `/recall songs` (or `fragments`, `collections`, …) lists every title of that type.
- Add a request after it: `/recall song The Crystal Caves — how does it treat the caves compared with the fragment?`

**Tips for local models**

- **Start a new thread for each piece you work on** (+ New Thread in the workspace sidebar). The model's context is 32K tokens, and a few long editorial passes in the history fill it. When that happens, AnythingLLM compresses the prompt and the model skips tool calls, invents results or times out. A fresh thread gives the file and the edit the whole window, and `/revise` still finds the previous reply within that thread.

- To save something the Librarian just wrote, give only a title: `/draft to-bend-the-law.md and include the editorializing`. The skill reads the previous reply from AnythingLLM's chat history itself (`use_last_reply`), so the model never has to copy long text into the tool call, which is where local models fail. The draft includes the reply's opening and closing remarks; trim them in Obsidian. It skips archive commands and their replies, and stays in the current thread.
- Text you paste after `/draft` is saved as given.
- To editorialize a file you wrote yourself (in Obsidian, or dropped into `drafts/`): `/read my-story.md editorialize it in place with inline remarks`, check the reply, then `/revise my-story.md` with nothing after it to save that reply over the file. `/revise` uses the latest reply that isn't a status message from `/draft`, `/revise`, `/edit`, `/promote`, `/publish`, `/retract` or `/audit`, so asking those in between is fine, but any other chat in between becomes the "previous reply".
- Local models sometimes claim a tool ran when it didn't. A real call always names the file and its length ("Saved new draft drafts/x.md (8973 characters…)"); if the reply doesn't, nothing happened. Check with `/audit`.
- `gc` stays manual: run `python3 canon_sync.py gc` after promoting things into canon.

Use `--rebuild` after changing the embedding model or chunk settings in AnythingLLM.

To read from the local DDEV site instead of production, set `DRUPAL_BASE_URL=https://drupalmtd.ddev.site` in `.env`.
