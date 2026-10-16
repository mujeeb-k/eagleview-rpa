import React from "react";
import {
  AbsoluteFill,
  Easing,
  Freeze,
  OffthreadVideo,
  Sequence,
  interpolate,
  staticFile,
  useCurrentFrame,
  useVideoConfig,
} from "remotion";
import { Action, Box, Control, Press, Region, Segment, Story, Thought, sourceAt, whoAt } from "./story";
import { AI, FONT, INK, WHO_COLOR, actorColor } from "./theme";

export const SRC_W = 1280;
export const SRC_H = 800;

export type Frame = { x: number; y: number; w: number };
export type Shot = { t: number; region: Region | null };
export type Label = {
  text: string;
  /** The element pointed at, in source pixels: the label sits outside it, joined by a line. */
  box: Box;
  color: string;
  from: number; // scene seconds
  to?: number;
  side?: "left" | "right" | "above" | "below";
};

const ease = Easing.bezier(0.4, 0, 0.2, 1);

/** A box around a point: the element's size when only its centre is known. */
export const around = (x: number, y: number, halfW: number, halfH: number): Box => ({
  x: x - halfW,
  y: y - halfH,
  width: 2 * halfW,
  height: 2 * halfH,
});

/** The camera, in source pixels: the visible window at scene time t. Each shot eases in from
 * the previous one over 0.9 s. Overlays are drawn inside the same transform as the footage. */
export function camera(shots: Shot[], t: number): { left: number; top: number; scale: number } {
  const view = (r: Region | null) => {
    if (r === null) return { left: 0, top: 0, scale: 1 };
    const [x0, y0, x1, y1] = r;
    const pad = 40;
    const scale = Math.min(SRC_W / (x1 - x0 + 2 * pad), SRC_H / (y1 - y0 + 2 * pad), 2.4);
    const w = SRC_W / scale;
    const h = SRC_H / scale;
    const left = Math.min(Math.max(0, (x0 + x1) / 2 - w / 2), SRC_W - w);
    const top = Math.min(Math.max(0, (y0 + y1) / 2 - h / 2), SRC_H - h);
    return { left, top, scale };
  };
  let from = view(null);
  let to = from;
  let t0 = 0;
  for (const shot of shots) {
    if (shot.t > t) break;
    from = to;
    to = view(shot.region);
    t0 = shot.t;
  }
  const p = interpolate(t, [t0, t0 + 0.9], [0, 1], {
    extrapolateLeft: "clamp",
    extrapolateRight: "clamp",
    easing: ease,
  });
  return {
    left: from.left + (to.left - from.left) * p,
    top: from.top + (to.top - from.top) * p,
    scale: from.scale + (to.scale - from.scale) * p,
  };
}

const Still: React.FC<{ src: string; at: number }> = ({ src, at }) => {
  const { fps } = useVideoConfig();
  return (
    <Freeze frame={0}>
      <OffthreadVideo src={src} trimBefore={Math.max(0, Math.round(at * fps))} muted />
    </Freeze>
  );
};

/** The footage, segment by segment: played at its recorded speed, or held on one frame while
 * an action's ripple plays (the frame before that action's effect, as measured and checked). */
const Segments: React.FC<{ story: Story; clip: string; segments: Segment[]; total: number }> = ({
  story,
  clip,
  segments,
  total,
}) => {
  const { fps } = useVideoConfig();
  if (segments.length === 0) return null;
  const src = staticFile(story.clips[clip].src);
  const frames = (s: number) => Math.max(1, Math.round(s * fps));
  const first = segments[0];
  const last = segments[segments.length - 1];
  const parts: React.ReactNode[] = [];
  if (first.o0 > 0) {
    parts.push(
      <Sequence key="pre" durationInFrames={frames(first.o0)}>
        <Still src={src} at={first.kind === "play" ? first.from : first.at} />
      </Sequence>,
    );
  }
  segments.forEach((seg, i) => {
    parts.push(
      <Sequence key={i} from={Math.round(seg.o0 * fps)} durationInFrames={frames(seg.o1 - seg.o0)}>
        {seg.kind === "play" ? (
          <OffthreadVideo src={src} trimBefore={Math.round(seg.from * fps)} muted />
        ) : (
          <Still src={src} at={seg.at} />
        )}
      </Sequence>,
    );
  });
  parts.push(
    <Sequence key="end" from={Math.round(last.o1 * fps)} durationInFrames={frames(total - last.o1) + 1}>
      <Still src={src} at={last.kind === "play" ? last.to - 1 / fps : last.at} />
    </Sequence>,
  );
  return <>{parts}</>;
};

