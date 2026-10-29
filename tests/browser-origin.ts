/** The browser suite's Vite origin; a different port lets two checkouts run the suite at once. */
export const browserTestPort =
  process.env.GAIAVFX_BROWSER_TEST_PORT ?? "5180";
export const browserTestOrigin = `http://127.0.0.1:${browserTestPort}`;
