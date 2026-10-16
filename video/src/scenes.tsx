import React from "react";
import {
  AbsoluteFill,
  Img,
  interpolate,
  spring,
  staticFile,
  useCurrentFrame,
  useVideoConfig,
} from "remotion";
import { Chat } from "./Chat";
import { Footage, Frame, Label, SRC_H, SRC_W, Token, around } from "./Footage";
import { Action, Box, Control, Region, Scene, Story, outTime, pressOf } from "./story";
import { AI, ALERT, BG, FONT, INK, MONO, MUTED, PANEL, PERSON, RECIPE } from "./theme";

type P = { story: Story; scene: Scene };
const FULL: Frame = { x: 200, y: 26, w: 1520 };

const useT = () => useCurrentFrame() / useVideoConfig().fps;
const cueAt = (scene: Scene, i: number) => scene.cues[i]?.start ?? 0;
const cueEnd = (scene: Scene, i: number) => scene.cues[i]?.end ?? scene.duration;
const fadeIn = (t: number, at: number, d = 0.4) =>
  interpolate(t, [at, at + d], [0, 1], { extrapolateLeft: "clamp", extrapolateRight: "clamp" });
const prop = <T,>(scene: Scene, key: string) => scene.props[key] as T;

const Bg: React.FC<{ children: React.ReactNode }> = ({ children }) => (
  <AbsoluteFill style={{ background: BG, fontFamily: FONT, color: INK }}>{children}</AbsoluteFill>
);

/** A free-standing label on the canvas (outside any footage). */
const Tag: React.FC<{ x: number; y: number; text: string; color: string; at: number; until?: number; t: number }> = ({
  x,
  y,
  text,
  color,
  at,
  until,
  t,
}) => {
  const o = fadeIn(t, at) * (until ? interpolate(t, [until - 0.3, until], [1, 0], { extrapolateLeft: "clamp", extrapolateRight: "clamp" }) : 1);
  if (o <= 0) return null;
  return (
    <div
      style={{
        position: "absolute",
        left: x,
        top: y,
        transform: "translate(-50%, -50%)",
        opacity: o,
        fontSize: 26,
        fontWeight: 700,
        color: "#0B0F17",
        background: color,
        padding: "6px 16px",
        borderRadius: 999,
        whiteSpace: "nowrap",
        boxShadow: "0 6px 20px rgba(0,0,0,.4)",
      }}
    >
      {text}
    </div>
  );
};

const question = (scene: Scene) => prop<string>(scene, "question");

/** The props every footage scene passes: its clip, and the one timeline of segments and
 * presses that video/sync.py built and checked. */
const film = (scene: Scene) => ({
  clip: scene.pieces[0]?.clip ?? "",
  segments: scene.segments,
  presses: scene.presses,
  total: scene.duration,
});

/** An action's press window in scene time, by its index; labels for an action live within it,
 * so none outlasts the screen the action was made on. */
const pressWindow = (scene: Scene, actions: Action[], a: Action | undefined) => {
  if (!a) return undefined;
  return pressOf(scene, actions.indexOf(a));
};
const boxOf = (a: Action, halfW: number, halfH: number): Box => a.bbox ?? around(a.x, a.y, halfW, halfH);
/** When the scene's zoom starts: after its cue, and after the last action on screen. */
const zoomStart = (scene: Scene) => scene.shots[0]?.t ?? 0;
const regionBox = (r: Region): Box => ({ x: r[0], y: r[1], width: r[2] - r[0], height: r[3] - r[1] });

// --- 1. the hook ------------------------------------------------------------------------------

export const Hook: React.FC<P> = ({ scene }) => {
  const t = useT();
  return (
    <Bg>
      <Chat
        x={610}
        y={250}
        w={700}
        h={440}
        who="Amber"
        messages={[
          { from: "member", text: question(scene), at: 0.3, typeFor: 1.8 },
          { from: "typing", at: 2.6, until: 99 },
        ]}
      />
      <Tag x={960} y={760} text="Amber, a member" color={INK} at={0.4} t={t} />
    </Bg>
  );
};