const MOVE = 0.55;

/** The cursor, in output time: it travels to each action in the moment before its press and
 * rests there during it, so a click always lands on the screen it was made on. */
function cursorAt(
  presses: Press[],
  actions: Action[],
  t: number,
): { x: number; y: number; actor: string } | null {
  const order = [...presses].sort((a, b) => a.o0 - b.o0);
  if (order.length === 0) return null;
  let prev: Press | null = null;
  let next: Press | null = null;
  for (const p of order) {
    if (p.o0 <= t) prev = p;
    else {
      next = p;
      break;
    }
  }
  const at = (p: Press) => actions[p.action];
  if (next && next.o0 - t < MOVE) {
    const n = at(next);
    const from = prev ? at(prev) : { x: n.x + 140, y: n.y + 110 };
    const k = ease(Math.min(1, Math.max(0, 1 - (next.o0 - t) / MOVE)));
    return { x: from.x + (n.x - from.x) * k, y: from.y + (n.y - from.y) * k, actor: n.actor };
  }
  if (prev) return { x: at(prev).x, y: at(prev).y, actor: at(prev).actor };
  const n = at(order[0]);
  return { x: n.x + 140, y: n.y + 110, actor: n.actor };
}

const Pointer: React.FC<{ x: number; y: number; color: string; k: number }> = ({ x, y, color, k }) => (
  <svg
    width={30 / k}
    height={40 / k}
    viewBox="0 0 30 40"
    style={{ position: "absolute", left: x - 3 / k, top: y - 2 / k, overflow: "visible" }}
  >
    <path
      d="M3 2 L3 31 L10.5 24.5 L15.5 36 L20.5 33.8 L15.6 22.6 L25.5 22.4 Z"
      fill="#fff"
      stroke={color}
      strokeWidth={2.6}
      strokeLinejoin="round"
    />
  </svg>
);

/** A pointer label, beside its element and never on it: placed beyond the element's edge and
 * joined to it by a short line. Drawn in source pixels inside the camera; `k` is the source-to-
 * screen scale (the frame's size times the zoom), so text is 30 px tall on a 1080p screen. */
