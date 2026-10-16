import React from "react";
import { Composition, staticFile } from "remotion";
import { Demo } from "./Demo";
import { Story } from "./story";

const FPS = 30;

export const Root: React.FC = () => (
  <Composition
    id="Demo"
    component={Demo}
    width={1920}
    height={1080}
    fps={FPS}
    durationInFrames={FPS * 60}
    defaultProps={{ story: null as Story | null }}
    calculateMetadata={async () => {
      // Written by video/prepare.py from the run logs; see video/README.md.
      const story = (await (await fetch(staticFile("media/story.json"))).json()) as Story;
      return { durationInFrames: Math.ceil(story.duration * FPS), props: { story } };
    }}
  />
);
