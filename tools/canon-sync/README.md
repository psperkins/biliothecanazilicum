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

- Only `.txt` and `.md` files directly inside these folders count. Files left loose in `source/` are ignored.
- The title comes from a first line `Title: …` if there is one; otherwise from the file name (`oculus-draconis.txt` → "Oculus Draconis").
- Keep the same file name from draft to canon. That is how gc knows which drafts are earlier versions.

```bash
python3 canon_sync.py                  # ingests files in source/canon along with the site
python3 canon_sync.py gc --dry-run     # preview what gc would clear
python3 canon_sync.py gc               # clear ingested files out of source/
python3 canon_sync.py retract X.txt    # take an ingested manuscript out of the workspace
```

`gc` deletes each file in `canon/` that has been ingested and not edited since, plus any file in `drafts/` or `pending/` with the same name (any extension). Files that are new or edited since ingest are kept until the next sync. The text stays in AnythingLLM and in `canon_sync.sqlite`.

Clearing a file does not remove it from the workspace, and `--rebuild` re-adds it from the stored text. To change a manuscript later, put a file with the same name in `canon/` and sync, which replaces it. To remove one, use `retract`. A renamed file counts as a new manuscript, so retract the old name.

## Driving the archive from AnythingLLM

`anythingllm-skill/canon-archive/` is a custom agent skill that lets the Librarian work the archive for you. It can create drafts, revise them, promote files from drafts to pending, publish them from pending into canon (publishing runs `canon_sync.py`), list each stage and read a file. Every action is added to `source/ledger/ingest.log`, next to the script's own ingested and cleared entries.

**Install (once).** Link the skill into AnythingLLM Desktop's storage, then restart AnythingLLM:

```bash
ln -s "$PWD/anythingllm-skill/canon-archive" "$HOME/Library/Application Support/anythingllm-desktop/storage/plugins/agent-skills/canon-archive"
```

Then open **Settings → Agent Skills**, switch on **Canon archive**, and check its two settings: the folder holding `canon_sync.py`, and the full path to `python3`. Because it's a link, edits to the skill here take effect after an AnythingLLM restart.

**Aliases.** Type `/` in the chat box (or click the slash-command button) and add a new preset. Each preset's text replaces the command before AnythingLLM checks for `@agent`, so a preset that starts with `@agent` opens agent mode for you:

| Command | Prompt |
| --- | --- |
| `/draft` | `@agent Use the Canon archive skill with action draft. Save the text below word for word as a new draft; the first line is the title:` |
| `/revise` | `@agent Use the Canon archive skill with action revise. Replace this file's text word for word with the text below. File:` |
| `/promote` | `@agent Use the Canon archive skill with action promote (drafts to pending) on this file:` |
| `/publish` | `@agent Use the Canon archive skill with action publish (pending into canon, then sync) on this file:` |
| `/retract` | `@agent Use the Canon archive skill with action retract to take this published manuscript out of the workspace:` |
| `/audit` | `@agent Use the Canon archive skill with action list and show me what is in drafts, pending and canon.` |

For example, `/promote the-oculus-draconis.txt` moves that draft to pending, and `/publish the-oculus-draconis.txt` then moves it into canon and syncs it. Promote only goes from drafts to pending and publish only from pending to canon, and a file moves at most one stage per message. `/retract the-oculus-draconis.txt` runs `canon_sync.py retract` with the same checks, so run `gc` first if the file is still in canon.

**Tips for local models**

- The model has to copy your text into the skill call. Small models sometimes shorten long passages, so for long pieces, paste the text after `/draft` yourself rather than asking for "a draft of that", and open the file to check it.
- The agent sees recent chat history, so "start a new draft of this" can work for short passages.
- `gc` stays manual: run `python3 canon_sync.py gc` after promoting things into canon.

Use `--rebuild` after changing the embedding model or chunk settings in AnythingLLM.

To read from the local DDEV site instead of production, set `DRUPAL_BASE_URL=https://drupalmtd.ddev.site` in `.env`.