// --- 2. the problem ---------------------------------------------------------------------------

export const Problem: React.FC<P> = ({ scene }) => {
  const t = useT();
  const move = interpolate(t, [0, 0.9], [0, 1], { extrapolateRight: "clamp" });
  const chatX = 610 + (110 - 610) * move;
  const screens = prop<string[]>(scene, "screens");
  const c1 = cueAt(scene, 1);
  const c2 = cueAt(scene, 2);
  const arrow = interpolate(t, [c1, c1 + 0.8], [0, 1], { extrapolateLeft: "clamp", extrapolateRight: "clamp" });
  const per = (cueEnd(scene, 2) - c2 + 0.6) / screens.length;
  const shown = t < c2 ? prop<string>(scene, "app") : screens[Math.min(screens.length - 1, Math.floor((t - c2) / per))];
  return (
    <Bg>
      <Chat
        x={chatX}
        y={190}
        w={520}
        h={600}
        who="Amber"
        messages={[
          { from: "member", text: question(scene), at: -1 },
          { from: "typing", at: -1, until: 99 },
        ]}
      />
      <div style={{ position: "absolute", left: 1010, top: 240, width: 820, opacity: fadeIn(t, 0.4, 0.6) }}>
        <div style={{ fontSize: 24, fontWeight: 600, color: MUTED, marginBottom: 12 }}>Lantern Bay's teller software</div>
        <Img
          src={staticFile(shown)}
          style={{ width: 820, height: 820 * (SRC_H / SRC_W), borderRadius: 12, boxShadow: "0 0 0 2px #2A3343, 0 24px 60px rgba(0,0,0,.5)" }}
        />
      </div>
      {/* the gap: the assistant's arrow stops short of the teller software */}
      <svg style={{ position: "absolute", left: 640, top: 470 }} width={360} height={80}>
        <line x1={0} y1={40} x2={190 * arrow} y2={40} stroke={AI} strokeWidth={6} strokeDasharray="14 10" />
        {arrow >= 1 ? (
          <g stroke={ALERT} strokeWidth={7} strokeLinecap="round">
            <line x1={222} y1={22} x2={258} y2={58} />
            <line x1={258} y1={22} x2={222} y2={58} />
          </g>
        ) : null}
      </svg>
      <Tag x={880} y={580} text="No way in" color={ALERT} at={c1 + 0.8} t={t} />
      <Tag x={1420} y={830} text="Clicked by hand" color={PERSON} at={c2 + 0.2} t={t} />
    </Bg>
  );
};

// --- 3. learning once -------------------------------------------------------------------------

export const Discovery: React.FC<P> = ({ story, scene }) => {
  const actions = prop<Action[]>(scene, "actions");
  const clicks = actions.filter((a) => a.kind === "click");
  const typed = actions.find((a) => a.kind === "type" || a.kind === "fill");
  const go = clicks.find((c) => typed && c.t > typed.t);
  const table = story.regions.table;
  const labels: Label[] = [];
  const link = pressWindow(scene, actions, clicks[0]);
  if (clicks[0] && link) labels.push({ text: "Member Inquiry", box: boxOf(clicks[0], 50, 8), color: AI, from: link.o0 - 0.6, to: link.o1 });
  const field = pressWindow(scene, actions, clicks[1]);
  const typing = pressWindow(scene, actions, typed);
  if (typed && field && typing) labels.push({ text: "Member # box", box: boxOf(typed, 55, 11), color: AI, from: field.o0 - 0.6, to: typing.o1, side: "below" });
  const goPress = pressWindow(scene, actions, go);
  if (go && goPress) labels.push({ text: "GO", box: boxOf(go, 18, 10), color: AI, from: goPress.o0 - 0.6, to: goPress.o1 });
  const last = scene.thoughts[scene.thoughts.length - 1];
  if (last && last.action === null) labels.push({ text: "Share Accounts", box: regionBox(table), color: AI, from: last.o0 + 0.2, side: "above" });
  return (
    <Bg>
      <Footage
        story={story}
        {...film(scene)}
        frame={FULL}
        actions={actions}
        control={prop<Control[]>(scene, "control")}
        thoughts={scene.thoughts}
        labels={labels}
      />
    </Bg>
  );
};

