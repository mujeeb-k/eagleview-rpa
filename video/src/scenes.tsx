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
        fontSize: 32,
        fontWeight: 700,
        color: "#0B0F17",
        background: color,
        padding: "8px 20px",
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
        x={260}
        y={150}
        w={1400}
        h={640}
        who="Amber"
        size={48}
        messages={[
          { from: "member", text: question(scene), at: 0.2, typeFor: 0.9 },
          { from: "typing", at: 1.5, until: 99 },
        ]}
      />
      <Tag x={960} y={850} text="Amber, a member" color={INK} at={0.3} t={t} />
    </Bg>
  );
};

// --- 2. the problem ---------------------------------------------------------------------------

export const Problem: React.FC<P> = ({ scene }) => {
  const t = useT();
  const move = interpolate(t, [0, 0.9], [0, 1], { extrapolateRight: "clamp" });
  const chatX = 260 + (60 - 260) * move;
  const chatW = 1400 + (640 - 1400) * move;
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
        y={150 + 80 * move}
        w={chatW}
        h={640 - 40 * move}
        who="Amber"
        size={48 - 14 * move}
        messages={[
          { from: "member", text: question(scene), at: -1 },
          { from: "typing", at: -1, until: 99 },
        ]}
      />
      <div style={{ position: "absolute", left: 860, top: 200, width: 1020, opacity: fadeIn(t, 0.4, 0.6) }}>
        <div style={{ fontSize: 32, fontWeight: 600, color: INK, marginBottom: 14 }}>Lantern Bay's teller software</div>
        <Img
          src={staticFile(shown)}
          style={{ width: 1020, height: 1020 * (SRC_H / SRC_W), borderRadius: 12, boxShadow: "0 0 0 2px #2A3343, 0 24px 60px rgba(0,0,0,.5)" }}
        />
      </div>
      {/* the gap: the assistant's arrow stops short of the teller software */}
      <svg style={{ position: "absolute", left: 704, top: 500 }} width={150} height={80}>
        <line x1={0} y1={40} x2={70 * arrow} y2={40} stroke={AI} strokeWidth={7} strokeDasharray="14 10" />
        {arrow >= 1 ? (
          <g stroke={ALERT} strokeWidth={8} strokeLinecap="round">
            <line x1={92} y1={20} x2={132} y2={60} />
            <line x1={132} y1={20} x2={92} y2={60} />
          </g>
        ) : null}
      </svg>
      <Tag x={780} y={470} text="No way in" color={ALERT} at={c1 + 0.8} t={t} />
      <Tag x={1370} y={890} text="Clicked by hand" color={PERSON} at={c2 + 0.2} t={t} />
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
      <Tag x={380} y={900} text={prop<string>(scene, "metric")} color={AI} at={0.4} t={useT()} />
    </Bg>
  );
};

export type Step = { id: string; title: string; how: string; x: number; y: number };

const CARD = { x: 700, y: 110, w: 1160 };
const ROW_H = 172;

/** The recipe at full size: its four steps and, for each, how it is found again (30 px, so the
 * tags are readable). `notes` pins a large label to a step (Copperhill's differences). */
export const RecipeCard: React.FC<{
  steps: Step[];
  t: number;
  arrive: (i: number) => number;
  tags: number;
  backup?: number;
  x?: number;
  y?: number;
  title?: string;
  notes?: Record<string, string>;
  noteAt?: (i: number) => number;
  extra?: { text: string; at: number };
  width?: number;
}> = ({ steps, t, arrive, tags, backup, x = CARD.x, y = CARD.y, title = "Recipe: look up share balances", notes = {}, noteAt, extra, width = CARD.w }) => (
  <div
    style={{
      position: "absolute",
      left: x,
      top: y,
      width,
      background: PANEL,
      borderRadius: 24,
      border: `4px solid ${RECIPE}`,
      padding: "30px 36px 32px",
      boxShadow: "0 30px 70px rgba(0,0,0,.5)",
    }}
  >
    <div style={{ fontSize: 36, fontWeight: 700, color: RECIPE, marginBottom: 22 }}>{title}</div>
    {extra ? (
      <div style={{ opacity: fadeIn(t, extra.at), marginBottom: 16, display: "inline-block", fontSize: 32, fontWeight: 700, color: "#3A2E00", background: "#FFE27A", padding: "8px 18px", borderRadius: 10 }}>
        {extra.text}
      </div>
    ) : null}
    {steps.map((s, i) => {
      const o = fadeIn(t, arrive(i) + 0.55, 0.25);
      const note = notes[s.id];
      return (
        <div key={s.id} style={{ height: ROW_H - 22, display: "flex", gap: 22, alignItems: "flex-start", opacity: o, position: "relative" }}>
          <span style={{ flex: "none", width: 58, height: 58, borderRadius: 99, background: RECIPE, color: "#0B0F17", fontWeight: 800, fontSize: 32, display: "grid", placeItems: "center" }}>
            {i + 1}
          </span>
          <div>
            <div style={{ fontSize: 42, fontWeight: 600 }}>{s.title}</div>
            <div style={{ fontSize: 30, color: RECIPE, marginTop: 6, opacity: fadeIn(t, tags + i * 0.3) }}>found by: {s.how}</div>
          </div>
          {note ? (
            <div
              style={{
                position: "absolute",
                left: 840,
                top: 4,
                opacity: fadeIn(t, noteAt ? noteAt(i) : 0),
                transform: "rotate(-2deg)",
                fontSize: 32,
                fontWeight: 700,
                color: "#3A2E00",
                background: "#FFE27A",
                padding: "8px 18px",
                borderRadius: 10,
                boxShadow: "0 10px 24px rgba(0,0,0,.4)",
              }}
            >
              {note}
            </div>
          ) : null}
        </div>
      );
    })}
    {backup !== undefined ? (
      <div style={{ fontSize: 30, color: MUTED, marginTop: 2, opacity: fadeIn(t, backup) }}>position kept only as a backup</div>
    ) : null}
  </div>
);

