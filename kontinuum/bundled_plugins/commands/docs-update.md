Update the repo living docs to reflect the change just made. $ARGUMENTS

The arguments above are the repo-relative paths that changed — Kontinuum computed them for you. You have NO shell; do not run git.

## Rules
- First read `.claude/skills/living-docs.md` for the page format, frontmatter schema, and authoring rules.
- Edit files directly under `docs/living-docs/`. Do NOT touch code, and do NOT run git or commit — Kontinuum commits your drafts into the SAME pull request the human reviews.
- Keep pages short and high-signal; point to code (`path:line`), do not restate it.

## Steps
1. If `docs/living-docs/` does not exist, initialize the minimal structure per the skill (config.json, README.md, empty INDEX.md, the subsystems/flows/adr folders), then continue.
2. Read the frontmatter of every page under `docs/living-docs/{subsystems,flows,adr}`. Against the changed paths, find:
   - **affected** — pages whose `sources:` globs cover any changed path.
   - **gaps** — a changed area covered by NO page that is significant enough to deserve one.
3. Refresh each affected page against the changed code; for a real gap, add one short page. Stamp `last_verified.date` to today.
4. Rebuild `docs/living-docs/INDEX.md` from all page frontmatter per the skill.
5. If nothing here is doc-worthy, make no changes.
