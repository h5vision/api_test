export const apiBaseUrl = (
  import.meta.env.VITE_API_BASE_URL || `http://${window.location.hostname}:8000`
).replace(/\/$/, "");

export const adminApiBaseUrl = (
  import.meta.env.VITE_ADMIN_API_BASE_URL || "/admin-api"
).replace(/\/$/, "");
