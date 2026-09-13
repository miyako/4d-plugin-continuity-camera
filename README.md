![version](https://img.shields.io/badge/version-19%2B-5682DF)
![platform](https://img.shields.io/static/v1?label=platform&message=mac-intel%20|%20mac-arm&color=blue)
[![license](https://img.shields.io/github/license/miyako/4d-plugin-continuity-camera)](LICENSE)
![downloads](https://img.shields.io/github/downloads/miyako/4d-plugin-continuity-camera/total)

# 4d-plugin-continuity-camera

This plugin invokes macOS's Continuity Camera feature — the system context menu that lets you pull in a photo or scanned document from a nearby iPhone or iPad signed into the same Apple ID — and returns whatever you captured to your 4D method as a `Picture` (image) or a PDF `Picture` (scanned document). Internally it drives AppKit's `NSMenu`/`NSServicesMenuRequestor` continuity-camera integration and libtiff (for splitting a multi-resolution TIFF capture into separate pictures), so no App Store/network permissions are involved beyond what macOS itself asks for when the feature is used.

| Command | Returns | Purpose |
|---|---|---|
| [`Continuity camera menu`](#continuity-camera-menu) | Object | Show the Continuity Camera / image-import context menu at a given screen position and return what the user captured. |

**Platforms:** macOS only (Intel and Apple Silicon). There is no Windows implementation — the plugin's entire source is Objective-C/Cocoa with no `#if VERSIONWIN` branch at all, so this is a Mac-exclusive plugin, not a cross-platform one with a missing half.

---

## Requirements & platform notes

- **macOS 10.14 (Mojave) or later.** Continuity Camera itself is an Apple feature introduced in Mojave; this plugin doesn't work on earlier macOS versions.
- **An iPhone or iPad nearby, signed into the same Apple ID as the Mac, with Wi-Fi/Bluetooth/Handoff enabled**, is required for the menu to offer the "Take Photo" / "Scan Documents" options at all. Without one nearby, the command still shows a menu (whatever else the system contributes to it), but the Continuity Camera-specific entries won't appear — this is entirely OS-driven; the plugin doesn't detect or report device proximity itself.
- **The command always returns an object**, even on failure or if the user dismisses the menu without capturing anything — check `success` before reading `document`/`images`.
- **`document` and `images` are independent, not mutually exclusive.** A single capture can populate both keys at once (e.g. a multi-page scan): `document` holds a single combined PDF `Picture`, `images` holds a `Collection` of separate per-page/per-resolution TIFF `Picture`s. Don't assume only one will be present.
- **Failure is silent, not a 4D error.** An invalid `window` reference, a dismissed menu, or an internal error all just yield `{success: false}` (with no `document`/`images` keys) — the command never raises a 4D error condition itself.

---

## Continuity camera menu

### Syntax

```4d
$status:=Continuity camera menu ($options)
```

| Parameter | Type | Description |
|---|---|---|
| `options` | Object | Configuration object (see below). If omitted or `Null`, the command does nothing and returns `{success: false}` — no menu is shown. |
| Result | Object | Result object (see below). |

**`options` properties:**

| Property | Type | Description |
|---|---|---|
| `title` | Text | Menu title. Optional — defaults to `"Contextual Menu"` if omitted. |
| `x` | Real | Horizontal position, in the target window's local coordinates, where the menu appears. |
| `y` | Real | Vertical position, in the target window's local coordinates (measured from the top of the window, like `GET MOUSE`'s coordinates — the plugin flips it to AppKit's bottom-left origin internally). |
| `window` | Longint | 4D window reference to anchor the menu to. Optional — if omitted, the command uses the current form window (equivalent to 4D's internal "current form window" command). |

**Result object properties:**

| Property | Type | Description |
|---|---|---|
| `success` | Boolean | `true` if a document and/or one or more images were captured; `false` if nothing was captured (menu dismissed, invalid `window`, or an internal error). |
| `document` | Picture | Present only if a PDF document was captured (e.g. a multi-page scan). A single `Picture` containing PDF data — extract it with `PICTURE TO BLOB` and a `.pdf` file extension, as in the example below. |
| `images` | Collection | Present only if an image was captured. A collection of `Picture` elements, each a TIFF. A single capture is typically returned in 3 resolutions (see Description). |

### Description

- **`options` is checked for truthiness, not for having every key set** — you must pass an object, but `title`/`window` may be omitted; `x`/`y` are read unconditionally (via `ob_get_n`, which yields `0` if the key is absent), so omitting them positions the menu at the window's origin rather than raising an error.
- **The menu shown by this command is deliberately empty of its own items.** The plugin creates a plain `NSMenu` with no entries and pops it up anchored to a helper view that implements `NSServicesMenuRequestor` and is made first responder — this is the documented Apple mechanism by which macOS automatically injects the "Import from iPhone or iPad" / "Insert from" Continuity Camera items into an otherwise-empty menu. If no eligible nearby device is found, you may see a menu with no usable items rather than an error.
- **`images` resolutions:** per the plugin's own description, a single photo capture is typically split into 3 TIFF resolutions in the returned collection — the plugin does not document which index corresponds to which resolution, so treat the collection as an unordered set of available resolutions rather than assuming, e.g., element 0 is always the largest.
- **The PDF in `document`, when present, is raw PDF byte data wrapped in a `Picture`** — 4D's `Picture` type is just a container here; you need `PICTURE TO BLOB` (with a `.pdf` extension) to get back a usable PDF file, as shown in the example.
- **As of the source-level fixes applied in the accompanying code review** (not yet built/shipped — check with whoever builds your copy of the plugin), an invalid or stale `window` reference and any unexpected internal error during capture now both resolve to a clean `{success: false}` result rather than a crash or an indefinitely hanging call. If you're running an older build of this plugin, don't rely on this yet.

### Example

From the plugin's own `README.md`:

```4d
$options:=New object
$options.window:=Current form window
GET MOUSE($x;$y;$buttonDown)
$options.x:=$x
$options.y:=$y

$status:=Continuity camera menu ($options)

var $image : Picture

Form.image:=$image*0

If ($status.success)
	
	For each ($image; $status.images)
		Form.image:=Form.image/$image
	End for each 
	
	If ($status.document#Null)
		$folder:=Folder(Temporary folder; fk platform path).folder(Generate UUID)
		$folder.create()
		$file:=$folder.file("scan.pdf")
		PICTURE TO BLOB($status.document; $data; ".pdf")
		$file.setContent($data)
		SHOW ON DISK($file.platformPath)
	End if 
	
End if 
```

A minimal call using the current form window and a fixed title, without reading the mouse position:

```4d
var $options; $status : Object

$options:=New object
$options.title:="Import photo"
$options.x:=20
$options.y:=20

$status:=Continuity camera menu ($options)

If ($status.success) & (($status.images#Null) & (Count elements($status.images)>0))
	FORM.capturedPhoto:=$status.images[0]
End if 
```

Anchoring the menu to a specific window rather than the current form window:

```4d
var $options; $status : Object

$options:=New object
$options.title:="Scan document"
$options.window:=$myWindowRef  // any 4D window reference you already have on hand; check the Language Reference for the exact command that returns one for your case
$options.x:=10
$options.y:=10

$status:=Continuity camera menu ($options)
```

---

## Error handling & troubleshooting

- **No Continuity Camera items appear in the menu.** Confirm both devices are signed into the same Apple ID, have Wi-Fi and Bluetooth on, and are near each other — this is entirely macOS's own detection, not something the plugin controls or can report a reason for.
- **`success` is `false` with no error detail.** The command doesn't currently return a distinct error code/message — a dismissed menu, a bad `window` reference, and an internal failure during image processing are all indistinguishable from the result object alone. Treat any `false` as "nothing was captured" rather than trying to branch on a specific cause.
- **Passing an invalid or already-closed `window` reference is safe, not a crash** (as of the fixed source) — it simply results in `{success: false}` with no menu shown, rather than acting on a stale window pointer.
- **Both `document` and `images` can be absent even with `success: true` in principle should the capture path change in a future version** — always check each key with `#Null` (or check it's defined) before using it, rather than assuming one implies the other, since they represent independent capture types (photo vs. scanned document).
- **Calling this command from more than one process/context at the same time:** as of the fixed source, each call's result is now scoped to that call, so concurrent invocations no longer risk one call's result leaking into or clobbering another's.

---

## Quick reference

```4d
var $options; $status : Object

$options:=New object
$options.window:=Current form window
GET MOUSE($x;$y;$buttonDown)
$options.x:=$x
$options.y:=$y

$status:=Continuity camera menu ($options)

If ($status.success)
	If ($status.images#Null)
		var $image : Picture
		For each ($image; $status.images)
			FORM.image:=$image  // or compose, e.g. FORM.image:=FORM.image/$image
		End for each 
	End if 
	If ($status.document#Null)
		$file:=Folder(Temporary folder; fk platform path).folder(Generate UUID).file("scan.pdf")
		$file.getParentFolder().create()
		PICTURE TO BLOB($status.document; $data; ".pdf")
		$file.setContent($data)
	End if 
End if 
```
