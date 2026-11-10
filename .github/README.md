# Manual source maintenance and publication

There are exactly two independent manual workflows:

* **Bump version** takes one required `patch`, `minor`, or `major` choice
  (default `patch`). Select a branch with the built-in ref selector.
  It validates the immutable selected commit, then creates one direct,
  source-only Actions-bot child and pushes only that branch. It never tags,
  builds, releases, or dispatches another workflow.
* **Build** has no custom inputs. The built-in ref selector selects the source;
  every checkout uses immutable `github.sha`. Its source version determines
  `vX.Y.Z` and the ZIP/DMG names. Running Build on already-published source fails
  before native work. Run Bump first when a new source version is needed.

Build publication shares the repository-wide constant `release-publication`
concurrency group across refs, without cancellation. Actions does not promise
FIFO scheduling. Bump has separate per-branch concurrency and checks the actual
advertised old branch SHA in a mandatory executable pre-push guard. Both
`push.followTags=false` and `--no-follow-tags` suppress annotated tags. A
concurrent advance, rewind, deletion, or same-result update fails closed;
nonforce Git transport also protects updates after advertisement. Failures
retain the local child; inspect it rather than automatically retrying.

## Authority and preservation

The immutable intake is `014963d3c6c3ab23a8406b108fed3dee27836812`;
`tests/fixtures/intake.json` records its actual source blobs and configuration
counts. Rename-follow history leads to Bump header
`d90755699b187eaee14392043d0f37efc3ed27e7`, which explicitly coordinates
`MARKETING_VERSION`, `CURRENT_PROJECT_VERSION`, and plist short/build fields.
Its buggy implementation selected only marketing when available.

The migration does **not** increment marketing `2.0.3`. Coordinator-authorized
repair changes exactly six stale current-version `2.0.0` literals to `2.0.3`,
and the two active English localized short/GetInfoString version tokens to
`2.0.3`. CR-only separators (including the original terminal CR), zero LF bytes,
the product name, copyright2022,
and file modes are preserved. Localized historical cochanges at
`fdfc95581f376ba76b978849b3b69253b2a3dbc9` and
`ee46f7810e2d07effde07bb5b16e83b5fd7ed4dc` are separate evidence from the
header: the old Bump script did **not** update localization.

Future bumps co-update both coordinated project fields and the actual copied
localized display fields. Source `Info.plist` remains byte-identical:
`CFBundleShortVersionString=$(MARKETING_VERSION)` is preserved, and absent
`CFBundleVersion` stays absent. Comments, unrelated strings, encodings/BOMs,
individual mixed line endings, modes, and valid references are preserved.
Lexical duplicates, unresolved/cyclic references, missing keys, configuration
identity/name drift and inconsistent versions fail before writes.

## Native scope

The actual original topology is Ubuntu version validation plus macOS Default,
not the old comment claiming a Windows build. The tracked Win32 **and** x64
payloads, their modes, QuickLook thumbnail, dependencies, all destination
metadata, runtime and 4D policy are unchanged. No Windows job, NTFS mode
normalization, dependency cloning, gitlink exception or native phase rewrite
was introduced.

The native invocation remains:

```sh
xcodebuild -project "Continuity Camera.xcodeproj" \
  -target "Continuity Camera" -configuration Default \
  ARCHS="arm64 x86_64" ONLY_ACTIVE_ARCH=NO \
  MACOSX_DEPLOYMENT_TARGET="11.0" build
```

In particular there is no output override or code-sign-disabling flag.
Cocoa and the existing tiff/jpeg/z static libraries remain in the project.
The original Deployment-only signing/archive phase remains unchanged and is
inactive at Default. Original successful hosted run36969811983, job110721260457,
native step5 at the intake SHA corroborates Default `OBJROOT=test/Plugins`,
the `Continuity Camera.build` intermediate and `XCBuildData` there.
The retained native-only step hash is
`83f893c6e9d99df8bb50733340d46828a4de9ff559f91ab51d60f8a89497ba51`.
Development/Deployment's explicit `OBJROOT=build` does not govern Default.
The product is `test/Plugins/Continuity Camera.bundle`, **not** a Default
package sibling. Observed empty module-cache directories allow no arbitrary
files.

