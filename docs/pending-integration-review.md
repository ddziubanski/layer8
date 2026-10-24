# Pending PR integration rehearsal — QA ONLY

This branch combines PRs #32, #34, #35, #37, #39 and #42 solely to run compatibility tests. Do not merge this branch into main. The six original review branches remain independent and unchanged.

## Candidate heads
- #32 Copilot: 544b48530e391af9bd6f2ab2562900ec2cd8f0a4
- #34 README: c8886d72a1fd006fbc4637d5977d47a5adfdc00e
- #35 Action1: 1f1b93fa09ea304dae88a837f909c518032dd325
- #37 workspace: e66b8f26aa4fa93584e48349a814278e1bdb8342
- #39 Meraki: 7d3375988e831e57647ab27a65fbc2f924825aa8
- #42 Midnight: 24e32cee3b024a1510f7ee9271ff8f66d1bc23b9

## Resolutions preserved
Auth returns both personal hidden_modules and Meraki feature flags. Both Meraki and software-deployment routers stay registered. Worker labels/tests retain both jobs. Generated API types come from the complete backend schema. README/SPEC and CSS retain both sides of additive conflicts.

## Proposed schema merge order (requires owner approval)
1. #32: c3d58f1b7a20 -> e4a27b9f1062.
2. Rebase #35 after approved #32 merge: set f52b6d10a930's parent to e4a27b9f1062.
3. Rebase #39 after approved #35 merge: set a86d20f49c71's parent to f52b6d10a930.

Those parent edits exist only in this QA branch. They must be carried into the respective PR after the preceding approved merge; do not copy a parent reference into an independent PR before that revision exists on its base. The QA regression upgrades through each stage, checks table creation, and ends at a single Alembic head. Existing Copilot upgrade/downgrade coverage runs too.

#37, #42 and #34 need no database migration. Rebase the later of #37/#42 and regenerate frontend API types. Rebase #39 to preserve auth navigation fields and both routers/jobs; never resolve those conflicts by picking one side wholesale.

## Validation
Combined frontend and PostgreSQL CI plus mock-data browser QA are recorded in the QA PR and source PR descriptions. No real integration credentials, outbound provider calls, software installation or appliance changes are used. Temporary browser previews are not committed.


## Completed validation on runtime commit 2240629989c042318ed905965470b6ad4fb4dea4

- PostgreSQL CI: 620 passed, 24 optional live AD/Kerberos skips; staged migration upgrade and existing downgrade tests passed.
- Frontend CI: 198 tests passed; TypeScript and production build passed.
- Local combined focus/navigation/branding tests: 10 passed; production build passed.
- Browser: save minimal, navigate without reload, Ctrl+K > Scripts > Enter opens the hidden module and focuses its heading, return/reload retains choices, Show all/save/reload restores every module.
- Browser: Midnight > Custom restores saved purple main colour (#643b92), navy sidebar (#101a2a), light mode, custom L8 logo and custom gradient background. Switching back to Midnight retains the same assets.
- Browser: tablet viewport 1029 CSS px had no horizontal overflow; command Escape restores trigger focus. All API data was synthetic, including persisted browser fixture state; no real user settings changed.

[Visual evidence](../screenshots/combined-review/) contains desktop minimal mode, hidden-module command palette, custom-brand restoration and tablet screenshots. Source feature branches remain independent; this QA branch must not be merged.
