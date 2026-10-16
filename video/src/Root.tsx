import React from "react";
import { Composition, Still, staticFile } from "remotion";
import { Demo } from "./Demo";
import { Poster } from "./Poster";
import { Story } from "./story";

const FPS = 30;

const loadStory = async () =>
  // Written by video/prepare.py from the run logs; see video/README.md.
  (await (await fetch(staticFile("media/story.json"))).json()) as Story;

export const Root: React.FC = () => (
  <>
  <Composition
    id="Demo"
    component={Demo}
    width={1920}
    height={1080}
    fps={FPS}
    durationInFrames={FPS * 60}
    defaultProps={{ story: null as Story | null }}
    calculateMetadata={async () => {
      const story = await loadStory();
      return { durationInFrames: Math.ceil(story.duration * FPS), props: { story } };
    }}
  />
  <Still
    id="Poster"
    component={Poster}
    width={1920}
    height={1080}
    defaultProps={{ story: null as Story | null }}
    calculateMetadata={async () => ({ props: { story: await loadStory() } })}
  />
  </>
);
