// What the app is busy with, as one explicit state per owner.
//
// The active photo's decode is owned by loadSource — only its newest call may
// move it, besides a lost GL context marking the frame gone — so an import
// finishing, an export failing or a look rebuild going wrong cannot end a
// decode early or mark a good frame as failed. Import
// and export keep their own flags (useCatalog's `importing`, useExport's
// `exporting`); errors are told through the toast (`errorMessage`), which is a
// message, not a state. The footer's one word is derived from all three here.

/** Why a decode was asked for; panels that started one show it as busy. */
export type DecodeCause = "open" | "denoise" | "erase";

export type DecodeState =
  | { phase: "idle" } // no frame: nothing open yet, or the photo went away
  | { phase: "decoding"; cause: DecodeCause }
  | { phase: "ready"; ms: number } // the frame on screen, and what it cost
  | { phase: "failed" }; // the last decode (or the GL context) did not survive

export const IDLE: DecodeState = { phase: "idle" };

export type Activity =
  | { state: "decoding" | "exporting" | "importing" | "failed" | "idle" }
  | { state: "ready"; ms: number };

/** The footer's status: the most pressing thing in flight, else the frame's state. */
export function activityOf(decode: DecodeState, o: { importing: boolean; exporting: boolean }): Activity {
  if (decode.phase === "decoding") return { state: "decoding" };
  if (o.exporting) return { state: "exporting" };
  if (o.importing) return { state: "importing" };
  if (decode.phase === "ready") return { state: "ready", ms: decode.ms };
  return { state: decode.phase };
}

export function decodingFor(decode: DecodeState, cause: DecodeCause): boolean {
  return decode.phase === "decoding" && decode.cause === cause;
}