export const PointerLabel: React.FC<{ label: Label; t: number; k: number }> = ({ label, t, k }) => {
  const to = label.to ?? 9999;
  const fade = interpolate(t, [label.from, label.from + 0.3, to - 0.2, to], [0, 1, 1, 0], {
    extrapolateLeft: "clamp",
    extrapolateRight: "clamp",
  });
  if (fade <= 0) return null;
  const b = label.box;
  const side = label.side ?? "right";
  const gap = 26 / k;
  const cx = b.x + b.width / 2;
  const cy = b.y + b.height / 2;
  const edge =
    side === "right"
      ? { x: b.x + b.width, y: cy }
      : side === "left"
        ? { x: b.x, y: cy }
        : side === "above"
          ? { x: cx, y: b.y }
          : { x: cx, y: b.y + b.height };
  const end =
    side === "right"
      ? { x: edge.x + gap, y: edge.y }
      : side === "left"
        ? { x: edge.x - gap, y: edge.y }
        : side === "above"
          ? { x: edge.x, y: edge.y - gap }
          : { x: edge.x, y: edge.y + gap };
  const pos: React.CSSProperties =
    side === "right"
      ? { left: end.x, top: end.y, transform: "translateY(-50%)" }
      : side === "left"
        ? { left: end.x, top: end.y, transform: "translate(-100%, -50%)" }
        : side === "above"
          ? { left: end.x, top: end.y, transform: "translate(-50%, -100%)" }
          : { left: end.x, top: end.y, transform: "translateX(-50%)" };
  return (
    <>
      <svg style={{ position: "absolute", left: 0, top: 0, overflow: "visible", opacity: fade }} width={1} height={1}>
        <line x1={edge.x} y1={edge.y} x2={end.x} y2={end.y} stroke={label.color} strokeWidth={3 / k} />
        <circle cx={edge.x} cy={edge.y} r={4 / k} fill={label.color} />
      </svg>
      <div
        style={{
          position: "absolute",
          ...pos,
          opacity: fade,
          whiteSpace: "nowrap",
          fontFamily: FONT,
          fontWeight: 600,
          fontSize: 30 / k,
          color: "#0B0F17",
          background: label.color,
          padding: `${6 / k}px ${14 / k}px`,
          borderRadius: 999,
          boxShadow: `0 ${4 / k}px ${16 / k}px rgba(0,0,0,.45)`,
        }}
      >
        {label.text}
      </div>
    </>
  );
};

export const Token: React.FC<{ who: Control["who"]; note?: string }> = ({ who, note }) => {
  const color = WHO_COLOR[who];
  return (
    <div style={{ position: "absolute", top: 18, right: 18, display: "flex", flexDirection: "column", alignItems: "flex-end", gap: 8 }}>
      <div
        style={{
          display: "flex",
          alignItems: "center",
          gap: 10,
          padding: "10px 22px 10px 16px",
          borderRadius: 999,
          background: "rgba(11,15,23,.92)",
          border: `3px solid ${color}`,
          fontFamily: FONT,
          fontSize: 32,
          fontWeight: 600,
          color: INK,
        }}
      >
        <span style={{ width: 16, height: 16, borderRadius: 99, background: color }} />
        Driving: <span style={{ color }}>{who}</span>
      </div>
      {note ? (
        <div style={{ fontFamily: FONT, fontSize: 28, color, background: "rgba(11,15,23,.85)", padding: "4px 12px", borderRadius: 8 }}>
          {note}
        </div>
      ) : null}
    </div>
  );
};

export type FootageProps = {
  story: Story;
  clip: string;
  segments: Segment[];
  presses: Press[];
  total: number;
  frame: Frame;
  actions?: Action[];
  control?: Control[];
  shots?: Shot[];
  labels?: Label[];
  thoughts?: Thought[];
  tokenNote?: (who: Control["who"]) => string | undefined;
  cutWord?: string;
  children?: (geo: { s: number; t: number; k: number }) => React.ReactNode;
};