type Step = { id: string; title: string; how: string; x: number; y: number };

const CARD = { x: 1030, y: 170, w: 800 };
const ROW_H = 132;

const RecipeCard: React.FC<{ steps: Step[]; t: number; arrive: (i: number) => number; tags: number; backup?: number; x?: number; y?: number; scale?: number }> = ({
  steps,
  t,
  arrive,
  tags,
  backup,
  x = CARD.x,
  y = CARD.y,
  scale = 1,
}) => (
  <div
    style={{
      position: "absolute",
      left: x,
      top: y,
      width: CARD.w,
      transform: `scale(${scale})`,
      transformOrigin: "0 0",
      background: PANEL,
      borderRadius: 22,
      border: `3px solid ${RECIPE}`,
      padding: "26px 30px 30px",
      boxShadow: "0 30px 70px rgba(0,0,0,.5)",
    }}
  >
    <div style={{ fontSize: 26, fontWeight: 700, color: RECIPE, marginBottom: 18 }}>Recipe: look up share balances</div>
    {steps.map((s, i) => {
      const o = fadeIn(t, arrive(i) + 0.55, 0.25);
      return (
        <div key={s.id} style={{ height: ROW_H - 20, display: "flex", gap: 18, alignItems: "flex-start", opacity: o }}>
          <span style={{ flex: "none", width: 44, height: 44, borderRadius: 99, background: RECIPE, color: "#0B0F17", fontWeight: 800, fontSize: 24, display: "grid", placeItems: "center" }}>
            {i + 1}
          </span>
          <div>
            <div style={{ fontSize: 32, fontWeight: 600 }}>{s.title}</div>
            <div style={{ fontSize: 22, color: RECIPE, marginTop: 6, opacity: fadeIn(t, tags + i * 0.3) }}>found by: {s.how}</div>
          </div>
        </div>
      );
    })}
    {backup !== undefined ? (
      <div style={{ fontSize: 22, color: MUTED, marginTop: 4, opacity: fadeIn(t, backup) }}>position kept only as a backup</div>
    ) : null}
  </div>
);

export const Recipe: React.FC<P> = ({ scene }) => {
  const t = useT();
  const steps = prop<Step[]>(scene, "steps");
  const shot: Frame = { x: 70, y: 220, w: 880 };
  const k = shot.w / SRC_W;
  const arrive = (i: number) => cueAt(scene, 0) + 0.2 + i * 0.75;
  return (
    <Bg>
      <Img
        src={staticFile(prop<string>(scene, "still"))}
        style={{ position: "absolute", left: shot.x, top: shot.y, width: shot.w, borderRadius: 12, opacity: 0.55, boxShadow: `0 0 0 2px ${AI}` }}
      />
      {steps.map((s, i) => {
        const p = interpolate(t, [arrive(i), arrive(i) + 0.7], [0, 1], { extrapolateLeft: "clamp", extrapolateRight: "clamp" });
        if (p <= 0 || p >= 1) return null;
        const x0 = shot.x + s.x * k;
        const y0 = shot.y + s.y * k;
        const x1 = CARD.x + 30;
        const y1 = CARD.y + 80 + i * ROW_H;
        const e = p * p * (3 - 2 * p);
        return (
          <div
            key={s.id}
            style={{
              position: "absolute",
              left: x0 + (x1 - x0) * e,
              top: y0 + (y1 - y0) * e,
              fontSize: 26,
              fontWeight: 700,
              color: "#0B0F17",
              background: e < 0.5 ? AI : RECIPE,
              padding: "6px 14px",
              borderRadius: 10,
              whiteSpace: "nowrap",
            }}
          >
            {s.title}
          </div>
        );
      })}
      <RecipeCard steps={steps} t={t} arrive={arrive} tags={cueAt(scene, 1)} backup={cueAt(scene, 2) + 0.2} />
    </Bg>
  );
};

