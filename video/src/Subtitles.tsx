import React from "react";
import { interpolate, useCurrentFrame, useVideoConfig } from "remotion";
import { Story } from "./story";
import { FONT } from "./theme";

const MAX = 42;

/** Breaks a cue into at most two lines of about 42 characters, at the most natural point:
 * after punctuation if there is one near the middle, otherwise at the space nearest it. */
export function lines(text: string): string[] {
  if (text.length <= MAX) return [text];
  const words = text.split(" ");
  let best: { i: number; score: number } = { i: 1, score: Infinity };
  let left = "";
  for (let i = 1; i < words.length; i++) {
    left = words.slice(0, i).join(" ");
    const right = words.slice(i).join(" ");
    if (left.length > MAX + 2 || right.length > MAX + 6) {
      if (left.length > MAX + 2) break;
      continue;
    }
    const balance = Math.abs(left.length - right.length);
    const phrase = /[,:;.]$/.test(left) ? -14 : /^(and|or|but|so|who|like|with|instead|before|until|by|not)$/.test(words[i]) ? -8 : 0;
    const score = balance + phrase;
    if (score < best.score) best = { i, score };
  }
  return [words.slice(0, best.i).join(" "), words.slice(best.i).join(" ")];
}

/** The narration, word for word. Bottom of the screen, unless a cue's own scene asks for the
 * top because what is being discussed sits low. */
export const Subtitles: React.FC<{ story: Story; top: Set<string> }> = ({ story, top }) => {
  const t = useCurrentFrame() / useVideoConfig().fps;
  const cue = story.cues.find((c) => t >= c.start - 0.05 && t < c.end + 0.25);
  if (!cue) return null;
  const o = interpolate(t, [cue.start - 0.05, cue.start + 0.1, cue.end + 0.1, cue.end + 0.25], [0, 1, 1, 0], {
    extrapolateLeft: "clamp",
    extrapolateRight: "clamp",
  });
  const atTop = top.has(`${cue.scene}:${cue.index}`);
  return (
    <div
      style={{
        position: "absolute",
        left: 0,
        right: 0,
        [atTop ? "top" : "bottom"]: 34,
        display: "flex",
        justifyContent: "center",
        opacity: o,
      }}
    >
      <div
        style={{
          fontFamily: FONT,
          fontSize: 40,
          fontWeight: 600,
          lineHeight: 1.28,
          color: "#fff",
          textAlign: "center",
          background: "rgba(5,8,14,.78)",
          padding: "10px 26px 12px",
          borderRadius: 14,
          textShadow: "0 2px 6px rgba(0,0,0,.6)",
        }}
      >
        {lines(cue.text).map((l, i) => (
          <div key={i}>{l}</div>
        ))}
      </div>
    </div>
  );
};
