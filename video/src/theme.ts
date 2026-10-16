// One colour per actor, used for the cursor, the "driving" token, labels and outlines alike.
export const AI = "#9B8CFF"; // violet: the AI learning the task
export const RECIPE = "#2EC4B6"; // teal: the approved recipe running, with no AI
export const PERSON = "#FF9F43"; // orange: a person (the staff member)
export const NOONE = "#9AA4B2"; // grey: nobody, while the run waits for help
export const ALERT = "#FF5D5D"; // the bank's own warnings
export const BG = "#0B0F17";
export const PANEL = "#151B26";
export const INK = "#E8EDF5";
export const MUTED = "#8A94A6";
export const FONT = "'Avenir Next', Avenir, 'Helvetica Neue', system-ui, sans-serif";
export const MONO = "Menlo, 'SF Mono', monospace";

export type Who = "AI" | "Recipe" | "No one" | "Person";
export const WHO_COLOR: Record<Who, string> = {
  AI,
  Recipe: RECIPE,
  "No one": NOONE,
  Person: PERSON,
};

export const actorColor = (actor: string): string =>
  actor === "llm" ? AI : actor.startsWith("operator") ? PERSON : RECIPE;
