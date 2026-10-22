/** The date a checklist kind's steps count from: "start date", "last day". */
export const dateWord = (kind: string) => ({ onboarding: "start date", offboarding: "last day" })[kind] ?? "date";