export const Approve: React.FC<P> = ({ scene }) => {
  const t = useT();
  const { fps } = useVideoConfig();
  const steps = prop<Step[]>(scene, "steps");
  const at = cueAt(scene, 0) + 0.9;
  const s = spring({ frame: Math.round((t - at) * fps), fps, config: { damping: 12, stiffness: 160 } });
  return (
    <Bg>
      <RecipeCard steps={steps} t={99} arrive={() => -9} tags={-9} x={560} y={150} />
      {t >= at ? (
        <div
          style={{
            position: "absolute",
            left: 1050,
            top: 640,
            transform: `translate(-50%, -50%) rotate(-12deg) scale(${2.2 - 1.2 * s})`,
            opacity: Math.min(1, s * 1.4),
            fontSize: 64,
            fontWeight: 800,
            letterSpacing: ".04em",
            color: PERSON,
            border: `7px solid ${PERSON}`,
            borderRadius: 16,
            padding: "6px 30px",
            background: "rgba(11,15,23,.8)",
          }}
        >
          Approved
        </div>
      ) : null}
      <Tag x={1050} y={760} text="by a staff member" color={PERSON} at={at + 0.5} t={t} />
    </Bg>
  );
};

// --- 4. answering Amber -----------------------------------------------------------------------

const SIDE: Frame = { x: 700, y: 170, w: 1150 };

const StepChecks: React.FC<{ done: { t: number; step: string }[]; titles: Record<string, string>; scene: Scene; t: number }> = ({
  done,
  titles,
  scene,
  t,
}) => (
  <div style={{ position: "absolute", left: SIDE.x, top: 104, display: "flex", gap: 12 }}>
    {done.map((d) => {
      const at = outTime(d.t, scene.segments);
      return (
        <div
          key={d.step}
          style={{ opacity: fadeIn(t, at, 0.25), fontSize: 21, fontWeight: 600, color: "#0B0F17", background: RECIPE, padding: "5px 12px", borderRadius: 999 }}
        >
          ✓ {titles[d.step] ?? d.step}
        </div>
      );
    })}
  </div>
);

const TITLES: Record<string, string> = {
  open_member_inquiry: "Member Inquiry",
  enter_member_id: "Member #",
  submit_member_search: "GO",
  extract_share_balances: "Share Accounts",
};

export const Answer: React.FC<P> = ({ story, scene }) => {
  const t = useT();
  const answer = prop<string>(scene, "answer");
  const region = prop<Region>(scene, "region");
  const zoomAt = zoomStart(scene);
  const lift = zoomAt + 1.0;
  const reply = lift + 1.1;
  const amounts = answer
    .split("\n")
    .filter((l) => l.trim().startsWith("-"))
    .map((l) => (l.match(/\$[\d,]+\.\d\d/) ?? [""])[0]);
  const k = SIDE.w / SRC_W;
  const zoom = Math.min(SRC_W / (region[2] - region[0] + 80), SRC_H / (region[3] - region[1] + 80), 2.4);
  return (
    <Bg>
      <Chat
        x={60}
        y={120}
        w={600}
        h={840}
        who="Amber"
        messages={[
          { from: "member", text: question(scene), at: -1 },
          { from: "status", text: prop<string>(scene, "status"), at: 0.3, until: reply },
          { from: "assistant", text: answer, at: reply },
        ]}
      />
      <Footage
        story={story}
        {...film(scene)}
        frame={SIDE}
        actions={prop<Action[]>(scene, "actions")}
        control={prop<Control[]>(scene, "control")}
        shots={scene.shots}
        labels={[{ text: "Share Accounts", box: regionBox(region), color: RECIPE, from: zoomAt + 0.6, side: "above" }]}
      />
      <StepChecks done={prop<{ t: number; step: string }[]>(scene, "done")} titles={TITLES} scene={scene} t={t} />
      {amounts.map((a, i) => {
        const p = interpolate(t, [lift + i * 0.2, lift + i * 0.2 + 0.8], [0, 1], { extrapolateLeft: "clamp", extrapolateRight: "clamp" });
        if (p <= 0 || p >= 1) return null;
        const x0 = SIDE.x + SIDE.w * 0.62;
        const y0 = SIDE.y + ((region[1] + 40 + i * 30 - region[1]) * zoom) * k + 60;
        const x1 = 120;
        const y1 = 560 + i * 34;
        const e = p * p * (3 - 2 * p);
        return (
          <div key={i} style={{ position: "absolute", left: x0 + (x1 - x0) * e, top: y0 + (y1 - y0) * e, fontFamily: MONO, fontSize: 26, fontWeight: 700, color: "#0B0F17", background: RECIPE, padding: "4px 12px", borderRadius: 8 }}>
            {a}
          </div>
        );
      })}
    </Bg>
  );
};

