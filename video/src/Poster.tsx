import React from "react";
import { AbsoluteFill } from "remotion";
import { Chat } from "./Chat";
import { RecipeCard, Step } from "./scenes";
import { Story } from "./story";
import { BG, FONT, INK, PERSON } from "./theme";

/** The README's poster: the approved recipe beside Amber's answered chat, from the same story
 * data as the film (the real question and reply, the recipe the real discovery compiled). */
export const Poster: React.FC<{ story: Story | null }> = ({ story }) => {
  // The film's own length, from the story it is rendered from, as m:ss.
  const secs = Math.round(story?.duration ?? 0);
  const length = `${Math.floor(secs / 60)}:${String(secs % 60).padStart(2, "0")}`;
  if (!story) return <AbsoluteFill style={{ background: BG }} />;
  const recipe = story.scenes.find((s) => s.id === "approve");
  const answer = story.scenes.find((s) => s.id === "answer");
  if (!recipe || !answer) return <AbsoluteFill style={{ background: BG }} />;
  const steps = recipe.props.steps as Step[];
  return (
    <AbsoluteFill style={{ background: BG, fontFamily: FONT, color: INK }}>
      <div style={{ position: "absolute", left: 40, top: 150, transform: "scale(0.74)", transformOrigin: "0 0" }}>
        <RecipeCard steps={steps} t={99} arrive={() => -9} tags={-9} x={0} y={0} />

      </div>
        <div
        style={{
          position: "absolute",
          left: 470,
          top: 740,
          transform: "rotate(-12deg)",
          fontSize: 72,
          fontWeight: 800,
          color: PERSON,
          border: `7px solid ${PERSON}`,
          borderRadius: 16,
          padding: "6px 30px",
          background: "rgba(11,15,23,.8)",
        }}
      >
        Approved
      </div>
      <Chat
        x={940}
        y={110}
        w={940}
        h={860}
        who="Amber"
        size={30}
        messages={[
          { from: "member", text: answer.props.question as string, at: -1 },
          { from: "assistant", text: answer.props.answer as string, at: -1, highlight: answer.props.highlight as string },
        ]}
      />
      <div style={{ position: "absolute", left: 40, top: 40, fontSize: 40, fontWeight: 700 }}>
        Eagleview: an AI learns a bank task once, then a recipe runs it with no AI
      </div>
      <div
        style={{
          position: "absolute",
          left: 40,
          bottom: 50,
          display: "flex",
          alignItems: "center",
          gap: 16,
          fontSize: 32,
          fontWeight: 700,
          background: "#fff",
          color: BG,
          padding: "12px 26px 12px 16px",
          borderRadius: 999,
        }}
      >
        <svg width={40} height={40} viewBox="0 0 24 24">
          <circle cx={12} cy={12} r={12} fill={BG} />
          <path d="M9.5 7.5v9l7-4.5Z" fill="#fff" />
        </svg>
        Watch the demo ({length})
      </div>
    </AbsoluteFill>
  );
};
