import React from "react";
import { interpolate, useCurrentFrame, useVideoConfig } from "remotion";
import { AI, FONT, INK, MUTED, PANEL } from "./theme";

export type Message =
  | { from: "member"; text: string; at: number; typeFor?: number }
  | { from: "assistant"; text: string; at: number }
  | { from: "typing"; at: number; until: number }
  | { from: "status"; text: string; at: number; until?: number }
  | { from: "result"; code: string; text: string; at: number };

const appear = (t: number, at: number) =>
  interpolate(t, [at, at + 0.35], [0, 1], { extrapolateLeft: "clamp", extrapolateRight: "clamp" });

/** The assistant's chat window. It is drawn for the video, so it says so in its corner; the
 * question and answer it shows are the real agent run's. */
export const Chat: React.FC<{
  x: number;
  y: number;
  w: number;
  h: number;
  who: string;
  messages: Message[];
}> = ({ x, y, w, h, who, messages }) => {
  const t = useCurrentFrame() / useVideoConfig().fps;
  return (
    <div
      style={{
        position: "absolute",
        left: x,
        top: y,
        width: w,
        height: h,
        background: PANEL,
        borderRadius: 22,
        boxShadow: "0 0 0 1px rgba(255,255,255,.07), 0 30px 70px rgba(0,0,0,.5)",
        fontFamily: FONT,
        color: INK,
        overflow: "hidden",
        display: "flex",
        flexDirection: "column",
      }}
    >
      <div style={{ display: "flex", alignItems: "center", gap: 12, padding: "18px 22px", borderBottom: "1px solid rgba(255,255,255,.07)" }}>
        <span style={{ width: 34, height: 34, borderRadius: 99, background: AI, display: "grid", placeItems: "center", color: "#0B0F17", fontWeight: 800, fontSize: 18 }}>
          AI
        </span>
        <span style={{ fontSize: 24, fontWeight: 600 }}>Credit union assistant</span>
        <span style={{ marginLeft: "auto", fontSize: 15, color: MUTED, border: `1px solid ${MUTED}`, borderRadius: 6, padding: "2px 8px" }}>
          illustration
        </span>
      </div>
      <div style={{ flex: 1, display: "flex", flexDirection: "column", justifyContent: "flex-end", gap: 14, padding: 22 }}>
        {messages
          .filter((m) => t >= m.at && (!("until" in m) || m.until === undefined || t < m.until))
          .map((m, i) => {
            const o = appear(t, m.at);
            const lift = { opacity: o, transform: `translateY(${(1 - o) * 14}px)` };
            if (m.from === "member") {
              const n = m.typeFor ? Math.floor(m.text.length * Math.min(1, (t - m.at) / m.typeFor)) : m.text.length;
              return (
                <div key={i} style={{ alignSelf: "flex-end", maxWidth: "85%", ...lift }}>
                  <div style={{ fontSize: 16, color: MUTED, textAlign: "right", marginBottom: 4 }}>{who}</div>
                  <div style={{ background: "#2A3446", borderRadius: "18px 18px 4px 18px", padding: "14px 18px", fontSize: 28, lineHeight: 1.3 }}>
                    {m.text.slice(0, n)}
                    {n < m.text.length ? <span style={{ opacity: 0.6 }}>|</span> : null}
                  </div>
                </div>
              );
            }
            if (m.from === "typing") {
              return (
                <div key={i} style={{ alignSelf: "flex-start", display: "flex", gap: 8, background: "#1E2535", borderRadius: 18, padding: "16px 20px", ...lift }}>
                  {[0, 1, 2].map((d) => (
                    <span key={d} style={{ width: 11, height: 11, borderRadius: 99, background: AI, opacity: 0.35 + 0.65 * Math.max(0, Math.sin((t * 6) - d)) }} />
                  ))}
                </div>
              );
            }
            if (m.from === "status") {
              return (
                <div key={i} style={{ alignSelf: "flex-start", fontSize: 20, color: AI, display: "flex", alignItems: "center", gap: 10, ...lift }}>
                  <span style={{ width: 16, height: 16, borderRadius: 99, border: `3px solid ${AI}`, borderTopColor: "transparent", transform: `rotate(${t * 360}deg)` }} />
                  {m.text}
                </div>
              );
            }
            if (m.from === "result") {
              return (
                <div key={i} style={{ alignSelf: "flex-start", maxWidth: "92%", background: "#1E2535", borderLeft: `4px solid ${AI}`, borderRadius: 14, padding: "14px 18px", ...lift }}>
                  <div style={{ fontFamily: "Menlo, monospace", fontSize: 20, color: AI, marginBottom: 6 }}>{m.code}</div>
                  <div style={{ fontSize: 24, lineHeight: 1.3 }}>{m.text}</div>
                </div>
              );
            }
            return (
              <div key={i} style={{ alignSelf: "flex-start", maxWidth: "94%", ...lift }}>
                <div style={{ background: "#1E2535", borderRadius: "18px 18px 18px 4px", padding: "14px 18px", fontSize: 21, lineHeight: 1.38, whiteSpace: "pre-wrap" }}>
                  {m.text}
                </div>
              </div>
            );
          })}
      </div>
    </div>
  );
};