// --- 5a. the handoff --------------------------------------------------------------------------

export const Handoff: React.FC<P> = ({ story, scene }) => {
  const t = useT();
  const phase = prop<string>(scene, "phase");
  const actions = prop<Action[]>(scene, "actions");
  const ops = actions.filter((a) => a.actor.startsWith("operator"));
  const labels: Label[] = [];
  const modal = prop<Region>(scene, "modal");
  if (phase === "alert") {
    const shown = outTime(prop<number>(scene, "modal_t"), scene.segments);
    labels.push({ text: "Never-seen warning", box: regionBox(modal), color: ALERT, from: shown + 0.3, side: "above" });
  }
  const tick = pressWindow(scene, actions, ops[0]);
  const cont = pressWindow(scene, actions, ops[1]);
  if (phase === "person" && ops[0] && tick) labels.push({ text: "Supervisor notified", box: boxOf(ops[0], 9, 9), color: PERSON, from: tick.o0 - 0.6, to: tick.o1, side: "left" });
  if (phase === "person" && ops[1] && cont) labels.push({ text: "Continue", box: boxOf(ops[1], 38, 12), color: PERSON, from: cont.o0 - 0.6, to: cont.o1, side: "right" });
  const region = phase === "back" ? prop<Region>(scene, "region") : null;
  if (region) labels.push({ text: "Lookup finished", box: regionBox(region), color: RECIPE, from: zoomStart(scene) + 0.8, side: "above" });
  const bell = phase === "waiting" ? fadeIn(t, 0.7) : 0;
  return (
    <Bg>
      <Footage
        story={story}
        {...film(scene)}
        frame={FULL}
        actions={actions}
        control={prop<Control[]>(scene, "control")}
        shots={scene.shots}
        labels={labels}
        cutWord="waiting"
        tokenNote={(who) => (who === "Person" ? "staff member (simulated)" : undefined)}
      />
      {bell > 0 ? (
        <div
          style={{
            position: "absolute",
            left: 40,
            top: 60,
            opacity: bell,
            transform: `translateX(${(1 - bell) * -30}px)`,
            display: "flex",
            alignItems: "center",
            gap: 14,
            background: PANEL,
            border: `3px solid ${PERSON}`,
            borderRadius: 16,
            padding: "16px 22px",
            boxShadow: "0 20px 50px rgba(0,0,0,.5)",
          }}
        >
          <svg width={34} height={34} viewBox="0 0 24 24" fill={PERSON}>
            <path d="M12 22a2.5 2.5 0 0 0 2.45-2h-4.9A2.5 2.5 0 0 0 12 22Zm7-6V11a7 7 0 0 0-5.5-6.84V3a1.5 1.5 0 0 0-3 0v1.16A7 7 0 0 0 5 11v5l-2 2v1h18v-1Z" />
          </svg>
          <span style={{ fontSize: 30, fontWeight: 700, color: PERSON }}>Staff needed</span>
        </div>
      ) : null}
    </Bg>
  );
};

