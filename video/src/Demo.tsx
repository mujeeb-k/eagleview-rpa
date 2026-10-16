import React from "react";
import { AbsoluteFill, Audio, Sequence, staticFile, useVideoConfig } from "remotion";
import { SCENES } from "./scenes";
import { Story } from "./story";
import { Subtitles } from "./Subtitles";
import { BG } from "./theme";

// Cues whose subject sits low on screen: their subtitles go to the top for that moment.
const SUBTITLES_ON_TOP = new Set<string>(["gate:2"]);

export const Demo: React.FC<{ story: Story | null }> = ({ story }) => {
  const { fps } = useVideoConfig();
  if (!story) return <AbsoluteFill style={{ background: BG }} />;
  return (
    <AbsoluteFill style={{ background: BG }}>
      {story.scenes.map((scene) => {
        const View = SCENES[scene.kind];
        return (
          <Sequence
            key={scene.id}
            name={scene.id}
            from={Math.round(scene.start * fps)}
            durationInFrames={Math.round(scene.duration * fps)}
          >
            <View story={story} scene={scene} />
          </Sequence>
        );
      })}
      {story.audio.map((a) => (
        <Sequence key={a.src} from={Math.round(a.start * fps)} durationInFrames={Math.round(a.duration * fps)}>
          <Audio src={staticFile(a.src)} />
        </Sequence>
      ))}
      <Subtitles story={story} top={SUBTITLES_ON_TOP} />
    </AbsoluteFill>
  );
};
