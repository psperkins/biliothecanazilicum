# Canon sync

Keeps the AnythingLLM workspace **The Librarian** in step with the canon published on the Drupal site.

It reads `/izizop/songs`, `/izizop/fragments` and `/izizop/albums`, turns every song, annotation, text fragment and album (IZIZOP vessel, with its description, tracklist and Bandcamp text) into its own document with a citation header (title, type, song/vessel or collection, source URL), and:

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

Use `--rebuild` after changing the embedding model or chunk settings in AnythingLLM.

To read from the local DDEV site instead of production, set `DRUPAL_BASE_URL=https://drupalmtd.ddev.site` in `.env`.