// --- 5b. a member who doesn't exist -----------------------------------------------------------

export const Missing: React.FC<P> = ({ story, scene }) => {
  const t = useT();
  const region = prop<Region>(scene, "region");
  const zoomAt = zoomStart(scene);
  const chatAt = Math.max(cueAt(scene, 1) + 1.4, zoomAt + 1.2);
  const message = prop<string>(scene, "message");
  // A verbatim excerpt of the capability's own outcome message, not new words.
  const excerpt = (message.match(/no member exists for the member number that was searched/) ?? [message])[0];
  return (
    <Bg>
      <Footage
        story={story}
        {...film(scene)}
        frame={FULL}
        actions={prop<Action[]>(scene, "actions")}
        control={prop<Control[]>(scene, "control")}
        shots={scene.shots}
        labels={[{ text: "IBT-1004: no member", box: regionBox(region), color: RECIPE, from: zoomAt + 0.7, side: "below" }]}
      />
      {t >= chatAt ? (
        <div style={{ opacity: fadeIn(t, chatAt), transform: `translateY(${(1 - fadeIn(t, chatAt)) * 30}px)` }}>
          <Chat
            x={80}
            y={430}
            w={640}
            h={460}
            who="A member"
            messages={[
              { from: "member", text: "What balances does member 99999 have?", at: -1 },
              { from: "result", code: prop<string>(scene, "code"), text: excerpt, at: chatAt + 0.4 },
            ]}
          />
        </div>
      ) : null}
    </Bg>
  );
};

// --- 5c. the Post gate ------------------------------------------------------------------------

const Lock: React.FC<{ color: string; size: number }> = ({ color, size }) => (
  <svg width={size} height={size} viewBox="0 0 24 24" fill={color}>
    <path d="M17 10V7a5 5 0 0 0-10 0v3H5v12h14V10Zm-8-3a3 3 0 0 1 6 0v3H9Z" />
  </svg>
);

export const Gate: React.FC<P> = ({ story, scene }) => {
  const t = useT();
  const zoomAt = zoomStart(scene);
  const lockAt = Math.max(cueAt(scene, 2) + 0.4, zoomAt + 1.2);
  const post = prop<[number, number]>(scene, "post");
  return (
    <Bg>
      <Footage
        story={story}
        {...film(scene)}
        frame={FULL}
        actions={prop<Action[]>(scene, "actions")}
        control={prop<Control[]>(scene, "control")}
        shots={scene.shots}
        labels={[{ text: "Post: can't be undone", box: around(post[0], post[1], 28, 14), color: ALERT, from: zoomAt + 0.7, side: "below" }]}
      >
        {({ k }) =>
          t >= lockAt ? (
            <div
              style={{
                position: "absolute",
                left: post[0],
                top: post[1] + 14 + 74 / k,
                translate: "-50% 0",
                opacity: fadeIn(t, lockAt),
                display: "flex",
                alignItems: "center",
                gap: 8 / k,
                fontFamily: FONT,
                fontSize: 22 / k,
                fontWeight: 700,
                color: "#0B0F17",
                background: PERSON,
                padding: `${5 / k}px ${12 / k}px`,
                borderRadius: 999,
              }}
            >
              <Lock color="#0B0F17" size={22 / k} />
              needs approval
            </div>
          ) : null
        }
      </Footage>
    </Bg>
  );
};

// --- 6. privacy and reuse ---------------------------------------------------------------------

