/** May the signed-in person take this action (Settings > Permissions)? Admins always may. */
/** Is this optional part in use (Settings > General), e.g. "action1"? */
export function uses(me: { features?: string[] }, feature: string): boolean {
  return (me.features ?? []).includes(feature);
}

export function can(me: { role: string; permissions?: string[] }, key: string): boolean {
  return me.role === "admin" || (me.permissions ?? []).includes(key);
}