export const Footage: React.FC<FootageProps> = ({
  story,
  clip,
  segments,
  presses,
  total,
  frame,
  actions = [],
  control,
  shots = [],
  labels = [],
  thoughts = [],
  tokenNote,
  cutWord = "thinking",
  children,
}) => {
  const t = useCurrentFrame() / useVideoConfig().fps;
  const { s, sinceCut, press } = sourceAt(t, segments);
  const cam = camera(shots, t);
  const base = frame.w / SRC_W;
  const zoom = cam.scale;
  const k = base * zoom; // source pixels to screen pixels: overlays size their text by it
  const cursor = cursorAt(presses, actions, t);
  const thought = thoughts.find((x) => t >= x.o0 && t < x.o1);
  const who = control ? whoAt(control, s) : null;
  const order = [...presses].sort((a, b) => a.o0 - b.o0);
  const ripple = press ? actions[press.action] : null;
  return (
    <div
      style={{
        position: "absolute",
        left: frame.x,
        top: frame.y,
        width: frame.w,
        height: frame.w * (SRC_H / SRC_W),
        overflow: "hidden",
        borderRadius: 14,
        boxShadow: `0 0 0 2px ${who ? WHO_COLOR[who] : "#2A3343"}, 0 24px 60px rgba(0,0,0,.5)`,
        background: "#fff",
      }}
    >
      <div
        style={{
          position: "absolute",
          width: SRC_W,
          height: SRC_H,
          transformOrigin: "0 0",
          transform: `scale(${k}) translate(${-cam.left}px, ${-cam.top}px)`,
        }}
      >
        <AbsoluteFill>
          <Segments story={story} clip={clip} segments={segments} total={total} />
        </AbsoluteFill>
        {ripple && press ? (
          <div
            style={{
              position: "absolute",
              left: ripple.x - (10 + 40 * press.p) / k,
              top: ripple.y - (10 + 40 * press.p) / k,
              width: (20 + 80 * press.p) / k,
              height: (20 + 80 * press.p) / k,
              borderRadius: 999,
              border: `${4 / k}px solid ${actorColor(ripple.actor)}`,
              background: `${actorColor(ripple.actor)}33`,
              opacity: 1 - 0.7 * press.p,
            }}
          />
        ) : null}
        {order.map((p, i) => {
          const a = actions[p.action];
          if (!a.value) return null;
          // From the keystroke until the next action begins: the field doesn't move meanwhile.
          const until = Math.min(p.o1 + 1.0, (order[i + 1]?.o0 ?? 1e9) - 0.05);
          if (t < p.o0 || t >= until) return null;
          return (
            <div
              key={`v${i}`}
              style={{
                position: "absolute",
                left: a.x + 26 / k,
                top: a.y - 56 / k,
                fontFamily: FONT,
                fontWeight: 700,
                fontSize: 30 / k,
                color: "#0B0F17",
                background: actorColor(a.actor),
                padding: `${5 / k}px ${14 / k}px`,
                borderRadius: 8 / k,
                whiteSpace: "nowrap",
                opacity: interpolate(t, [p.o0, p.o0 + 0.15, until - 0.15, until], [0, 1, 1, 0]),
              }}
            >
              {a.quoted ? `types “${a.value}”` : `types ${a.value}`}
            </div>
          );
        })}
        {labels.map((l, i) => (
          <PointerLabel key={`l${i}`} label={l} t={t} k={k} />
        ))}
        {children ? children({ s, t, k }) : null}
        {cursor ? <Pointer x={cursor.x} y={cursor.y} color={actorColor(cursor.actor)} k={k} /> : null}
        {cursor && thought ? (
          <div
            style={{
              position: "absolute",
              left: Math.min(cursor.x + 34 / k, SRC_W - 760 / k),
              top: cursor.y + 34 / k,
              maxWidth: 720 / k,
              fontFamily: FONT,
              fontSize: 30 / k,
              lineHeight: 1.3,
              color: INK,
              background: "rgba(23,18,48,.92)",
              border: `${2 / k}px solid ${AI}`,
              padding: `${8 / k}px ${12 / k}px`,
              borderRadius: 10 / k,
              opacity: interpolate(t, [thought.o0, thought.o0 + 0.25, thought.o1 - 0.15, thought.o1], [0, 1, 1, 0]),
            }}
          >
            <span style={{ color: AI, fontWeight: 700 }}>AI: </span>
            {thought.text}
          </div>
        ) : null}
        {cursor && sinceCut < 0.7 ? (
          <div
            style={{
              position: "absolute",
              left: cursor.x + 34 / k,
              top: cursor.y - 40 / k,
              fontFamily: FONT,
              fontSize: 28 / k,
              fontWeight: 600,
              color: AI,
              background: "rgba(11,15,23,.85)",
              padding: `${4 / k}px ${10 / k}px`,
              borderRadius: 99,
            }}
          >
            {cutWord}
            {".".repeat(1 + (Math.floor(t * 4) % 3))}
          </div>
        ) : null}
      </div>
      {who ? <Token who={who} note={tokenNote?.(who)} /> : null}
    </div>
  );
};