export const Privacy: React.FC<P> = ({ scene }) => {
  const t = useT();
  const w = 840;
  const k = w / SRC_W;
  return (
    <Bg>
      {[
        { x: 90, src: prop<string>(scene, "live"), title: "The live screen" },
        { x: 990, src: prop<string>(scene, "saved"), title: "What Eagleview saves" },
      ].map((p, i) => (
        <div key={i} style={{ position: "absolute", left: p.x, top: 190, width: w, opacity: fadeIn(t, i * 0.5) }}>
          <div style={{ fontSize: 28, fontWeight: 600, marginBottom: 14, color: i ? RECIPE : INK }}>{p.title}</div>
          <Img src={staticFile(p.src)} style={{ width: w, borderRadius: 12, boxShadow: `0 0 0 2px ${i ? RECIPE : "#2A3343"}` }} />
        </div>
      ))}
      {/* beside the painted-out name and address (which end about x=330 in the screenshot) */}
      <Tag x={990 + 470 * k + 80} y={190 + 52 + 80 * k} text="Blacked out" color={RECIPE} at={cueAt(scene, 0) + 1.2} t={t} />
    </Bg>
  );
};

const Building: React.FC<{ x: number; y: number; name: string; color: string; o: number }> = ({ x, y, name, color, o }) => (
  <div style={{ position: "absolute", left: x, top: y, transform: "translate(-50%, -50%)", textAlign: "center", opacity: o }}>
    <svg width={120} height={110} viewBox="0 0 24 22" fill="none" stroke={color} strokeWidth={1.4} strokeLinejoin="round">
      <path d="M2 8 12 2l10 6Z" />
      <path d="M4 9v9M8 9v9M12 9v9M16 9v9M20 9v9M2 19h20M1.5 21h21" />
    </svg>
    <div style={{ fontSize: 28, fontWeight: 600, marginTop: 8 }}>{name}</div>
  </div>
);

export const Reuse: React.FC<P> = ({ story, scene }) => {
  const t = useT();
  const notes = prop<string[]>(scene, "notes");
  const steps = prop<Step[]>(scene, "steps");
  const actions = prop<Action[]>(scene, "actions");
  const intro = t < scene.delay;
  const slide = interpolate(t, [0.5, 1.7], [0, 1], { extrapolateLeft: "clamp", extrapolateRight: "clamp" });
  const branch = actions.find((a) => a.kind === "select");
  const field = actions.find((a) => a.step === "enter_member_id");
  const search = actions.find((a) => a.step === "submit_member_search");
  const labels: Label[] = [];
  const pick = pressWindow(scene, actions, branch);
  const enter = pressWindow(scene, actions, field);
  const find = pressWindow(scene, actions, search);
  if (branch && pick) labels.push({ text: "Branch screen", box: boxOf(branch, 80, 11), color: RECIPE, from: pick.o0 - 0.6, to: pick.o1, side: "above" });
  if (field && enter) labels.push({ text: "Acct Holder No. box", box: boxOf(field, 55, 11), color: RECIPE, from: enter.o0 - 0.6, to: enter.o1 + 0.8, side: "below" });
  if (search && find) labels.push({ text: "Search button", box: boxOf(search, 32, 12), color: RECIPE, from: find.o0 - 0.6, to: find.o1 });
  return (
    <Bg>
      {intro || t < scene.delay + 0.5 ? (
        <div style={{ opacity: intro ? 1 : interpolate(t, [scene.delay, scene.delay + 0.5], [1, 0]) }}>
          <Building x={420} y={250} name="Lantern Bay" color={RECIPE} o={fadeIn(t, 0)} />
          <Building x={1500} y={250} name="Copperhill" color={RECIPE} o={fadeIn(t, 0.2)} />
          <RecipeCard steps={steps} t={99} arrive={() => -9} tags={-9} x={200 + (1060 - 200) * slide} y={420} scale={0.55} />
          {notes.map((n, i) => {
            const o = fadeIn(t, 1.8 + i * 0.35, 0.25);
            return (
              <div
                key={n}
                style={{
                  position: "absolute",
                  left: 1530,
                  top: 410 + i * 120,
                  transform: `rotate(${i % 2 ? 3 : -3}deg) scale(${0.8 + 0.2 * o})`,
                  opacity: o,
                  width: 330,
                  background: "#FFE27A",
                  color: "#3A2E00",
                  fontSize: 22,
                  fontWeight: 600,
                  padding: "14px 16px",
                  borderRadius: 4,
                  boxShadow: "0 10px 24px rgba(0,0,0,.4)",
                }}
              >
                {n}
              </div>
            );
          })}
        </div>
      ) : null}
      {!intro ? (
        <div style={{ opacity: fadeIn(t, scene.delay, 0.5) }}>
          <Footage
            story={story}
            {...film(scene)}
            frame={FULL}
            actions={actions}
            control={prop<Control[]>(scene, "control")}
            labels={labels}
          />
          <Tag x={360} y={60} text="Copperhill" color={RECIPE} at={scene.delay} t={t} />
        </div>
      ) : null}
    </Bg>
  );
};

