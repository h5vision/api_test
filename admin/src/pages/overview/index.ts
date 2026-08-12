export const OVERVIEW_ROUTE = "/";

export function isOverviewPath(pathname: string): boolean {
  return pathname === OVERVIEW_ROUTE;
}