export const Recipe: React.FC<P> = ({ scene }) => {
  const t = useT();
  const steps = prop<Step[]>(scene, "steps");
  const shot: Frame = { x: 30, y: 330, w: 640 };
  const k = shot.w / SRC_W;
  const arrive = (i: number) => cueAt(scene, 0) + 0.2 + i * 0.75;
  return (
    <Bg>
      <Img
        src={staticFile(prop<string>(scene, "still"))}
        style={{ position: "absolute", left: shot.x, top: shot.y, width: shot.w, borderRadius: 12, opacity: 0.6, boxShadow: `0 0 0 2px ${AI}` }}
      />
      {steps.map((s, i) => {
        const p = interpolate(t, [arrive(i), arrive(i) + 0.7], [0, 1], { extrapolateLeft: "clamp", extrapolateRight: "clamp" });
        if (p <= 0 || p >= 1) return null;
        const x0 = shot.x + s.x * k;
        const y0 = shot.y + s.y * k;
        const x1 = CARD.x + 116;
        const y1 = CARD.y + 100 + i * ROW_H;
        const e = p * p * (3 - 2 * p);
        return (
          <div
            key={s.id}
            style={{
              position: "absolute",
              left: x0 + (x1 - x0) * e,
              top: y0 + (y1 - y0) * e,
              fontSize: 34,
              fontWeight: 700,
              color: "#0B0F17",
              background: e < 0.5 ? AI : RECIPE,
              padding: "6px 16px",
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
      <RecipeCard steps={steps} t={99} arrive={() => -9} tags={-9} x={380} y={110} />
      {t >= at ? (
        <div
          style={{
            position: "absolute",
            left: 1330,
            top: 700,
            transform: `translate(-50%, -50%) rotate(-12deg) scale(${2.2 - 1.2 * s})`,
            opacity: Math.min(1, s * 1.4),
            fontSize: 84,
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
      <Tag x={1330} y={850} text="by a staff member" color={PERSON} at={at + 0.5} t={t} />
    </Bg>
  );
};

// --- 4. answering Amber -----------------------------------------------------------------------

const SIDE: Frame = { x: 840, y: 200, w: 1040 };

const StepChecks: React.FC<{ done: { t: number; step: string }[]; titles: Record<string, string>; scene: Scene; t: number }> = ({
  done,
  titles,
  scene,
  t,
}) => (
  <div style={{ position: "absolute", left: SIDE.x, top: 122, display: "flex", gap: 12 }}>
    {done.map((d) => {
      const at = outTime(d.t, scene.segments);
      return (
        <div
          key={d.step}
          style={{ opacity: fadeIn(t, at, 0.25), fontSize: 28, fontWeight: 600, color: "#0B0F17", background: RECIPE, padding: "6px 14px", borderRadius: 999 }}
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
  const reply = lift + 0.8;
  // The amounts the reply quotes, lifted out of the table it read them from.
  const amounts = answer
    .split("\n")
    .map((l) => (l.match(/\$[\d,]+\.\d\d/) ?? [""])[0])
    .filter((a) => a);
  const k = SIDE.w / SRC_W;
  const zoom = Math.min(SRC_W / (region[2] - region[0] + 80), SRC_H / (region[3] - region[1] + 80), 2.4);
  return (
    <Bg>
      <Chat
        x={40}
        y={110}
        w={760}
        h={930}
        who="Amber"
        size={30}
        messages={[
          { from: "member", text: question(scene), at: -1 },
          { from: "status", text: prop<string>(scene, "status"), at: 0.3, until: reply },
          { from: "assistant", text: answer, at: reply, highlight: prop<string>(scene, "highlight") },
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
      <Tag x={SIDE.x + SIDE.w / 2} y={SIDE.y + SIDE.w * (SRC_H / SRC_W) + 48} text={prop<string>(scene, "metric")} color={RECIPE} at={0.6} t={t} />
      {amounts.map((a, i) => {
        const p = interpolate(t, [lift + i * 0.2, lift + i * 0.2 + 0.8], [0, 1], { extrapolateLeft: "clamp", extrapolateRight: "clamp" });
        if (p <= 0 || p >= 1) return null;
        const x0 = SIDE.x + SIDE.w * 0.62;
        const y0 = SIDE.y + 40 * i * zoom * k + 120;
        const x1 = 120;
        const y1 = 620 + i * 44;
        const e = p * p * (3 - 2 * p);
        return (
          <div key={i} style={{ position: "absolute", left: x0 + (x1 - x0) * e, top: y0 + (y1 - y0) * e, fontFamily: MONO, fontSize: 30, fontWeight: 700, color: "#0B0F17", background: RECIPE, padding: "4px 12px", borderRadius: 8 }}>
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
  const chatAt = Math.max(cueAt(scene, 1) + 0.6, zoomAt + 1.0);
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
            x={60}
            y={440}
            w={1060}
            h={480}
            who="A member"
            size={32}
            messages={[
              { from: "member", text: prop<string>(scene, "question"), at: -1 },
              { from: "assistant", text: prop<string>(scene, "answer"), at: chatAt + 0.4, highlight: prop<string>(scene, "highlight") },
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
                fontSize: 30 / k,
                fontWeight: 700,
                color: "#0B0F17",
                background: PERSON,
                padding: `${5 / k}px ${12 / k}px`,
                borderRadius: 999,
              }}
            >
              <Lock color="#0B0F17" size={30 / k} />
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
  const k = FULL.w / SRC_W;
  const h = FULL.w * (SRC_H / SRC_W);
  const at = cueAt(scene, 0) + 0.5;
  const wipe = interpolate(t, [at, at + 1.3], [0, 100], { extrapolateLeft: "clamp", extrapolateRight: "clamp" });
  const frame: React.CSSProperties = { position: "absolute", left: FULL.x, top: FULL.y, width: FULL.w, height: h, borderRadius: 14 };
  return (
    <Bg>
      <Img src={staticFile(prop<string>(scene, "live"))} style={{ ...frame, boxShadow: "0 0 0 2px #2A3343" }} />
      <Img
        src={staticFile(prop<string>(scene, "saved"))}
        style={{ ...frame, clipPath: `inset(0 ${100 - wipe}% 0 0)`, boxShadow: `0 0 0 3px ${RECIPE}` }}
      />
      {wipe > 0 && wipe < 100 ? (
        <div style={{ position: "absolute", left: FULL.x + (FULL.w * wipe) / 100 - 3, top: FULL.y, width: 6, height: h, background: RECIPE }} />
      ) : null}
      <Tag x={FULL.x + 200} y={FULL.y + h - 60} text="The live screen" color={INK} at={0} until={at + 0.4} t={t} />
      <Tag x={FULL.x + 250} y={FULL.y + h - 60} text="What Eagleview saves" color={RECIPE} at={at + 1.3} t={t} />
      {/* beside the painted-out name and address, which end about x=330 on the screen */}
      <Tag x={FULL.x + (470 + 90) * k} y={FULL.y + 80 * k} text="Blacked out" color={RECIPE} at={at + 1.5} t={t} />
    </Bg>
  );
};

type Diff = { step: string; text: string };

export const Reuse: React.FC<P> = ({ story, scene }) => {
  const t = useT();
  const diffs = prop<Diff[]>(scene, "diffs");
  const steps = prop<Step[]>(scene, "steps");
  const actions = prop<Action[]>(scene, "actions");
  const intro = t < scene.delay;
  const field = actions.find((a) => a.step === "enter_member_id");
  const search = actions.find((a) => a.step === "submit_member_search");
  const enter = pressWindow(scene, actions, field);
  const find = pressWindow(scene, actions, search);
  const labels: Label[] = [];
  if (field && enter) labels.push({ text: "Acct Holder No. box", box: boxOf(field, 55, 11), color: RECIPE, from: enter.o0 - 0.6, to: enter.o1 + 0.8, side: "below" });
  if (search && find) labels.push({ text: "Search button", box: boxOf(search, 32, 12), color: RECIPE, from: find.o0 - 0.6, to: find.o1 });
  const notes: Record<string, string> = {};
  for (const d of diffs) if (d.step) notes[d.step] = d.text;
  const extra = diffs.find((d) => !d.step);
  const noteAt = (i: number) => 0.6 + i * 0.45;
  return (
    <Bg>
      {intro || t < scene.delay + 0.5 ? (
        <div style={{ opacity: intro ? 1 : interpolate(t, [scene.delay, scene.delay + 0.5], [1, 0]) }}>
          <RecipeCard
            steps={steps}
            t={t}
            arrive={() => -9}
            tags={-9}
            x={110}
            y={70}
            width={1700}
            title="The same recipe, at Copperhill"
            notes={notes}
            noteAt={noteAt}
            extra={extra ? { text: extra.text, at: noteAt(steps.length) } : undefined}
          />
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
          <Tag x={360} y={70} text="Copperhill" color={RECIPE} at={scene.delay} t={t} />
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
        <div style={{ fontSize: 28, color: MUTED, marginTop: 40 }}>Narration is AI-generated. Everything shown is a test system with made-up data.</div>
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
