// The timeline written by video/prepare.py from the run logs and the footage: every time,
// coordinate and line of reasoning on screen comes from there. video/sync.py builds the
// segments, presses and thoughts, and checks them against the footage before writing them.
export type Piece = { clip: string; from: number; to: number; cut: boolean };
export type Segment =
  | { kind: "play"; from: number; to: number; o0: number; o1: number; cut: boolean }
  | { kind: "hold"; at: number; o0: number; o1: number; action: number };
export type Press = { action: number; o0: number; o1: number };
export type Thought = { o0: number; o1: number; text: string; action: number | null };
export type Box = { x: number; y: number; width: number; height: number };
export type Action = {
  t: number;
  kind: string;
  x: number;
  y: number;
  value: string | null;
  quoted: boolean;
  actor: string;
  step: string;
  name: string;
  bbox?: Box;
};
export type Control = { t: number; who: "AI" | "Recipe" | "No one" | "Person" };
export type Cue = { start: number; end: number };
export type Region = [number, number, number, number];
export type Scene = {
  id: string;
  kind: string;
  start: number;
  duration: number;
  delay: number;
  pieces: Piece[];
  segments: Segment[];
  presses: Press[];
  thoughts: Thought[];
  /** Camera zooms, timed by video/prepare.py to wait for the footage (and checked there). */
  shots: { t: number; region: Region }[];
  props: Record<string, unknown>;
  cues: Cue[];
};
export type Story = {
  fps: number;
  voice: string;
  duration: number;
  scenes: Scene[];
  cues: { text: string; start: number; end: number; scene: string; index: number }[];
  audio: { src: string; start: number; duration: number }[];
  clips: Record<string, { src: string; duration: number; width: number; height: number }>;
  regions: Record<string, Region>;
};

/** What the footage shows at scene time t: the source time (held during a press, and on the
 * last frame after the footage ends), how long since a jump cut, and the press under way.
 * The same rule as video/sync.py's source_at, which the sync check verifies. */
export function sourceAt(
  t: number,
  segments: Segment[],
): { s: number; sinceCut: number; press: { action: number; p: number } | null } {
  if (segments.length === 0) return { s: 0, sinceCut: 99, press: null };
  const first = segments[0];
  if (t < first.o0) return { s: first.kind === "play" ? first.from : first.at, sinceCut: 99, press: null };
  for (const seg of segments) {
    if (t >= seg.o0 && t < seg.o1) {
      if (seg.kind === "hold") {
        return { s: seg.at, sinceCut: 99, press: { action: seg.action, p: (t - seg.o0) / (seg.o1 - seg.o0) } };
      }
      return { s: seg.from + (t - seg.o0), sinceCut: seg.cut ? t - seg.o0 : 99, press: null };
    }
  }
  const last = segments[segments.length - 1];
  return { s: last.kind === "play" ? last.to : last.at, sinceCut: 99, press: null };
}

/** The scene time at which source time s is first on screen. */
export function outTime(s: number, segments: Segment[]): number {
  for (const seg of segments) {
    if (seg.kind === "play" && seg.from <= s && s < seg.to) return seg.o0 + (s - seg.from);
    if (seg.kind === "play" && s < seg.from) return seg.o0;
  }
  return segments.length ? segments[segments.length - 1].o1 : 0;
}

export const pressOf = (scene: Scene, action: number): Press | undefined =>
  scene.presses.find((p) => p.action === action);

export function whoAt(control: Control[], s: number): Control["who"] {
  let who = control[0]?.who ?? "Recipe";
  for (const c of control) if (c.t <= s) who = c.who;
  return who;
}
