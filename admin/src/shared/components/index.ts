export function svgIcon(path: string, className = "size-5"): string {
  return `
    <svg class="${className}" viewBox="0 0 24 24" fill="none" aria-hidden="true">
      <path d="${path}" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round" />
    </svg>`;
}
