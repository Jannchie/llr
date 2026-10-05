// One photo record for the tests that need one, so a spec names only the fields
// its subject is about — and a field added to `Photo` is filled in here once.

import type { Photo } from "../ui";

/** A photo with every field filled in; `over` replaces what the test cares about. */
export function photo(over: Partial<Photo> & { id: string }): Photo {
  return {
    folderId: 1, name: `${over.id}.arw`, ext: ".arw", size: 1000, importedAt: 1000, modifiedAt: 1000,
    width: 6000, height: 4000, orientation: 1, capturedAt: null, make: null, model: null, lens: null,
    iso: null, exposure: null, fnumber: null, focal: null, thumbState: "ready", previewAt: null,
    embeddedUrl: "", thumbUrl: "", previewUrl: null, ...over,
  };
}

/** `n` photos, named p0…, in catalog order. */
export function photos(n: number): Photo[] {
  return Array.from({ length: n }, (_, i) => photo({ id: `p${i}`, name: `p${i}.arw` }));
}