Cleanup validates every target, ancestor, descendant, nested `.git` marker,
physical type and read/search access, plus deletion-parent write/search
access, before the first deletion. Only ENOENT means absence. It removes only
the exact regenerated leaves (including copied manifest/constants resources)
and proven project intermediate, never whole bundles, unrelated resources,
Windows prebuilts, database roots or Default siblings.
Selected/indexed source under an alleged intermediate is never disposable.

Initial checkout validation is globally clean, including ignored files.
Final validation instead retains regenerated executable/plist/localization/
signature bytes and exact native intermediates; it independently checks HEAD,
the complete index, both `ls-files -v` and `-f` flags, and raw nongenerated
source bytes. XCBuildData permits only `build.db`, `build.db-wal`,
`build.db-shm` and lowercase32hex `.xcbuilddata` records.
Every packaged leaf must be nonempty, regular and nonsymlink. Copied manifest
and constants must equal selected source; both prebuilts and thumbnail must
remain raw-equal to selected Git blobs. Built XML/binary plist and compiled or
text localization are revalidated at final publication.

Exact `arm64` and `x86_64` tokens are required; additional slices are not
forbidden by the original policy. Both ZIP and DMG require notarytool exit0
and strict JSON `Accepted`; DMG stapling and strict/deep codesign verification
must succeed. Existing signing credential references remain unchanged.

## Create-only publication and recovery

The helper requires explicit nonempty `GH_TOKEN`, exact case-sensitive
`GITHUB_REPOSITORY=miyako/4d-plugin-continuity-camera`, immutable
`GITHUB_SHA`, and successful canonical repository API authentication with a
positive integer repository ID. Installation tokens are supported: no
`GET /user` probe is used.

Before native work and after all signing/notarization/stapling, availability
checks read all release pages until an **empty** page, including drafts,
matching API tag refs and exact remote tag advertisements. HTTP/auth/network,
duplicate/nonfinite JSON, malformed schema and occupied versions fail closed.
Publication separately creates a tag ref at the selected SHA, verifies it
through API and Git, creates one new public release ID, then uploads only to
that ID. There is no update, reuse, delete, clobber, rollback or retry.
Original release notes remain empty and generated notes remain disabled.
GitHub normalizes spaces to dots, yielding
`Continuity.Camera-X.Y.Z.zip` and `Continuity.Camera-X.Y.Z.dmg`.

On failure, the diagnostic reports retained or possibly-created tag, release
ID and uploaded assets. Inspect these remotely and arrange manual recovery;
do not rerun blindly or overwrite an existing version.

## Offline validation

```sh
python3 -B -m unittest discover -s tests -v
actionlint .github/workflows/*.yml
```

The suite requires Python3, PyYAML and actionlint. Runner `-B` keeps the suite's
own import loader from writing test caches; actual helper and extracted-phase
subprocesses deliberately run without `-B` or a bytecode environment.
Fixtures pin original data to intake while live expectations support genuine
bumped children. Real local bare-Git and fake-gh tests exercise CLI publication,
guard races, real H/h fsmonitor flags, retained tracked outputs, exact package
leaves, physical cleanup and real extracted shell phases with the bytecode
environment explicitly absent. Native/credential commands run only behind
closed-PATH doubles; these do not prove hosted toolchains or Apple/GitHub
services. No local native build, signing, notarization, 4D execution or Actions
dispatch is part of this suite.

The guarded Bump CLI is:

```sh
python3 .github/scripts/bump.py patch --sha LITERAL_SELECTED_SHA \
  --ref refs/heads/SELECTED_BRANCH
```

The migration publication hook uses `MIGRATION_HEAD` and `MIGRATION_OLD` as
literal40hex values, validates its exact committed100755 raw blob on every
check, and accepts precisely one advertisement for
`refs/heads/miyako-simplify-continuity-camera-workflows`. An initial branch
publication requires all-zero old SHA. The owner session ordinarily handles
commits, final committed/bumped-child proofs, production publication and PR
creation, subject to separate coordinator authorization. Coordinator review
gates commits, final committed/bumped-child proofs, production publication and
PR creation; completing one gate does not authorize the next. The coordinator
controls review/merge/archive authorization; this guide does not itself
authorize any of those actions.