// --- 7. the close -----------------------------------------------------------------------------

/** Three icons in the actor colours. Same shape, size, weight and opacity, all fully shown well
 * before the scene ends, so the final frame gives them equal weight. */
const Glyph: React.FC<{ kind: "ai" | "approved" | "recipe" }> = ({ kind }) => (
  <svg width={70} height={70} viewBox="0 0 24 24" fill="none" stroke="#0B0F17" strokeWidth={2.4} strokeLinecap="round" strokeLinejoin="round">
    {kind === "ai" ? <path d="M12 3l1.9 5.1L19 10l-5.1 1.9L12 17l-1.9-5.1L5 10l5.1-1.9Z" fill="#0B0F17" /> : null}
    {kind === "approved" ? <path d="M5 12.5l4.5 4.5L19 7.5" /> : null}
    {kind === "recipe" ? <path d="M8 5.5v13l11-6.5Z" fill="#0B0F17" /> : null}
  </svg>
);

export const Close: React.FC<P> = ({ scene }) => {
  const t = useT();
  const c0 = cueAt(scene, 0);
  const c1 = cueAt(scene, 1);
  const icons = [
    { color: AI, kind: "ai" as const, text: "Learned once", at: c0 },
    { color: PERSON, kind: "approved" as const, text: "Approved by a person", at: c0 + (cueEnd(scene, 0) - c0) * 0.55 },
    { color: RECIPE, kind: "recipe" as const, text: "Runs the same way", at: c1 },
  ];
  return (
    <Bg>
      <div style={{ position: "absolute", top: 330, left: 0, right: 0, display: "flex", justifyContent: "center", gap: 120 }}>
        {icons.map((i) => {
          const o = fadeIn(t, i.at, 0.35);
          return (
            <div key={i.text} style={{ width: 300, textAlign: "center", opacity: o, transform: `translateY(${(1 - o) * 20}px)` }}>
              <div style={{ width: 150, height: 150, margin: "0 auto", borderRadius: 99, background: i.color, display: "grid", placeItems: "center" }}>
                <Glyph kind={i.kind} />
              </div>
              <div style={{ fontSize: 32, fontWeight: 600, marginTop: 22, color: INK }}>{i.text}</div>
            </div>
          );
        })}
      </div>
    </Bg>
  );
};

export const Url: React.FC<P> = ({ scene }) => {
  const t = useT();
  return (
    <Bg>
      <div style={{ position: "absolute", inset: 0, display: "flex", flexDirection: "column", alignItems: "center", justifyContent: "center", opacity: fadeIn(t, 0.1, 0.6) }}>
        <div style={{ fontSize: 34, color: MUTED, marginBottom: 18 }}>Code, design notes and test evidence</div>
        <div style={{ fontSize: 64, fontWeight: 700 }}>{prop<string>(scene, "url")}</div>
        <div style={{ fontSize: 22, color: MUTED, marginTop: 40 }}>Narration is AI-generated. Everything shown is a test system with made-up data.</div>
      </div>
    </Bg>
  );
};

export const SCENES: Record<string, React.FC<P>> = {
  hook: Hook,
  problem: Problem,
  discovery: Discovery,
  recipe: Recipe,
  approve: Approve,
  answer: Answer,
  handoff: Handoff,
  missing: Missing,
  gate: Gate,
  privacy: Privacy,
  reuse: Reuse,
  close: Close,
  url: Url,
};

export { Token };
